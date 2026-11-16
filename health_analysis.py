"""Comparación determinista y tendencias de demostración; no diagnostica enfermedades."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite

from alerts import RULES

METRICS = {
    "heart_rate": ("Frecuencia cardíaca", "bpm"),
    "spo2": ("Saturación de oxígeno", "%"),
    "systolic": ("Presión sistólica", "mmHg"),
    "diastolic": ("Presión diastólica", "mmHg"),
    "glucose": ("Glucosa sanguínea", "mg/dL"),
    "temperature": ("Temperatura corporal", "°C"),
    "sleep_hours": ("Horas de sueño", "h"),
}
STATES = {
    "normal": "Normal según referencia", "no_reference": "Sin referencia personalizada",
    "variation": "Variación detectada", "review": "Requiere revisión",
    "priority": "Atención prioritaria", "insufficient": "Datos insuficientes",
    "stale": "Lectura desactualizada", "educational": "Regla educativa del simulador",
}
SOURCES = {
    "individual_reference": "Referencia médica individual",
    "trend_analysis": "Análisis de tendencia",
    "simulation_rule": "Regla educativa del simulador",
    "configured_protocol": "Protocolo configurado",
}
EMERGENCY_NOTE = "Si existen síntomas graves, activa el procedimiento de emergencia establecido para la misión."


@dataclass(frozen=True)
class AnalysisRules:
    """Parámetros técnicos de demostración, NO umbrales clínicos ni de emergencia."""
    history_limit: int = 36
    trend_samples: int = 4
    repeated_samples: int = 3
    max_age_seconds: float = 300
    window_seconds: float = 1800
    max_gap_seconds: float = 360
    min_interval_seconds: float = 1
    min_span_seconds: float = 10
    jump_range_fraction: float = 0.5

    def __post_init__(self):
        for key, value in asdict(self).items():
            if number(value) is None or value <= 0:
                raise ValueError(f"Regla de análisis inválida: {key}")
        for key in ("history_limit", "trend_samples", "repeated_samples"):
            value = getattr(self, key)
            if not isinstance(value, int) or not 2 <= value <= 200:
                raise ValueError(f"{key} debe ser entero entre 2 y 200")
        if self.history_limit < max(self.trend_samples, self.repeated_samples):
            raise ValueError("history_limit debe cubrir las muestras requeridas")
        if self.min_interval_seconds > self.max_gap_seconds or self.min_span_seconds > self.window_seconds:
            raise ValueError("Intervalos de análisis incompatibles")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def reading_value(row, metric):
    """Una calidad mala no equivale a un valor ausente ni a una referencia normal."""
    return None if row.get("quality", {}).get(metric) in ("poor", "invalid") else number(row.get(metric))


def reference_pair(reference):
    if not isinstance(reference, dict):
        return None
    low, high = number(reference.get("min_value")), number(reference.get("max_value"))
    return {"min_value": low, "max_value": high} if low is not None and high is not None and low < high else None


def build_guidance(metric=None, state="variation"):
    return [
        "Verifica la captura y, si procede de un sensor, su funcionamiento y colocación según el fabricante.",
        "Comprueba si la lectura corresponde a ejercicio o reposo; registra síntomas y condiciones de la medición.",
        "Repite la medición cuando lo permita el protocolo del dispositivo.",
        "Consulta el protocolo médico de la misión y solicita asistencia cuando la situación o el protocolo lo requieran.",
        EMERGENCY_NOTE,
    ]


def evaluate_metric(metric, value, reference=None, captured_at=None, *, now=None, rules=None):
    rules = rules or AnalysisRules()
    now = now or datetime.now(timezone.utc)
    value, reference = number(value), reference_pair(reference)
    moment = timestamp(captured_at)
    age = (now - moment).total_seconds() if moment else None
    comparison, difference, limit = "unavailable", None, None
    if value is not None and reference:
        low, high = reference["min_value"], reference["max_value"]
        comparison = "below" if value < low else "above" if value > high else "within"
        limit = low if comparison == "below" else high if comparison == "above" else None
        delta = abs(value - limit) if limit is not None else 0.0
        difference = round(delta, 10) if isfinite(delta) else None
    elif value is not None:
        comparison = "no_reference"

    if value is None or age is None or age < 0:
        state, description = "insufficient", "La lectura, su fecha o su valor no permiten una evaluación actual."
    elif age > rules.max_age_seconds:
        state, description = "stale", "La lectura supera la antigüedad de demostración configurada; verifica una lectura actual."
    elif reference is None:
        state, description = "no_reference", "No hay una pareja válida de referencias individuales para este indicador."
    elif comparison == "within":
        state, description = "normal", "La medición está dentro de la referencia configurada, incluidos sus extremos. Esto no es un diagnóstico."
    else:
        state = "variation"
        side = "por encima del máximo" if comparison == "above" else "por debajo del mínimo"
        amount = f"{difference:g} {METRICS[metric][1]} " if difference is not None else ""
        description = f"La medición está {amount}{side} configurado. Puede necesitar verificación; no confirma una enfermedad ni una emergencia."
    return {
        "metric": metric, "label": METRICS[metric][0], "unit": METRICS[metric][1],
        "current_value": value, "reference": reference, "comparison": comparison,
        "difference": difference, "limit": limit, "state": state, "state_label": STATES[state],
        "captured_at": captured_at if moment else None, "age_seconds": age,
        "description": description, "guidance": build_guidance(metric, state),
    }


def analyze_trends(history, references, astronaut_id, *, latest=None, now=None, rules=None):
    rules, now = rules or AnalysisRules(), now or datetime.now(timezone.utc)
    own = [row for row in history if row.get("astronaut_id") == astronaut_id]
    ordered = sorted(own, key=lambda row: (timestamp(row.get("captured_at")) or datetime.min.replace(tzinfo=timezone.utc), str(row.get("id", ""))))
    latest = latest if latest is not None else (ordered[-1] if ordered else None)
    end = timestamp(latest.get("captured_at")) if latest else None
    output = {}
    for metric in METRICS:
        result = {"state": "insufficient", "label": STATES["insufficient"], "sample_count": 0,
                  "span_seconds": 0, "findings": []}
        output[metric] = result
        if not latest or not end or (now - end).total_seconds() < 0:
            continue
        if (now - end).total_seconds() > rules.max_age_seconds:
            result.update(state="stale", label=STATES["stale"])
            continue
        # Solo el tramo final válido, del mismo origen; no se salvan huecos ni datos inválidos.
        tail = []
        for row in reversed(ordered):
            at, value = timestamp(row.get("captured_at")), reading_value(row, metric)
            if not at or at > end:
                continue
            if row.get("source") != latest.get("source"):
                continue
            if value is None or (end - at).total_seconds() > rules.window_seconds:
                break
            if tail:
                gap = (tail[-1][0] - at).total_seconds()
                if not rules.min_interval_seconds <= gap <= rules.max_gap_seconds:
                    break
            tail.append((at, value))
            if len(tail) >= max(rules.trend_samples, rules.repeated_samples):
                break
        tail.reverse()
        result["sample_count"] = len(tail)
        result["span_seconds"] = (tail[-1][0] - tail[0][0]).total_seconds() if tail else 0
        if len(tail) < 2 or result["span_seconds"] < rules.min_span_seconds:
            continue
        values = [value for _, value in tail]
        enough = len(values) >= rules.trend_samples
        repeated_enough = len(values) >= rules.repeated_samples
        reference = reference_pair(references.get(metric))
        findings = result["findings"]
        if enough:
            window = values[-rules.trend_samples:]
            if all(a < b for a, b in zip(window, window[1:])):
                findings.append({"type": "increasing", "description": f"Incremento progresivo en {len(window)} muestras consecutivas."})
            elif all(a > b for a, b in zip(window, window[1:])):
                findings.append({"type": "decreasing", "description": f"Disminución progresiva en {len(window)} muestras consecutivas."})
        if reference:
            width = reference["max_value"] - reference["min_value"]
            delta = abs(values[-1] - values[-2])
            if isfinite(width) and isfinite(delta) and delta >= width * rules.jump_range_fraction:
                findings.append({"type": "sudden_change", "description": "Cambio entre las dos últimas lecturas según la regla de demostración configurada."})
            if repeated_enough and all(v < reference["min_value"] or v > reference["max_value"] for v in values[-rules.repeated_samples:]):
                findings.append({"type": "repeated_outside", "description": f"{rules.repeated_samples} lecturas consecutivas fuera de la referencia; requiere comprobar la captura según la regla de demostración."})
        if enough or findings:
            result.update(state="available", label="Tendencia de demostración disponible")
        # Un patrón temporal no confirma una causa: el contexto de actividad no está registrado.
        if findings:
            result["context_note"] = "El ejercicio, el reposo y la calidad del sensor pueden influir; este patrón no confirma una anomalía clínica."
    return output


def active_protocols(protocols):
    """Solo configuración local explícita con declaración y referencia de validación."""
    return [p for p in protocols if isinstance(p, dict) and p.get("enabled") is True
            and p.get("validated") is True and p.get("metric") in METRICS
            and p.get("operator") in ("above", "below") and number(p.get("threshold")) is not None
            and all(isinstance(p.get(k), str) and p[k].strip()
                    for k in ("id", "title", "approved_by", "validation_reference"))]


def evaluate_astronaut(astronaut_id, latest, history, references, *, now=None, rules=None, protocols=(), educational_rules=None):
    rules, now = rules or AnalysisRules(), now or datetime.now(timezone.utc)
    if latest and latest.get("astronaut_id") != astronaut_id:
        raise ValueError("La medición no corresponde al astronauta")
    latest = latest or {}
    trends = analyze_trends(history, references, astronaut_id, latest=latest, now=now, rules=rules)
    configured = active_protocols(protocols)
    metrics, events = {}, []
    for metric in METRICS:
        item = evaluate_metric(metric, reading_value(latest, metric), references.get(metric), latest.get("captured_at"), now=now, rules=rules)
        item["quality"] = latest.get("quality", {}).get(metric, "unknown")
        if item["quality"] in ("poor", "invalid"):
            item["description"] = "Lectura de baja calidad o inválida; verifica la captura. No se usa para evaluar referencias ni activar protocolos."
        item["trend"] = trends[metric]
        metrics[metric] = item
        if item["state"] in ("stale", "insufficient"):
            continue
        findings = trends[metric]["findings"]
        outside = item["comparison"] in ("above", "below")
        event_type = f"{item['comparison']}_reference" if outside else "trend_variation"
        source = "individual_reference" if outside else "trend_analysis"
        sources = (["individual_reference"] if outside else []) + (["trend_analysis"] if findings else [])
        if findings:
            item["state"] = "review" if any(f["type"] == "repeated_outside" for f in findings) else "variation"
        protocol = next((p for p in configured if p["metric"] == metric and
                         (item["current_value"] > p["threshold"] if p["operator"] == "above" else item["current_value"] < p["threshold"])), None)
        if protocol:
            item["state"] = "priority"
            item["protocol"] = {key: protocol[key] for key in ("id", "title", "approved_by", "validation_reference", "operator", "threshold")}
            item["protocol"].update({key: protocol[key] for key in ("version", "source", "validation_status") if key in protocol})
            if protocol.get("guidance"):
                item["guidance"] = [*protocol["guidance"], *item["guidance"]]
            source, event_type = "configured_protocol", f"protocol:{protocol['id']}"
            sources.append(source)
        item["state_label"] = STATES[item["state"]]
        if not outside and not findings and not protocol:
            continue
        description = item["description"]
        if findings:
            description += " " + " ".join(f["description"] for f in findings) + " " + trends[metric]["context_note"]
        if protocol:
            description += f" Se activa el protocolo explícitamente configurado: {protocol['title']}. Consulta ese protocolo."
        events.append({**item, "event_type": event_type, "severity": item["state"], "source": source,
                       "sources": sources, "description": description})

    # Las reglas originales se conservan como una fuente educativa separada del expediente.
    if latest.get("source") in ("simulated", "simulation", "manual"):
        for metric, predicate, message, recommendation in (RULES if educational_rules is None else educational_rules):
            item = metrics[metric]
            if item["state"] in ("stale", "insufficient") or item["current_value"] is None:
                continue
            if predicate(item["current_value"]):
                events.append({**item, "reference": None, "comparison": None, "difference": None,
                               "event_type": "educational_rule", "severity": "educational",
                               "state_label": STATES["educational"], "source": "simulation_rule",
                               "sources": ["simulation_rule"], "description": message + ". Regla educativa independiente del simulador aplicada a una lectura "
                               + ("manual" if latest.get("source") == "manual" else "simulada") + "; no utiliza la referencia personal ni es un criterio clínico validado.",
                               "guidance": [recommendation, *build_guidance(metric)]})
    states = [item["state"] for item in metrics.values()]
    overall = next((state for state in ("priority", "review", "variation", "stale", "insufficient", "no_reference") if state in states), "normal")
    missing = [metric for metric, item in metrics.items() if not item["reference"]]
    comparable = [item for item in metrics.values() if item["reference"] and item["state"] not in ("stale", "insufficient")]
    if overall not in ("priority", "review", "variation") and comparable:
        message = "No se detectaron variaciones respecto a las referencias individuales disponibles."
    else:
        message = STATES[overall] + ". Consulta el detalle y la cobertura de referencias."
    return {"astronaut_id": astronaut_id, "measurement_id": latest.get("id"), "captured_at": latest.get("captured_at"),
            "measurement_source": latest.get("source"), "evaluated_at": now.isoformat(), "state": overall,
            "state_label": STATES[overall], "message": message, "missing_references": missing,
            "metrics": metrics, "events": events, "active_protocol_count": len(configured),
            "rules": asdict(rules), "emergency_note": EMERGENCY_NOTE}
