"""Validación de expedientes y referencias; persistencia mediante contrato."""
from datetime import datetime, timezone
from math import isfinite
from persistence.provider import get_repository

METRICS = ("heart_rate", "spo2", "systolic", "diastolic", "glucose", "temperature", "sleep_hours")
MAX_NOTES_LENGTH = 20000


def init_medical_db(repository=None):
    get_repository(repository).initialize("medical")


def medical_profile(astronaut_id, *, repository=None):
    return get_repository(repository).get_medical_profile(astronaut_id)


def _finite_number(value, metric):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{metric}: las referencias deben ser números finitos")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{metric}: las referencias deben ser números finitos") from None
    if not isfinite(number):
        raise ValueError(f"{metric}: las referencias deben ser números finitos")
    return number


def _validate_profile(data):
    if not isinstance(data, dict):
        raise ValueError("Se requiere un objeto JSON válido")
    if set(data) - {"notes", "references"}:
        raise ValueError("Campos desconocidos: usa únicamente notes y references")
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError("notes debe ser texto")
    if len(notes) > MAX_NOTES_LENGTH:
        raise ValueError(f"Las observaciones admiten hasta {MAX_NOTES_LENGTH} caracteres")
    references = data.get("references", {})
    if not isinstance(references, dict):
        raise ValueError("references debe ser un objeto de indicadores")
    if set(references) - set(METRICS):
        raise ValueError("references contiene indicadores desconocidos")

    validated = {}
    for metric, bounds in references.items():
        if bounds is None:
            continue
        if not isinstance(bounds, dict) or set(bounds) - {"min_value", "max_value"}:
            raise ValueError(f"{metric}: usa min_value y max_value")
        low, high = bounds.get("min_value"), bounds.get("max_value")
        low_empty, high_empty = low in (None, ""), high in (None, "")
        if low_empty and high_empty:
            continue
        if low_empty or high_empty:
            raise ValueError(f"{metric}: completa tanto el mínimo como el máximo")
        low, high = _finite_number(low, metric), _finite_number(high, metric)
        if low >= high:
            raise ValueError(f"{metric}: el mínimo debe ser menor que el máximo")
        validated[metric] = (low, high)
    return notes, validated


def save_medical_profile(astronaut_id, data, *, repository=None):
    """Valida el documento completo antes de una sustitución atómica."""
    notes, references = _validate_profile(data)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    bounds = {metric: {"min_value": low, "max_value": high} for metric, (low, high) in references.items()}
    return get_repository(repository).save_medical_profile(astronaut_id, notes, bounds, updated_at)
