"""Validación de registros OCT y API local de demostración."""
from datetime import date, datetime, timezone
import hmac
from ipaddress import ip_address
import json
from math import isfinite
import re
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, abort, jsonify, request, send_file

from oct_analysis import compare_exams
from oct_files import MAX_FILE_BYTES, MAX_UPLOAD_FILES, cleanup_files, prepare_files, resolve_private, storage_root
from science_service import oct_review_rules
from oct_repository import (OctConflict, attach_files, create_exam, get_exam, get_file,
                            list_exams, ocular_summary, update_exam)

oct_api = Blueprint("oct", __name__)
CAPABILITIES = {"mode": "local_demo", "formats": ["PNG", "JPEG", "TIFF"], "dicom_supported": False,
                "max_file_bytes": MAX_FILE_BYTES, "max_files_per_upload": MAX_UPLOAD_FILES,
                "label": "Datos OCT de demostración"}
REVIEW_STATES = {"pending", "review_requested", "reviewed"}
CREATE_FIELDS = {"eye", "exam_date", "mission_phase", "device_model", "scan_protocol", "image_quality",
                 "is_baseline", "review_status", "reviewed_by", "reviewed_at", "review_notes", "notes",
                 "provenance", "source_document", "is_demo", "measurements"}


def text(value, label, maximum=200, required=False):
    if value is None and not required:
        value = ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{label}: introduce texto de hasta {maximum} caracteres")
    value = " ".join(value.split()) if maximum <= 200 else value.strip()
    if required and not value:
        raise ValueError(f"Completa {label}")
    return value


def timestamp(value, label):
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError(f"{label}: se requiere una fecha ISO con zona horaria")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        parsed = parsed.astimezone(timezone.utc)
        if parsed > datetime.now(timezone.utc):
            raise ValueError()
        return parsed.isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        raise ValueError(f"{label}: usa una fecha válida con zona horaria y que no sea futura") from None


def validate_review(data, exam_date):
    status = data.get("review_status", "pending")
    if not isinstance(status, str) or status not in REVIEW_STATES:
        raise ValueError("Estado de revisión desconocido")
    result = {"review_status": status, "review_notes": text(data.get("review_notes", ""), "Observaciones de revisión", 10000)}
    if status == "reviewed":
        result["reviewed_by"] = text(data.get("reviewed_by", ""), "Profesional que revisó el examen", required=True)
        result["reviewed_at"] = timestamp(data.get("reviewed_at"), "Fecha de revisión")
        if result["reviewed_at"] < exam_date:
            raise ValueError("La revisión debe ser posterior o igual al examen")
    return result


def validate_exam(data):
    if not isinstance(data, dict) or set(data) - CREATE_FIELDS:
        raise ValueError("Se requiere un objeto de examen OCT con los campos documentados")
    if data.get("is_demo") is not True:
        raise ValueError("Este módulo local admite únicamente datos OCT de demostración")
    if data.get("eye") not in ("OD", "OS"):
        raise ValueError("Selecciona OD (ojo derecho) u OS (ojo izquierdo)")
    if data.get("mission_phase") not in ("preflight", "inflight", "postflight"):
        raise ValueError("Selecciona preflight, inflight o postflight")
    quality = data.get("image_quality", "unknown")
    if quality not in ("good", "poor", "ungradable", "unknown"):
        raise ValueError("Calidad desconocida: usa good, poor, ungradable o unknown")
    baseline = data.get("is_baseline", False)
    if not isinstance(baseline, bool):
        raise ValueError("is_baseline debe ser verdadero o falso")
    provenance = data.get("provenance", "manual_entry")
    if provenance not in ("manual_entry", "exported_report"):
        raise ValueError("Procedencia OCT desconocida")
    result = {"eye": data["eye"], "mission_phase": data["mission_phase"],
              "exam_date": timestamp(data.get("exam_date"), "Fecha del examen"), "image_quality": quality,
              "device_model": text(data.get("device_model", ""), "Equipo OCT"),
              "scan_protocol": text(data.get("scan_protocol", ""), "Protocolo de exploración"),
              "notes": text(data.get("notes", ""), "Observaciones", 20000), "is_baseline": baseline,
              "is_demo": True, "provenance": provenance,
              "source_document": text(data.get("source_document", ""), "Documento de procedencia", required=provenance == "exported_report"),
              "reviewed_by": "", "reviewed_at": None}
    result.update(validate_review(data, result["exam_date"]))
    measurements = data.get("measurements", [])
    if not isinstance(measurements, list) or len(measurements) > 50:
        raise ValueError("measurements debe ser una lista de hasta 50 mediciones documentadas")
    result["measurements"] = []
    seen = set()
    for item in measurements:
        if (not isinstance(item, dict) or not {"metric", "value"}.issubset(item)
                or set(item) - {"metric", "value", "unit", "anatomical_region", "measurement_method"}):
            raise ValueError("Cada medición necesita indicador, valor y los metadatos disponibles")
        metric = text(item["metric"], "Indicador", 64, required=True).lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", metric):
            raise ValueError("El código del indicador debe usar letras, números y guion bajo")
        value = item["value"]
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError("Las mediciones deben contener números finitos")
        try:
            value = float(value)
        except OverflowError:
            raise ValueError("La medición está fuera de la precisión numérica admitida") from None
        if not isfinite(value):
            raise ValueError("Las mediciones deben contener números finitos")
        unit = text(item.get("unit", ""), "Unidad", 32)
        unit = {"um": "µm", "μm": "µm"}.get(unit, unit)
        region = text(item.get("anatomical_region", ""), "Región anatómica")
        method = text(item.get("measurement_method", ""), "Método de medición")
        key = (metric, unit, region.casefold(), method.casefold())
        if key in seen:
            raise ValueError("Hay mediciones duplicadas para la misma variable, unidad, región y método")
        seen.add(key)
        result["measurements"].append({"metric": metric, "value": value, "unit": unit,
                                       "anatomical_region": region, "measurement_method": method})
    return result


