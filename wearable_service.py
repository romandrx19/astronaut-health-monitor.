"""Fachada entre Flask, DeviceManager y la persistencia existente de ARES."""

from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import Any, Callable

from acquisition import store_reading
from devices.manager import DeviceManager, VerifiedSession
from devices.normalizer import NormalizedReading
from persistence.contracts import PersistenceRepository


def store_verified_wearable_reading(
    reading: NormalizedReading,
    verification: VerifiedSession,
    *,
    repository: PersistenceRepository,
) -> dict[str, Any]:
    """Persiste una lectura solo si coincide con la sesión física verificada."""

    if not isinstance(reading, NormalizedReading):
        raise TypeError("Se requiere el NormalizedReading de Leandro")
    if not isinstance(verification, VerifiedSession):
        raise TypeError("Falta la verificación de la sesión wearable")
    if reading.source != "smartwatch":
        raise ValueError("La fuente wearable debe ser smartwatch")
    if reading.provenance != "verified_device":
        raise ValueError("La procedencia wearable debe ser verified_device")
    if not isinstance(reading.device_id, str) or not reading.device_id.strip():
        raise ValueError("La lectura wearable requiere device_id")
    if not verification.telemetry_confirmed or not verification.verified_physical:
        raise ValueError("La sesión no confirma telemetría física verificada")
    if reading.astronaut_id != verification.astronaut_id:
        raise ValueError("El astronauta de la lectura no coincide con la sesión")
    if reading.device_id != verification.device_id:
        raise ValueError("El dispositivo de la lectura no coincide con la sesión")
    if repository.get_astronaut(reading.astronaut_id) is None:
        raise LookupError("Astronauta no encontrado")

    saved, created = store_reading(
        reading,
        request_id=f"jg74:{reading.fingerprint}",
        repository=repository,
    )
    return {"measurement": saved, "created": created}


@dataclass(frozen=True)
class VerifiedWearableStore:
    """Callback explícito; nunca resuelve un repositorio global en el thread BLE."""

    repository: PersistenceRepository

    def __call__(
        self,
        reading: NormalizedReading,
        verification: VerifiedSession,
    ) -> dict[str, Any]:
        return store_verified_wearable_reading(
            reading,
            verification,
            repository=self.repository,
        )


class WearableService:
    def __init__(
        self,
        repository: PersistenceRepository,
        manager: DeviceManager,
        store: VerifiedWearableStore,
    ) -> None:
        self.repository = repository
        self.manager = manager
        self.store = store

    def status(self) -> dict[str, Any]:
        status = self.manager.status()
        return {
            "devices": [status],
            "verified_telemetry_available": bool(
                status.get("state") == "receiving_telemetry"
                and status.get("connected")
                and status.get("telemetry_confirmed")
                and status.get("verified_physical")
            ),
        }

    def connect(self, *, device_id: str, astronaut_id: int) -> dict[str, Any]:
        if self.repository.get_astronaut(astronaut_id) is None:
            raise LookupError("Astronauta no encontrado")
        return self.manager.connect(device_id=device_id, astronaut_id=astronaut_id)

    def disconnect(self) -> dict[str, Any]:
        return self.manager.disconnect()

    def latest(self) -> dict[str, Any] | None:
        latest = self.manager.latest()
        if latest is None:
            return None
        status = self.manager.status()
        telemetry_confirmed = bool(
            status.get("telemetry_confirmed")
            and status.get("verified_physical")
            and status.get("device_id") == latest.get("device_id")
            and status.get("astronaut_id") == latest.get("astronaut_id")
        )
        return {
            **latest,
            "telemetry_confirmed": telemetry_confirmed,
            "verified_physical": bool(
                telemetry_confirmed and status.get("verified_physical")
            ),
        }

    def shutdown(self) -> None:
        self.manager.shutdown()


def init_wearable_service(
    app: Any,
    repository: PersistenceRepository,
    *,
    manager_factory: Callable[..., DeviceManager] | None = None,
) -> WearableService:
    """Configura una instancia por aplicación con el repositorio ya seleccionado."""

    factory = manager_factory or app.config.get("ARES_DEVICE_MANAGER_FACTORY") or DeviceManager
    store = VerifiedWearableStore(repository)
    manager = factory(store_callback=store)
    service = WearableService(repository, manager, store)
    app.extensions["ares_wearable_service"] = service
    atexit.register(service.shutdown)
    return service
