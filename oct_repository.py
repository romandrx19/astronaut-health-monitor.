"""Operaciones de exámenes OCT sobre el contrato de persistencia."""
from copy import deepcopy
import hashlib
import json
from persistence.contracts import OctConflict
from persistence.provider import get_repository
from oct_domain import now, documented, same_acquisition, ocular_summary


def init_oct_db(repository=None):
    get_repository(repository).initialize("oct")


def _public_exam(document):
    exam = deepcopy(document)
    exam.pop("request_key", None)
    exam.pop("request_hash", None)
    for attachment in exam["files"]:
        attachment.pop("file_path", None)
        attachment.pop("preview_path", None)
        attachment["content_url"] = f'/api/oct/files/{attachment["id"]}/content?astronaut_id={exam["astronaut_id"]}'
    return exam


def list_exams(astronaut_id, *, repository=None):
    return [_public_exam(exam) for exam in get_repository(repository).list_oct_exams(astronaut_id)]


def get_exam(exam_id, astronaut_id, *, repository=None):
    return _public_exam(get_repository(repository).get_oct_exam(exam_id, astronaut_id))


def _replace_baseline(repository, exam):
    # La sustitución ocurre únicamente por una elección explícita del usuario.
    for old in repository.list_oct_exams(exam["astronaut_id"]):
        if old["is_baseline"] and same_acquisition(old, exam):
            repository.update_oct_exam(old["id"], exam["astronaut_id"], {"is_baseline": False, "updated_at": now()})


def _insert_files(repository, exam_id, attachments):
    repository.add_oct_files(exam_id, [{**item, "uploaded_at": now()} for item in attachments])


def create_exam(astronaut_id, payload, attachments, request_key=None, *, repository=None):
    digest = hashlib.sha256(json.dumps({"astronaut_id": astronaut_id, "exam": payload,
        "files": [item["checksum"] for item in attachments]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with get_repository(repository).transaction(write=True) as session:
        if session.get_astronaut(astronaut_id) is None:
            raise LookupError("Astronauta no encontrado")
        if request_key:
            previous = session.find_oct_request(request_key)
            if previous:
                if previous["request_hash"] != digest:
                    raise OctConflict("La clave de reintento corresponde a otro examen o archivos")
                return _public_exam(previous), False
        if payload["is_baseline"]:
            _replace_baseline(session, {**payload, "astronaut_id": astronaut_id})
        timestamp = now()
        record = {key: value for key, value in payload.items() if key != "measurements"}
        record.update(astronaut_id=astronaut_id, request_key=request_key, request_hash=digest,
                      created_at=timestamp, updated_at=timestamp)
        exam_id = session.insert_oct_exam(record, payload["measurements"])
        _insert_files(session, exam_id, attachments)
        return _public_exam(session.get_oct_exam(exam_id, astronaut_id)), True


def attach_files(exam_id, astronaut_id, attachments, *, repository=None):
    with get_repository(repository).transaction(write=True) as session:
        exam = session.get_oct_exam(exam_id, astronaut_id)
        existing = {item["checksum"] for item in exam["files"]}
        if len(existing) + len(attachments) > 10:
            raise ValueError("Un examen admite hasta 10 archivos")
        if any(item["checksum"] in existing for item in attachments):
            raise OctConflict("Este archivo ya está asociado al examen")
        _insert_files(session, exam_id, attachments)
        return _public_exam(session.update_oct_exam(exam_id, astronaut_id, {"updated_at": now()}))


def update_exam(exam_id, astronaut_id, changes, *, repository=None):
    with get_repository(repository).transaction(write=True) as session:
        exam = session.get_oct_exam(exam_id, astronaut_id)
        if changes.get("is_baseline"):
            _replace_baseline(session, exam)
        return _public_exam(session.update_oct_exam(exam_id, astronaut_id, {**changes, "updated_at": now()}))


def get_file(file_id, astronaut_id, *, repository=None):
    return get_repository(repository).get_oct_file(file_id, astronaut_id)
