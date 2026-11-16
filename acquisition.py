"""Validación e ingestión de lecturas independientes del motor de persistencia."""
import re
from uuid import uuid4
from devices.normalizer import UNITS
from measurements import FIELDS
from persistence.contracts import DuplicateRequest, canonical_source
from persistence.provider import get_repository


def init_acquisition_db(repository=None):
    get_repository(repository).initialize("telemetry")


def query_history(connection, astronaut_id, limit=36, source=None):
    """Compatibilidad para herramientas SQLite anteriores; fuera del flujo de negocio."""
    from persistence.sqlite import read_history
    return read_history(connection, astronaut_id, limit, source)


def reading_document(item):
    """API nueva: origen canónico; no inventa la fecha de recepción de registros antiguos."""
    raw_source = item["source"]
    values = {metric: {"value": item[metric], "unit": item.get("units", {}).get(metric, UNITS[metric]),
                       "quality": item.get("quality", {}).get(metric, "unknown")}
              for metric in FIELDS if item.get(metric) is not None}
    return {**item, "source": canonical_source(raw_source), "original_source": raw_source,
            "acquired_at": item["captured_at"], "received_at": item.get("received_at"),
            "device_id": item.get("device_id"), "provenance": item.get("provenance", "legacy"),
            "observations": item.get("observations", ""), "metrics": values}


def store_reading(reading, request_id=None, *, repository=None):
    """Solo para código local de confianza. Los clientes HTTP no eligen el origen."""
    request_id = str(uuid4()) if request_id is None else request_id
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        raise ValueError("Idempotency-Key debe contener de 1 a 128 caracteres alfanuméricos, _, ., : o -")
    return get_repository(repository).store_reading(reading, request_id)


def ingest_from_adapter(adapter, astronaut_id, request_id=None, *, repository=None):
    """No hay ruta HTTP de dispositivos mientras falte un protocolo autorizado."""
    reading = adapter.read(astronaut_id)
    if reading.astronaut_id != astronaut_id:
        raise ValueError("El adaptador devolvió otro astronauta")
    if reading.source == "smartwatch":
        status = adapter.status()
        if not (status.verified_physical and status.connected and status.telemetry_confirmed):
            raise ValueError("El adaptador no confirma telemetría física verificada")
    return store_reading(reading, request_id, repository=repository)