def register_oct(app):
    app.config.setdefault("ARES_OCT_REVIEW_RULES", [])
    app.config.setdefault("ARES_OCT_STORAGE", None)
    app.config["ARES_OCT_SESSION_TOKEN"] = secrets.token_urlsafe(32)
    app.register_blueprint(oct_api)


def session_token():
    from flask import current_app
    return current_app.config["ARES_OCT_SESSION_TOKEN"]


@oct_api.before_request
def local_demo_access():
    try:
        local_client = ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        local_client = False
    host = urlsplit(request.host_url).hostname
    if not local_client or host not in ("127.0.0.1", "localhost", "::1"):
        abort(403, description="OCT está disponible únicamente en el entorno local de demostración")
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403, description="Abre OCT desde la aplicación local")
    cookie = request.cookies.get("ares_oct_session", "")
    if not hmac.compare_digest(cookie.encode(), session_token().encode()):
        abort(403, description="Abre primero la página de ARES HEALTH para iniciar la sesión local OCT")
    if request.method in ("POST", "PATCH"):
        if not hmac.compare_digest(request.headers.get("X-ARES-OCT-Token", "").encode(), session_token().encode()):
            abort(403, description="La sesión OCT cambió. Recarga la aplicación y vuelve a intentarlo")
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403, description="Origen de solicitud OCT no permitido")
        request.max_content_length = 51 * 1024 * 1024
        request.max_form_memory_size = 256 * 1024


@oct_api.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@oct_api.errorhandler(ValueError)
def invalid_oct(error):
    return jsonify(error=str(error), notice={"kind": "invalid_import", "reason": str(error)}), 400


@oct_api.errorhandler(OctConflict)
def conflict_oct(error):
    return jsonify(error=str(error)), 409


@oct_api.errorhandler(LookupError)
def missing_oct(error):
    return jsonify(error=str(error)), 404


@oct_api.errorhandler(403)
def forbidden_oct(error):
    return jsonify(error=error.description), 403


@oct_api.errorhandler(413)
def large_oct(error):
    return jsonify(error="La operación supera el tamaño admitido. Cada imagen admite hasta 10 MiB"), 413


def owner_id():
    raw = request.args.get("astronaut_id")
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError("Se requiere astronaut_id para comprobar la pertenencia del examen") from None


def optional_id(name):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError()
        return value
    except ValueError:
        raise ValueError(f"{name} debe ser un identificador positivo") from None


def period_date(name):
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise ValueError("El periodo necesita fechas con formato YYYY-MM-DD") from None


@oct_api.get("/api/astronauts/<int:astronaut_id>/oct/exams")
def exams_list(astronaut_id):
    exams = list_exams(astronaut_id)
    return jsonify(astronaut_id=astronaut_id, exams=exams, summary=ocular_summary(exams), capabilities=CAPABILITIES)


@oct_api.get("/api/astronauts/<int:astronaut_id>/oct/summary")
def summary(astronaut_id):
    return jsonify(astronaut_id=astronaut_id, **ocular_summary(list_exams(astronaut_id)))


