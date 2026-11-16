"""Inyección explícita, contexto local y selección por configuración Flask."""
from contextlib import contextmanager
from contextvars import ContextVar
from importlib import import_module

from flask import current_app, has_app_context

from persistence.contracts import PersistenceRepository

_override = ContextVar("ares_repository_override", default=None)
_default_repository = None


def get_repository(repository=None) -> PersistenceRepository:
    if repository is not None:
        return repository
    override = _override.get()
    if override is not None:
        return override
    if has_app_context():
        return current_app.extensions["ares_repository"]
    global _default_repository
    if _default_repository is None:
        from persistence.sqlite import SQLiteRepository
        _default_repository = SQLiteRepository()
    return _default_repository


@contextmanager
def use_repository(repository: PersistenceRepository):
    """Selecciona el adaptador solo para este contexto, incluso fuera de HTTP."""
    token = _override.set(repository)
    try:
        yield repository
    finally:
        _override.reset(token)


def configure_repository(app):
    backend = app.config["ARES_DATA_BACKEND"]
    if backend == "sqlite":
        from persistence.sqlite import SQLiteRepository
        repository = SQLiteRepository(app.config.get("ARES_DB_PATH"), rules_path=app.config.get("ARES_RULES_PATH"))
    elif backend == "custom":
        factory = app.config.get("ARES_REPOSITORY_FACTORY")
        if isinstance(factory, str):
            try:
                module, name = factory.split(":", 1)
                factory = getattr(import_module(module), name)
            except (ValueError, ImportError, AttributeError) as error:
                raise ValueError("ARES_REPOSITORY_FACTORY debe identificar una fábrica existente como paquete.modulo:crear_repositorio") from error
        if not callable(factory):
            raise ValueError("El backend custom necesita una fábrica funcional en ARES_REPOSITORY_FACTORY")
        repository = factory(app.config)
    else:
        raise ValueError(f"Backend de datos desconocido: {backend}. Usa sqlite o custom con una fábrica funcional")
    if not isinstance(repository, PersistenceRepository):
        raise TypeError("El adaptador no cumple PersistenceRepository. Revisa persistence/contracts.py")
    app.extensions["ares_repository"] = repository
    return repository
