"""Validación técnica de entradas manuales; no define rangos de salud."""
import math
from datetime import datetime, timezone

# Límites amplios para detectar errores de captura, no umbrales clínicos.
FIELDS = {
    "heart_rate": (1, 300, True), "spo2": (0, 100, True),
    "systolic": (1, 350, True), "diastolic": (1, 250, True),
    "glucose": (1, 1000, True), "temperature": (25, 45, False),
    "sleep_hours": (0, 24, False),
}


def validate_manual(payload, astronaut_id):
    if not isinstance(payload, dict):
        raise ValueError("Se requiere un objeto JSON con los siete indicadores")
    if set(payload) - set(FIELDS):
        raise ValueError("Campos desconocidos: envía únicamente los siete indicadores")
    sample = {"astronaut_id": astronaut_id, "source": "manual",
              "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
    for field, (minimum, maximum, integer) in FIELDS.items():
        value = payload.get(field)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not minimum <= value <= maximum
                or (integer and value != int(value))):
            kind = "entero" if integer else "número"
            raise ValueError(f"{field}: se requiere un {kind} entre {minimum} y {maximum}")
        sample[field] = int(value) if integer else round(value, 1)
    if sample["systolic"] <= sample["diastolic"]:
        raise ValueError("La presión sistólica debe superar a la diastólica; revisa la captura")
    return sample
