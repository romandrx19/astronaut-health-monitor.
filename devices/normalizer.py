"""Contrato de lecturas parciales y validación técnica independiente de Flask/SQLite."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math

from measurements import FIELDS

UNITS = {"heart_rate": "bpm", "spo2": "%", "systolic": "mmHg", "diastolic": "mmHg",
         "glucose": "mg/dL", "temperature": "°C", "sleep_hours": "h"}
QUALITIES = ("unknown", "good", "poor", "invalid")


@dataclass(frozen=True)
class NormalizedReading:
    astronaut_id: int
    metrics: dict
    acquired_at: str
    received_at: str
    source: str
    device_id: str | None
    provenance: str
    observations: str
    fingerprint: str


def normalize_reading(payload, astronaut_id, *, source="manual", device_id=None,
                      provenance="manual", now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(astronaut_id, int) or isinstance(astronaut_id, bool) or astronaut_id <= 0:
        raise ValueError("Identificador de astronauta inválido")
    if not isinstance(payload, dict) or set(payload) - {"metrics", "acquired_at", "observations"}:
        raise ValueError("Usa metrics, acquired_at y observations en una lectura normalizada")
    if source not in ("simulation", "manual", "smartwatch"):
        raise ValueError("Origen de lectura desconocido")
    if provenance not in ("manual", "simulation", "adapter_test", "imported", "verified_device"):
        raise ValueError("Procedencia desconocida")
    if provenance not in {"manual": ("manual", "imported"), "simulation": ("simulation", "adapter_test"),
                          "smartwatch": ("verified_device",)}[source]:
        raise ValueError("La procedencia no corresponde al origen declarado")
    if source == "smartwatch" and (provenance != "verified_device" or not isinstance(device_id, str) or not device_id.strip()):
        raise ValueError("Una lectura SMARTWATCH requiere un adaptador físico verificado e identificado")
    if provenance in ("simulation", "adapter_test") and source != "simulation":
        raise ValueError("Los datos de prueba deben identificarse como simulación")
    if provenance == "imported" and source != "manual":
        raise ValueError("Una importación no equivale a telemetría física verificada")
    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, dict) or not raw_metrics or set(raw_metrics) - set(FIELDS):
        raise ValueError("Incluye al menos uno de los siete indicadores conocidos")
    metrics = {}
    for metric, raw in raw_metrics.items():
        if isinstance(raw, dict):
            if set(raw) - {"value", "unit", "quality"}:
                raise ValueError(f"{metric}: campos de lectura desconocidos")
            value, unit, quality = raw.get("value"), raw.get("unit", UNITS[metric]), raw.get("quality", "unknown")
        else:
            value, unit, quality = raw, UNITS[metric], "unknown"
        minimum, maximum, integer = FIELDS[metric]
        try:
            valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                     and math.isfinite(value) and minimum <= value <= maximum
                     and (not integer or value == int(value)))
        except (ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"{metric}: introduce un {'entero' if integer else 'número'} finito entre {minimum} y {maximum}")
        if unit != UNITS[metric] or quality not in QUALITIES:
            raise ValueError(f"{metric}: unidad o calidad no reconocida")
        metrics[metric] = {"value": int(value) if integer else float(value), "unit": unit, "quality": quality}
    if "systolic" in metrics and "diastolic" in metrics and metrics["systolic"]["value"] <= metrics["diastolic"]["value"]:
        raise ValueError("La presión sistólica debe superar a la diastólica; revisa la captura")
    observations = payload.get("observations", "")
    if not isinstance(observations, str) or len(observations) > 4000:
        raise ValueError("Las observaciones deben ser texto de hasta 4000 caracteres")
    raw_time = payload.get("acquired_at")
    if raw_time is None:
        acquired = now
    else:
        if not isinstance(raw_time, str):
            raise ValueError("acquired_at debe ser una fecha ISO 8601 con zona horaria")
        try:
            acquired = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if acquired.tzinfo is None:
                raise ValueError()
            acquired = acquired.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("acquired_at debe ser una fecha ISO 8601 con zona horaria") from None
        if acquired > now:
            raise ValueError("La fecha de adquisición no puede estar en el futuro")
    identity = {"astronaut_id": astronaut_id, "metrics": metrics, "source": source,
                "device_id": device_id, "provenance": provenance, "observations": observations,
                "acquired_at": acquired.isoformat() if raw_time is not None else None}
    fingerprint = sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return NormalizedReading(astronaut_id, metrics, acquired.isoformat(timespec="microseconds"),
                             now.isoformat(timespec="microseconds"), source, device_id,
                             provenance, observations, fingerprint)


def normalize_manual(payload, astronaut_id, *, now=None):
    if not isinstance(payload, dict) or set(payload) - set(FIELDS) - {"acquired_at", "observations"}:
        raise ValueError("Envía indicadores, acquired_at y observations; no se admite etiquetar un dispositivo")
    return normalize_reading({"metrics": {key: payload[key] for key in FIELDS if key in payload},
                              **{key: payload[key] for key in ("acquired_at", "observations") if key in payload}},
                             astronaut_id, now=now)
