"""Reglas inventadas para una demostración educativa. NO son umbrales clínicos."""
from math import isfinite

# La constante conserva los consumidores Python de la evaluación pura.
from scientific_rules import educational_tuples, load_rule_definitions
RULES = educational_tuples(load_rule_definitions())


def evaluate(sample, *, repository=None):
    from science_service import educational_rules
    rules = educational_rules(repository)
    alerts = []
    for field, predicate, message, recommendation in rules:
        value = sample.get(field)
        try:
            valid = (not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
                     and sample.get("quality", {}).get(field) not in ("poor", "invalid"))
        except OverflowError:
            valid = False
        if valid and predicate(value):
            alerts.append({
                "field": field,
                "message": message,
                "recommendation": recommendation,
                "value": sample[field],
                "severity": "warning",
            })
    return alerts
