"""Comparación descriptiva de mediciones documentadas, sin umbrales clínicos predeterminados."""
from math import isfinite

from oct_domain import documented, same_acquisition

METRIC_LABELS = {"rnfl": "Grosor RNFL", "trt": "Grosor retiniano total", "macular": "Grosor macular", "choroidal": "Grosor coroideo"}
REVIEW_GUIDANCE = ("Se identificó una variación respecto al examen basal. Se recomienda revisar la calidad y "
                   "comparabilidad de las imágenes y solicitar interpretación al personal médico de la misión")


def measurement_key(item):
    return (item["metric"].casefold(), item["unit"], item["anatomical_region"].casefold(), item["measurement_method"].casefold())


def acquisition_mismatch(baseline, current):
    reasons = []
    if baseline["astronaut_id"] != current["astronaut_id"]:
        reasons.append("Astronautas diferentes")
    for field, label in (("eye", "Ojos"), ("device_model", "Equipos"), ("scan_protocol", "Protocolos")):
        if not documented(baseline[field]) or not documented(current[field]):
            reasons.append(f"{label}: información pendiente de documentar")
        elif baseline[field].casefold() != current[field].casefold():
            reasons.append(f"{label} diferentes")
    if baseline["exam_date"] > current["exam_date"]:
        reasons.append("El examen basal es posterior al examen seleccionado")
    if baseline["image_quality"] in ("poor", "ungradable") or current["image_quality"] in ("poor", "ungradable"):
        reasons.append("Calidad insuficiente declarada en uno de los exámenes")
    return reasons


def validate_review_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("ARES_OCT_REVIEW_RULES debe ser una lista")
    fields = {"metric", "unit", "anatomical_region", "measurement_method", "device_model", "scan_protocol",
              "min_absolute_change", "reason", "validation_reference"}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != fields:
            raise ValueError("Una regla OCT necesita criterio, contexto completo, razón y referencia de validación")
        if any(not isinstance(rule[key], str) or not rule[key].strip() for key in fields - {"min_absolute_change"}):
            raise ValueError("El contexto y la fuente de una regla OCT deben ser texto no vacío")
        limit = rule["min_absolute_change"]
        if isinstance(limit, bool) or not isinstance(limit, (float, int)) or not isfinite(limit) or limit <= 0:
            raise ValueError("min_absolute_change debe ser un número positivo finito en la unidad declarada")
    return rules


