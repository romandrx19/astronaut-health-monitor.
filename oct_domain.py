"""Funciones oculares puras compartidas por persistencia y comparación."""
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def same_acquisition(left, right):
    return all(documented(left[field]) and documented(right[field]) and left[field].casefold() == right[field].casefold()
               for field in ("eye", "device_model", "scan_protocol"))


def documented(value):
    missing = {"", "unknown", "desconocido", "desconocida", "no informado", "no informada",
               "no disponible", "sin información", "sin informacion", "n/a", "-", "—", "?"}
    return isinstance(value, str) and value.strip().casefold() not in missing


def ocular_summary(exams):
    latest = exams[0] if exams else None
    reviewed = sorted((exam for exam in exams if exam["reviewed_at"]), key=lambda exam: exam["reviewed_at"], reverse=True)
    return {"exam_count": len(exams), "last_exam_date": latest["exam_date"] if latest else None,
            "baselines": [{"id": exam["id"], "eye": exam["eye"], "device_model": exam["device_model"],
                           "scan_protocol": exam["scan_protocol"], "exam_date": exam["exam_date"]}
                          for exam in exams if exam["is_baseline"]],
            "last_review": {key: reviewed[0][key] for key in ("reviewed_at", "reviewed_by", "review_notes")} if reviewed else None,
            "latest_notes": latest["notes"] if latest else "", "is_demo": True}
