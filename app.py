"""Servidor Flask: páginas y endpoints JSON consumidos por JavaScript."""
from runtime_paths import configure_runtime

configure_runtime()

from flask import Flask, abort, current_app, jsonify, make_response, render_template, request
import os

from persistence.provider import configure_repository
from persistence.contracts import RepositoryUnavailable
import science_service

from alerts import evaluate
from database import add_manual_measurement, get_astronauts, get_history, get_latest
from measurements import validate_manual
from medical import medical_profile, save_medical_profile
from health_analysis import AnalysisRules, METRICS as ANALYSIS_METRICS
from health_events import EVENT_STATUSES, assess_astronaut, get_health_events
from simulator import seed_if_empty, simulate_due, simulation_status, set_simulation_running
from acquisition import DuplicateRequest, reading_document, store_reading
from devices.normalizer import normalize_manual
from devices.manager import DeviceManagerError, DeviceSessionBusy
from oct_service import register_oct, session_token
from research_api import register_research
from local_security import register_local_security
from wearable_service import init_wearable_service


def register_api(app):
    def valid_astronaut(astronaut_id):
        if not any(astronaut["id"] == astronaut_id for astronaut in get_astronauts()):
            abort(404, description="Astronauta no encontrado")


    @app.get("/")
    def index():
        response = make_response(render_template("index.html", oct_token=session_token()))
        response.set_cookie("ares_oct_session", session_token(), httponly=True, samesite="Strict")
        response.headers["Cache-Control"] = "no-store"
        return response


    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "mode": "simulation"})


    @app.get("/api/astronauts")
    def astronauts():
        source = requested_source()
        payload = []
        for astronaut in get_astronauts():
            latest = get_latest(astronaut["id"], source)
            payload.append({**astronaut, "latest": latest,
                            "alerts": evaluate(latest) if latest else []})
        return jsonify(payload)


    @app.get("/api/astronauts/<int:astronaut_id>/history")
    def history(astronaut_id):
        valid_astronaut(astronaut_id)
        source = requested_source()
        raw_limit = request.args.get("limit", "36")
        try:
            limit = int(raw_limit)
        except ValueError:
            return jsonify({"error": "limit debe ser un número entero"}), 400
        if not 1 <= limit <= 200:
            return jsonify({"error": "limit debe estar entre 1 y 200"}), 400
        return jsonify(get_history(astronaut_id, limit, source))


    def requested_source():
        source = request.args.get("source")
        if source not in (None, "manual", "simulated", "simulation", "smartwatch"):
            abort(400, description="source debe ser manual, simulation (o simulated) o smartwatch")
        return source


    @app.get("/api/devices/status")
    def device_status():
        service = current_app.extensions["ares_wearable_service"]
        return jsonify(service.status())


    @app.post("/api/devices/connect")
    def device_connect():
        if not request.is_json:
            return jsonify(error="Se requiere Content-Type: application/json"), 415
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"device_id", "astronaut_id"}:
            return jsonify(error="Usa únicamente device_id y astronaut_id"), 400
        device_id = data.get("device_id")
        astronaut_id = data.get("astronaut_id")
        if not isinstance(device_id, str) or not device_id.strip():
            return jsonify(error="device_id debe ser texto no vacío"), 400
        if (not isinstance(astronaut_id, int) or isinstance(astronaut_id, bool)
                or astronaut_id <= 0):
            return jsonify(error="astronaut_id debe ser un entero positivo"), 400
        try:
            status = current_app.extensions["ares_wearable_service"].connect(
                device_id=device_id.strip(),
                astronaut_id=astronaut_id,
            )
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except DeviceSessionBusy as error:
            return jsonify(error=str(error)), 409
        except DeviceManagerError as error:
            return jsonify(error=str(error)), 503
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(status), 202


    @app.post("/api/devices/disconnect")
    def device_disconnect():
        service = current_app.extensions["ares_wearable_service"]
        return jsonify(service.disconnect())


    @app.get("/api/devices/latest")
    def device_latest():
        reading = current_app.extensions["ares_wearable_service"].latest()
        if reading is None:
            return jsonify(error="Todavía no hay telemetría JG74 válida"), 404
        return jsonify(reading)


    @app.post("/api/astronauts/<int:astronaut_id>/measurements/manual")
    def partial_manual_measurement(astronaut_id):
        valid_astronaut(astronaut_id)
        if not request.is_json:
            return jsonify(error="Se requiere Content-Type: application/json"), 415
        try:
            reading = normalize_manual(request.get_json(silent=True), astronaut_id)
            saved, created = store_reading(reading, request.headers.get("Idempotency-Key"))
        except DuplicateRequest as error:
            return jsonify(error=str(error)), 409
        except ValueError as error:
            return jsonify(error=str(error)), 400
        result = assess_astronaut(astronaut_id, "manual", rules=AnalysisRules(**science_service.analysis_settings()),
                                 protocols=science_service.safety_protocols())
        return jsonify(measurement=reading_document(saved), created=created, assessment=result,
                       alerts=evaluate(saved)), 201 if created else 200


    @app.get("/api/astronauts/<int:astronaut_id>/measurements")
    def measurements(astronaut_id):
        valid_astronaut(astronaut_id)
        source = requested_source()
        try:
            limit = int(request.args.get("limit", "36"))
        except ValueError:
            return jsonify(error="limit debe ser un número entero"), 400
        if not 1 <= limit <= 200:
            return jsonify(error="limit debe estar entre 1 y 200"), 400
        return jsonify([reading_document(item) for item in get_history(astronaut_id, limit, source)])


    @app.post("/api/astronauts/<int:astronaut_id>/measurements")
    def manual_measurement(astronaut_id):
        valid_astronaut(astronaut_id)
        if not request.is_json:
            return jsonify(error="Se requiere Content-Type: application/json"), 415
        try:
            sample = validate_manual(request.get_json(silent=True), astronaut_id)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        saved = add_manual_measurement(sample)
        return jsonify(measurement=saved, alerts=evaluate(saved)), 201


    @app.errorhandler(400)
    def bad_request(error):
        return jsonify(error=error.description), 400


    @app.get("/api/astronauts/<int:astronaut_id>/medical")
    def get_medical(astronaut_id):
        try:
            return jsonify(medical_profile(astronaut_id))
        except LookupError as error:
            abort(404, description=str(error))


    @app.post("/api/astronauts/<int:astronaut_id>/medical")
    def post_medical(astronaut_id):
        valid_astronaut(astronaut_id)
        if not request.is_json:
            return jsonify(error="Se requiere Content-Type: application/json"), 415
        try:
            return jsonify(save_medical_profile(astronaut_id, request.get_json(silent=True)))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except LookupError as error:
            abort(404, description=str(error))


    @app.post("/api/simulate")
    def simulate():
        data = request.get_json(silent=True) if request.is_json else {}
        if not isinstance(data, dict) or set(data) - {"automatic"} or not isinstance(data.get("automatic", False), bool):
            return jsonify(error="Usa automatic como valor booleano"), 400
        result = simulate_due(automatic=data.get("automatic", False))
        return jsonify(result), 201 if result["created"] else 200

    @app.get("/api/simulation")
    def get_simulation():
        return jsonify(simulation_status())

    @app.post("/api/simulation")
    def update_simulation():
        if not request.is_json:
            return jsonify(error="Se requiere Content-Type: application/json"), 415
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"running"} or not isinstance(data["running"], bool):
            return jsonify(error="Usa únicamente running como valor booleano"), 400
        return jsonify(set_simulation_running(data["running"]))


    @app.get("/api/astronauts/<int:astronaut_id>/assessment")
    def assessment(astronaut_id):
        source = requested_source()
        try:
            return jsonify(assess_astronaut(astronaut_id, source,
                           rules=AnalysisRules(**science_service.analysis_settings()),
                           protocols=science_service.safety_protocols()))
        except LookupError as error:
            abort(404, description=str(error))


    @app.get("/api/astronauts/<int:astronaut_id>/events")
    def events(astronaut_id):
        metric, status = request.args.get("metric"), request.args.get("status")
        if metric is not None and metric not in ANALYSIS_METRICS:
            return jsonify(error="Indicador desconocido"), 400
        if status is not None and status not in EVENT_STATUSES:
            return jsonify(error="Estado de evento desconocido"), 400
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify(error="limit debe ser un número entero"), 400
        if not 1 <= limit <= 200:
            return jsonify(error="limit debe estar entre 1 y 200"), 400
        try:
            return jsonify(get_health_events(astronaut_id, limit, metric, status))
        except LookupError as error:
            abort(404, description=str(error))


    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": error.description}), 404
        return error

    @app.get("/api/rules")
    def scientific_catalog():
        return jsonify(science_service.catalog())

    @app.errorhandler(RepositoryUnavailable)
    def unavailable(error):
        return jsonify(error=str(error)), 503


def create_app(config=None):
    application = Flask(__name__)
    application.config.from_mapping(
        ARES_DATA_BACKEND=os.environ.get("ARES_DATA_BACKEND", "sqlite"),
        ARES_REPOSITORY_FACTORY=os.environ.get("ARES_REPOSITORY_FACTORY"),
        ARES_RULES_PATH=os.environ.get("ARES_RULES_PATH"),
        ARES_ANALYSIS_RULES={}, ARES_SAFETY_PROTOCOLS=[],
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        MAX_CONTENT_LENGTH=51 * 1024 * 1024,
    )
    if config:
        application.config.update(config)
    application.config.setdefault("ARES_SEED_DEMO", application.config["ARES_DATA_BACKEND"] == "sqlite")
    repository = configure_repository(application)
    with application.app_context():
        repository.initialize("all")
        if application.config["ARES_SEED_DEMO"]:
            seed_if_empty()
    init_wearable_service(application, repository)
    register_api(application)
    register_local_security(application)
    register_oct(application)
    register_research(application)
    return application


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("ARES_DEBUG") == "1", use_reloader=False)
