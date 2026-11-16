"""Contratos y selección de la implementación de persistencia."""
from persistence.contracts import PersistenceRepository
from persistence.provider import get_repository, use_repository

__all__ = ["PersistenceRepository", "get_repository", "use_repository"]
