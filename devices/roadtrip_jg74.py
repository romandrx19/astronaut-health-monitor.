"""Integración BLE especializada del Roadtrip Watch 4 JG74.

El módulo no depende de Flask ni de SQLite. Su frontera termina en una
``NormalizedReading`` del contrato existente de ARES HEALTH.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib
import inspect
import logging
from typing import Any, Awaitable, Callable

from devices.normalizer import NormalizedReading, normalize_reading


LOGGER = logging.getLogger(__name__)

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"
BLUETOOTH_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"
ROADTRIP_MODEL = "Roadtrip Watch 4 JG74"
PAIRING_TIMEOUT_SECONDS = 60.0
OBSERVATION = (
    "Frecuencia cardíaca recibida mediante Bluetooth Heart Rate "
    "Measurement 0x2A37."
)

ReadingCallback = Callable[[NormalizedReading], Awaitable[None] | None]


class JG74Error(RuntimeError):
    """Fallo controlado de la integración física JG74."""


class BleUnavailableError(JG74Error):
    """Bleak o el backend Bluetooth del sistema no está disponible."""


class DeviceVerificationError(JG74Error):
    """El dispositivo no satisface el perfil físico JG74 verificado."""


class DeviceDisconnectedError(JG74Error):
    """La conexión física terminó de forma inesperada."""


def canonical_uuid(value: Any) -> str:
    """Normaliza UUID Bluetooth SIG de 16/32 bits o completos."""

    text = str(value).strip().lower()
    short = text[2:] if text.startswith("0x") else text
    if len(short) in (4, 8) and all(character in "0123456789abcdef" for character in short):
        return f"{short.zfill(8)}{BLUETOOTH_BASE_SUFFIX}"
    return text


def parse_heart_rate_measurement(data: bytes) -> int:
    """Decodifica únicamente el campo Heart Rate Value del estándar SIG.

    El bit 0 de flags selecciona uint8 (0) o uint16 little-endian (1). La
    función es pura y no realiza interpretación clínica.
    """

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Heart Rate Measurement debe ser bytes")
    payload = bytes(data)
    if not payload:
        raise ValueError("Heart Rate Measurement está vacío")

    uses_uint16 = bool(payload[0] & 0x01)
    required = 3 if uses_uint16 else 2
    if len(payload) < required:
        raise ValueError("Heart Rate Measurement está truncado")

    value = (
        int.from_bytes(payload[1:3], byteorder="little", signed=False)
        if uses_uint16
        else payload[1]
    )
    if value <= 0:
        raise ValueError("Heart Rate Measurement no contiene una frecuencia válida")
    return value


def normalize_heart_rate(
    data: bytes,
    *,
    astronaut_id: int,
    device_id: str,
    received_at: datetime | None = None,
) -> NormalizedReading:
    """Convierte un payload 0x2A37 al contrato oficial de Leandro."""

    timestamp = received_at or datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("received_at debe incluir zona horaria")
    timestamp = timestamp.astimezone(timezone.utc)
    heart_rate = parse_heart_rate_measurement(data)
    return normalize_reading(
        {
            "metrics": {
                "heart_rate": {
                    "value": heart_rate,
                    "unit": "bpm",
                    "quality": "good",
                }
            },
            "acquired_at": timestamp.isoformat(),
            "observations": OBSERVATION,
        },
        astronaut_id,
        source="smartwatch",
        device_id=device_id,
        provenance="verified_device",
        now=timestamp,
    )


def _load_bleak_module() -> Any:
    """Carga Bleak bajo demanda para que Flask funcione sin el extra BLE."""

    try:
        return importlib.import_module("bleak")
    except ImportError as error:
        raise BleUnavailableError(
            "Bleak no está instalado; instala requirements-ble.txt para usar el JG74."
        ) from error


def _load_bleak_client() -> Any:
    return _load_bleak_module().BleakClient


def _load_bleak_scanner() -> Any:
    return _load_bleak_module().BleakScanner


class RoadtripJG74Adapter:
    """Conexión BLE asíncrona administrada exclusivamente por DeviceManager."""

    def __init__(
        self,
        *,
        device_id: str,
        astronaut_id: int,
        on_reading: ReadingCallback,
        pair: bool = True,
        client_factory: Any = None,
        scanner_class: Any = None,
        scan_time: float = 10.0,
    ) -> None:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id es obligatorio")
        if not isinstance(astronaut_id, int) or isinstance(astronaut_id, bool) or astronaut_id <= 0:
            raise ValueError("astronaut_id debe ser un entero positivo")
        if not callable(on_reading):
            raise TypeError("on_reading debe ser invocable")
        self.device_id = device_id.strip()
        self.astronaut_id = astronaut_id
        self.on_reading = on_reading
        self.pair = bool(pair)
        self._client_factory = client_factory
        self._scanner_class = scanner_class
        self.scan_time = float(scan_time)
        self._client: Any = None
        self._characteristic: Any = None
        self._subscribed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._disconnected_event: asyncio.Event | None = None
        self.invalid_notification_count = 0

    @property
    def is_connected(self) -> bool:
        return bool(self._client is not None and self._client.is_connected)

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._disconnected_event = asyncio.Event()
        client_factory = self._client_factory or _load_bleak_client()
        target = await self._resolve_device()
        kwargs: dict[str, Any] = {"disconnected_callback": self._on_disconnected}
        if self.pair:
            kwargs.update(pair=True, timeout=PAIRING_TIMEOUT_SECONDS)
        self._client = client_factory(target, **kwargs)

        try:
            LOGGER.info("Conectando %s: %s", ROADTRIP_MODEL, self.device_id)
            await self._client.connect()
            if not self._client.is_connected:
                raise DeviceDisconnectedError("Bleak no dejó una conexión activa")

            services = self._client.services
            await self._verify_device_name(services)
            service = self._find_service(services, HEART_RATE_SERVICE_UUID)
            if service is None:
                raise DeviceVerificationError("Heart Rate Service 0x180D ausente")

            characteristic = self._find_characteristic(
                service.characteristics,
                HEART_RATE_MEASUREMENT_UUID,
            )
            if characteristic is None:
                raise DeviceVerificationError("Heart Rate Measurement 0x2A37 ausente")
            properties = {
                str(value).strip().lower()
                for value in (characteristic.properties or ())
            }
            if "notify" not in properties:
                raise DeviceVerificationError(
                    "Heart Rate Measurement 0x2A37 no soporta notify"
                )

            self._characteristic = characteristic
            await self._client.start_notify(characteristic, self._handle_notification)
            self._subscribed = True
            LOGGER.info("JG74 verificado y suscripción 0x2A37 iniciada")
        except Exception as error:
            await self.disconnect()
            if type(error).__name__ == "BleakBluetoothNotAvailableError":
                raise BleUnavailableError(str(error)) from error
            raise

    async def _resolve_device(self) -> Any:
        if self.device_id.casefold() != "auto":
            return self.device_id

        scanner_class = self._scanner_class or _load_bleak_scanner()
        find_device = getattr(scanner_class, "find_device_by_filter", None)
        if callable(find_device):
            selected = await find_device(
                lambda device, advertisement: self._is_expected_name(
                    str(
                        getattr(advertisement, "local_name", None)
                        or getattr(device, "name", None)
                        or ""
                    ).strip()
                ),
                timeout=self.scan_time,
            )
            if selected is None:
                raise DeviceVerificationError(
                    "No se encontró el Roadtrip Watch 4 JG74 durante el escaneo"
                )
            return self._remember_selected_device(selected)

        raw_results = await scanner_class.discover(
            timeout=self.scan_time,
            return_adv=True,
        )
        values = raw_results.values() if isinstance(raw_results, dict) else raw_results
        candidates = []
        for item in values or ():
            device, advertisement = item if isinstance(item, tuple) else (item, None)
            name = str(
                getattr(advertisement, "local_name", None)
                or getattr(device, "name", None)
                or ""
            ).strip()
            if self._is_expected_name(name):
                candidates.append(device)
        if not candidates:
            raise DeviceVerificationError(
                "No se encontró el Roadtrip Watch 4 JG74 durante el escaneo"
            )
        if len(candidates) > 1:
            raise DeviceVerificationError(
                "Se encontraron varios JG74; utiliza un identificador BLE explícito"
            )
        return self._remember_selected_device(candidates[0])

    def _remember_selected_device(self, selected: Any) -> Any:
        self.device_id = str(
            getattr(selected, "address", None)
            or getattr(selected, "identifier", None)
            or ""
        ).strip()
        if not self.device_id:
            raise DeviceVerificationError("El JG74 no publicó un identificador BLE")
        return selected

    async def _verify_device_name(self, services: Any) -> None:
        name = str(getattr(self._client, "name", None) or "").strip()
        if not self._is_expected_name(name):
            characteristic = self._find_characteristic_in_services(
                services,
                DEVICE_NAME_UUID,
            )
            if characteristic is not None and "read" in {
                str(value).strip().lower()
                for value in (characteristic.properties or ())
            }:
                try:
                    raw_name = await self._client.read_gatt_char(characteristic)
                    name = bytes(raw_name).decode("utf-8", errors="replace").strip()
                except Exception as error:
                    LOGGER.warning("No se pudo leer Device Name: %s", error)
        if not self._is_expected_name(name):
            raise DeviceVerificationError(
                f"El dispositivo conectado no se identificó como JG74: {name or 'sin nombre'}"
            )

    @staticmethod
    def _is_expected_name(name: str) -> bool:
        folded = name.casefold()
        return "roadtrip watch 4" in folded or "jg74" in folded

    @staticmethod
    def _find_service(services: Any, uuid: str) -> Any | None:
        target = canonical_uuid(uuid)
        return next(
            (service for service in services if canonical_uuid(service.uuid) == target),
            None,
        )

    @staticmethod
    def _find_characteristic(characteristics: Any, uuid: str) -> Any | None:
        target = canonical_uuid(uuid)
        return next(
            (
                characteristic
                for characteristic in characteristics
                if canonical_uuid(characteristic.uuid) == target
            ),
            None,
        )

    @classmethod
    def _find_characteristic_in_services(cls, services: Any, uuid: str) -> Any | None:
        for service in services:
            characteristic = cls._find_characteristic(service.characteristics, uuid)
            if characteristic is not None:
                return characteristic
        return None

    async def _handle_notification(self, _sender: Any, data: bytearray) -> None:
        try:
            reading = normalize_heart_rate(
                bytes(data),
                astronaut_id=self.astronaut_id,
                device_id=self.device_id,
            )
        except (TypeError, ValueError) as error:
            self.invalid_notification_count += 1
            LOGGER.warning("Notificación 0x2A37 inválida: %s", error)
            return

        result = self.on_reading(reading)
        if inspect.isawaitable(result):
            await result

    def _on_disconnected(self, _client: Any) -> None:
        LOGGER.warning("El Roadtrip Watch 4 JG74 se desconectó")
        if self._loop is not None and self._disconnected_event is not None:
            self._loop.call_soon_threadsafe(self._disconnected_event.set)

    async def wait_for_disconnect(self, timeout: float) -> bool:
        if not self.is_connected:
            return True
        if self._disconnected_event is None:
            return False
        try:
            await asyncio.wait_for(self._disconnected_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def disconnect(self) -> None:
        client = self._client
        if client is None:
            return
        if self._subscribed and self._characteristic is not None and client.is_connected:
            try:
                await client.stop_notify(self._characteristic)
            except Exception as error:
                LOGGER.warning("No se pudo confirmar stop_notify: %s", error)
        self._subscribed = False
        if client.is_connected:
            await client.disconnect()
        if self._disconnected_event is not None:
            self._disconnected_event.set()
        LOGGER.info("Roadtrip Watch 4 JG74 desconectado")
