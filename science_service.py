"""Referencias y reglas consultadas por el backend desde el repositorio configurado."""
from copy import deepcopy

from flask import current_app, has_app_context

from persistence.provider import get_repository
from scientific_rules import educational_tuples, validate_rule_definitions


def rule_definitions(repository=None):
    return validate_rule_definitions(get_repository(repository).get_rule_definitions())


def educational_rules(repository=None):
    return educational_tuples(rule_definitions(repository))


def individual_references(astronaut_id, repository=None):
    return get_repository(repository).get_medical_profile(astronaut_id)["references"]


def analysis_settings():
    return deepcopy(current_app.config.get("ARES_ANALYSIS_RULES", {})) if has_app_context() else {}


def safety_protocols(repository=None):
    # La configuración anterior conserva su prioridad y sus condiciones de activación.
    protocols = deepcopy(current_app.config.get("ARES_SAFETY_PROTOCOLS", [])) if has_app_context() else []
    for rule in rule_definitions(repository):
        if rule["scope"] != "safety_protocol" or not rule["enabled"] or rule["validation_status"] != "validated":
            continue
        if not rule.get("approved_by") or not rule.get("validation_reference"):
            continue
        protocols.append({"id": rule["id"], "title": rule["message"], "metric": rule["metric"],
                          "operator": rule["condition"]["operator"], "threshold": rule["condition"]["threshold"],
                          "enabled": True, "validated": True, "approved_by": rule["approved_by"],
                          "validation_reference": rule["validation_reference"], "version": rule["version"],
                          "source": rule["source"], "validation_status": rule["validation_status"], "guidance": rule["guidance"]})
    return protocols


def oct_review_rules(repository=None):
    rules = deepcopy(current_app.config.get("ARES_OCT_REVIEW_RULES", [])) if has_app_context() else []
    for rule in rule_definitions(repository):
        if rule["scope"] != "oct_review" or not rule["enabled"] or rule["validation_status"] != "validated":
            continue
        if not rule.get("approved_by") or not rule.get("validation_reference"):
            continue
        rules.append({"metric": rule["metric"], "unit": rule["unit"], **rule["context"],
                      "min_absolute_change": rule["condition"]["threshold"], "reason": rule["message"],
                      "validation_reference": rule["validation_reference"]})
    return rules


def catalog(repository=None):
    """Metadatos serializables. La validación declarada procede del proveedor."""
    return {"rules": rule_definitions(repository), "analysis_settings": analysis_settings(),
            "configured_protocols": safety_protocols(repository), "oct_review_rules": oct_review_rules(repository)}
