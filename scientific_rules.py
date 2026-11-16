"""Registros declarativos de reglas y evaluación numérica acotada, sin ejecutar código recibido."""
from copy import deepcopy
import json
from math import isfinite
from pathlib import Path

from devices.normalizer import UNITS

DEMO_RULES_PATH = Path(__file__).resolve().parent / "config" / "demo_rules.json"


def validate_rule_definitions(rules):
    if not isinstance(rules, list):
        raise ValueError("El catálogo de reglas debe ser una lista")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Cada regla debe ser un registro con fuente y versión")
        for field in ("id", "metric", "unit", "version", "message"):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise ValueError(f"La regla necesita {field}")
        if rule["id"] in seen:
            raise ValueError("El catálogo contiene identificadores de regla duplicados")
        seen.add(rule["id"])
        if rule.get("scope") not in ("educational", "safety_protocol", "oct_review"):
            raise ValueError("Ámbito de regla desconocido")
        if rule.get("validation_status") not in ("demonstration", "unvalidated", "validated"):
            raise ValueError("La regla necesita un estado de validación declarado")
        source = rule.get("source")
        if not isinstance(source, dict) or any(not isinstance(source.get(field), str) for field in ("title", "reference")):
            raise ValueError("La fuente necesita title y reference, con url opcional")
        if not source["title"].strip():
            raise ValueError("La fuente necesita un título no vacío")
        if source.get("url") is not None and not isinstance(source["url"], str):
            raise ValueError("source.url debe ser texto o null")
        if rule["validation_status"] == "validated" and not (source["reference"].strip() or (source.get("url") or "").strip()):
            raise ValueError("Una regla declarada validada debe identificar su fuente")
        for field in ("approved_by", "validation_reference"):
            if field in rule and (not isinstance(rule[field], str) or not rule[field].strip()):
                raise ValueError(f"{field} debe ser texto no vacío cuando se declara")
        if not isinstance(rule.get("enabled"), bool) or not isinstance(rule.get("guidance"), list) or any(not isinstance(item, str) for item in rule["guidance"]):
            raise ValueError("La regla necesita enabled booleano y guidance como lista de textos")
        condition = rule.get("condition")
        if not isinstance(condition, dict) or condition.get("operator") not in ("above", "below", "outside", "absolute_change_at_least"):
            raise ValueError("Condición declarativa desconocida")
        fields = ("min_value", "max_value") if condition["operator"] == "outside" else ("threshold",)
        for field in fields:
            value = condition.get(field)
            try:
                valid = not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"La condición necesita {field} numérico y finito")
        if condition["operator"] == "outside" and condition["min_value"] >= condition["max_value"]:
            raise ValueError("La condición outside necesita min_value menor que max_value")
        if rule["scope"] in ("educational", "safety_protocol"):
            if rule["metric"] not in UNITS or rule["unit"] != UNITS[rule["metric"]]:
                raise ValueError("La regla de telemetría debe usar un indicador y unidad conocidos")
            operators = ("above", "below", "outside") if rule["scope"] == "educational" else ("above", "below")
            if condition["operator"] not in operators:
                raise ValueError("El operador no corresponde al ámbito de la regla")
        else:
            context = rule.get("context")
            if condition["operator"] != "absolute_change_at_least" or condition["threshold"] <= 0:
                raise ValueError("La regla OCT necesita una magnitud de cambio positiva")
            if not isinstance(context, dict) or any(not isinstance(context.get(key), str) or not context[key].strip()
                for key in ("anatomical_region", "measurement_method", "device_model", "scan_protocol")):
                raise ValueError("La regla OCT necesita región, método, equipo y protocolo")
    return deepcopy(rules)


def load_rule_definitions(path=None):
    source = Path(path) if path is not None else DEMO_RULES_PATH
    with source.open(encoding="utf-8") as stream:
        return validate_rule_definitions(json.load(stream))


def condition_matches(condition, value):
    operator = condition["operator"]
    if operator == "outside":
        return value < condition["min_value"] or value > condition["max_value"]
    if operator == "above":
        return value > condition["threshold"]
    if operator == "below":
        return value < condition["threshold"]
    return abs(value) >= condition["threshold"]


def educational_tuples(definitions):
    return [(rule["metric"], lambda value, condition=rule["condition"]: condition_matches(condition, value),
             rule["message"], " ".join(rule["guidance"]))
            for rule in definitions if rule["scope"] == "educational" and rule["enabled"]]
