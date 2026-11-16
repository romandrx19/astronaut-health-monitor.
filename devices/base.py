"""Interfaz reutilizable desde Flask o un servicio Python de escritorio."""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from devices.normalizer import NormalizedReading


class IntegrationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceStatus:
    model: str
    state: str
    message: str
    connected: bool = False
    telemetry_confirmed: bool = False
    verified_physical: bool = False
    last_received_at: str | None = None


class DeviceAdapter(ABC):
    @abstractmethod
    def status(self) -> DeviceStatus:
        """Un estado de Bluetooth no demuestra recepción de datos biométricos."""

    @abstractmethod
    def read(self, astronaut_id: int) -> NormalizedReading:
        """No completar indicadores que el origen no suministre."""