def compare_exams(exams, current_id=None, baseline_id=None, start=None, end=None, rules=None):
    rules = validate_review_rules([] if rules is None else rules)
    current = next((exam for exam in exams if exam["id"] == current_id), None) if current_id else (exams[0] if exams else None)
    if current_id and current is None:
        raise LookupError("Examen OCT no encontrado para este astronauta")
    result = {"current_id": current["id"] if current else None, "baseline_id": None,
              "message": "No existe un examen basal comparable", "comparisons": [], "incompatible": [],
              "notices": [], "is_demo": True}
    if current is None:
        result["notices"].append({"kind": "missing_baseline", "reason": "Registra un examen y define explícitamente su referencia basal"})
        return result
    if current["review_status"] != "reviewed":
        result["notices"].append({"kind": "pending_review", "exam_id": current["id"], "exam_date": current["exam_date"],
                                  "quality": current["image_quality"], "reason": "Examen pendiente de revisión profesional registrada"})
    if not current["files"]:
        result["notices"].append({"kind": "missing_image", "reason": "El examen contiene datos registrados. Añade la imagen exportada para consultar su estructura"})
    if baseline_id:
        baseline = next((exam for exam in exams if exam["id"] == baseline_id), None)
        if baseline is None:
            raise LookupError("Referencia basal no encontrada para este astronauta")
        if not baseline["is_baseline"]:
            raise ValueError("Marca explícitamente el examen como referencia basal antes de compararlo")
    else:
        baseline = next((exam for exam in exams if exam["is_baseline"] and same_acquisition(exam, current)), None)
    if baseline is None:
        result["notices"].append({"kind": "missing_baseline", "reason": result["message"]})
        return result
    mismatch = acquisition_mismatch(baseline, current)
    if mismatch:
        result["incompatible"] = mismatch
        result["notices"].append({"kind": "incompatible", "reason": ". ".join(mismatch), "exam_date": current["exam_date"], "quality": current["image_quality"]})
        return result
    context_fields = ("metric", "unit", "anatomical_region", "measurement_method")
    basal_metrics = {measurement_key(item): item for item in baseline["measurements"]
                     if all(documented(item[field]) for field in context_fields)}
    for item in current["measurements"]:
        if not all(documented(item[field]) for field in context_fields):
            result["incompatible"].append(f'{METRIC_LABELS.get(item["metric"], item["metric"])}: unidad, región o método pendiente de documentar')
            continue
        key = measurement_key(item)
        previous = basal_metrics.get(key)
        if previous is None:
            result["incompatible"].append(f'{METRIC_LABELS.get(item["metric"], item["metric"])}: falta la misma variable, unidad, región o método en el basal')
            continue
        difference = item["value"] - previous["value"]
        if not isfinite(difference):
            result["incompatible"].append(f'{item["metric"]}: diferencia fuera de la precisión numérica admitida')
            continue
        percent = difference / previous["value"] * 100 if previous["value"] > 0 else None
        if percent is not None and not isfinite(percent):
            percent = None
        points = []
        for exam in sorted(exams, key=lambda entry: (entry["exam_date"], entry["id"])):
            if acquisition_mismatch(baseline, exam) or exam["exam_date"] > current["exam_date"]:
                continue
            if (start and exam["exam_date"][:10] < start) or (end and exam["exam_date"][:10] > end):
                continue
            matching = next((value for value in exam["measurements"] if measurement_key(value) == key), None)
            if matching:
                points.append({"exam_id": exam["id"], "exam_date": exam["exam_date"], "value": matching["value"],
                               "mission_phase": exam["mission_phase"], "quality": exam["image_quality"], "provenance": exam["provenance"]})
        comparison = {"metric": item["metric"], "label": METRIC_LABELS.get(item["metric"], item["metric"]),
                      "unit": item["unit"], "anatomical_region": item["anatomical_region"], "measurement_method": item["measurement_method"],
                      "baseline_value": previous["value"], "current_value": item["value"], "difference": difference,
                      "absolute_difference": abs(difference), "percent_difference": percent,
                      "baseline_date": baseline["exam_date"], "current_date": current["exam_date"],
                      "baseline_quality": baseline["image_quality"], "current_quality": current["image_quality"],
                      "baseline_provenance": baseline["provenance"], "current_provenance": current["provenance"], "points": points}
        result["comparisons"].append(comparison)
        for rule in rules:
            context = {**current, **item}
            matches = context["unit"] == rule["unit"] and all(context[field].casefold() == rule[field].casefold() for field in
                          ("metric", "anatomical_region", "measurement_method", "device_model", "scan_protocol"))
            if matches and abs(difference) >= rule["min_absolute_change"] and baseline["id"] != current["id"]:
                result["notices"].append({"kind": "configured_review", "metric": item["metric"],
                    "previous_value": previous["value"], "current_value": item["value"], "difference": difference,
                    "unit": item["unit"], "exam_date": current["exam_date"], "quality": current["image_quality"],
                    "reason": rule["reason"], "validation_reference": rule["validation_reference"], "guidance": REVIEW_GUIDANCE})
    if result["comparisons"]:
        result["baseline_id"] = baseline["id"]
        result["message"] = "Comparación descriptiva con el basal seleccionado" if baseline["id"] != current["id"] else "Este examen es la referencia basal"
    else:
        result["notices"].append({"kind": "missing_baseline", "reason": result["message"]})
    if result["incompatible"]:
        result["notices"].append({"kind": "incompatible", "reason": ". ".join(result["incompatible"]), "exam_date": current["exam_date"], "quality": current["image_quality"]})
    return result
