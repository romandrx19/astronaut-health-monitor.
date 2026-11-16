"""API de solo lectura para investigación histórica."""
from flask import Blueprint, jsonify, request

from research_service import connection, parse_filters

research_api = Blueprint("research", __name__, url_prefix="/api/research")


@research_api.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@research_api.errorhandler(LookupError)
def missing(error):
    return jsonify(error=str(error)), 404


def query(operation, *args):
    filters = parse_filters(request.args)
    with connection() as service:
        return jsonify(getattr(service, operation)(*args, filters))


@research_api.get("/datasets")
@research_api.get("/sources")
def datasets():
    return query("datasets")


@research_api.get("/subjects")
def subjects():
    return query("subjects")


@research_api.get("/summary")
def summary():
    return query("summary")


@research_api.get("/series")
def series():
    return query("series")


@research_api.get("/quality-flags")
def quality():
    return query("quality")


def records(entity):
    return query("records", entity)


for _entity in ("vital_signs", "sleep", "nutrition", "hydration", "lab_results"):
    research_api.add_url_rule("/" + _entity.replace("_", "-"), _entity, lambda name=_entity: records(name))


def register_research(app):
    app.register_blueprint(research_api)
