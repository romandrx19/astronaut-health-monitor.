"""Fachada compatible de telemetría; acceso mediante el repositorio configurado."""
import os
from pathlib import Path
from persistence.provider import get_repository
from persistence.schema import ASTRONAUTS

DB_PATH = Path(os.environ.get("ARES_DB_PATH", Path(__file__).resolve().parent / "health.sqlite3"))


def connect():
    """Compatibilidad para mantenimiento SQLite; fuera del flujo de servicios y rutas."""
    from persistence.sqlite import open_connection
    return open_connection(DB_PATH)


def init_db(repository=None):
    get_repository(repository).initialize("telemetry")


def get_astronauts(repository=None):
    return get_repository(repository).list_astronauts()


def get_latest(astronaut_id, source=None, *, repository=None):
    rows = get_repository(repository).get_history(astronaut_id, 1, source)
    return rows[-1] if rows else None


def get_history(astronaut_id, limit=36, source=None, *, repository=None):
    return get_repository(repository).get_history(astronaut_id, limit, source)


def add_measurements(items, *, repository=None):
    get_repository(repository).add_measurements(items)


def add_manual_measurement(sample, *, repository=None):
    return get_repository(repository).add_manual_measurement(sample)


def has_measurements(repository=None):
    return get_repository(repository).has_measurements()