@oct_api.post("/api/astronauts/<int:astronaut_id>/oct/exams")
def exams_create(astronaut_id):
    from flask import current_app
    if request.is_json:
        data, uploads = request.get_json(silent=True), []
    elif request.mimetype == "multipart/form-data":
        try:
            data = json.loads(request.form.get("exam", ""))
        except (ValueError, TypeError):
            raise ValueError("El campo exam debe contener un objeto JSON válido") from None
        uploads = request.files.getlist("files")
        if set(request.files) - {"files"}:
            raise ValueError("Utiliza files para los archivos asociados")
    else:
        return jsonify(error="Usa JSON o multipart/form-data con el campo exam y archivos files"), 415
    payload = validate_exam(data)
    key = request.headers.get("Idempotency-Key")
    if key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
        raise ValueError("Idempotency-Key debe usar hasta 128 letras, números, guiones o guiones bajos")
    # La pertenencia se comprueba antes de procesar o guardar las imágenes.
    list_exams(astronaut_id)
    root, attachments = prepare_files(uploads, current_app.config)
    try:
        exam, created = create_exam(astronaut_id, payload, attachments, key)
    except Exception:
        cleanup_files(root, attachments)
        raise
    if not created:
        cleanup_files(root, attachments)
    return jsonify(exam=exam, created=created, label=CAPABILITIES["label"]), 201 if created else 200


@oct_api.post("/api/oct/validate")
def validate_import():
    if not request.is_json:
        return jsonify(error="Se requiere un objeto JSON de examen OCT"), 415
    return jsonify(validate_exam(request.get_json(silent=True)))


@oct_api.get("/api/oct/exams/<int:exam_id>")
def exam_detail(exam_id):
    return jsonify(get_exam(exam_id, owner_id()))


@oct_api.get("/api/oct/exams/<int:exam_id>/measurements")
def measurements(exam_id):
    return jsonify(get_exam(exam_id, owner_id())["measurements"])


@oct_api.patch("/api/oct/exams/<int:exam_id>")
def exam_update(exam_id):
    astronaut_id = owner_id()
    exam = get_exam(exam_id, astronaut_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data or set(data) - {"is_baseline", "review_status", "reviewed_by", "reviewed_at", "review_notes", "notes"}:
        raise ValueError("Solo puedes actualizar la referencia basal, observaciones o revisión del examen")
    changes = {}
    if "is_baseline" in data:
        if not isinstance(data["is_baseline"], bool):
            raise ValueError("is_baseline debe ser verdadero o falso")
        changes["is_baseline"] = data["is_baseline"]
    if "notes" in data:
        changes["notes"] = text(data["notes"], "Observaciones", 20000)
    review_fields = {"review_status", "reviewed_by", "reviewed_at", "review_notes"}
    if review_fields.intersection(data):
        changes.update(validate_review({**exam, **data}, exam["exam_date"]))
    return jsonify(update_exam(exam_id, astronaut_id, changes))


@oct_api.get("/api/astronauts/<int:astronaut_id>/oct/comparison")
def comparison(astronaut_id):
    eye = request.args.get("eye")
    if eye is not None and eye not in ("OD", "OS"):
        raise ValueError("Selecciona OD u OS para el seguimiento")
    start, end = period_date("from"), period_date("to")
    if start and end and start > end:
        raise ValueError("La fecha inicial debe ser anterior o igual a la final")
    exams = list_exams(astronaut_id)
    if eye:
        exams = [exam for exam in exams if exam["eye"] == eye]
    result = compare_exams(exams, optional_id("current_id"), optional_id("baseline_id"), start, end,
                           oct_review_rules())
    return jsonify(astronaut_id=astronaut_id, **result)


@oct_api.post("/api/oct/exams/<int:exam_id>/files")
def files_create(exam_id):
    from flask import current_app
    astronaut_id = owner_id()
    get_exam(exam_id, astronaut_id)
    uploads = request.files.getlist("files")
    if not uploads or not any(upload.filename for upload in uploads):
        raise ValueError("Selecciona al menos una imagen")
    root, attachments = prepare_files(uploads, current_app.config)
    try:
        exam = attach_files(exam_id, astronaut_id, attachments)
    except Exception:
        cleanup_files(root, attachments)
        raise
    return jsonify(exam), 201


@oct_api.get("/api/oct/files/<int:file_id>/content")
def file_content(file_id):
    from flask import current_app
    attachment = get_file(file_id, owner_id())
    path = resolve_private(storage_root(current_app.config), attachment["preview_path"])
    if not path.is_file():
        return jsonify(error="Vista previa no disponible. Revisa la copia privada y su respaldo"), 404
    response = send_file(path, mimetype="image/png", as_attachment=False, download_name=f"oct-demo-{file_id}.png", conditional=False)
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
