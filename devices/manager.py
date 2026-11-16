"""Loop BLE persistente, estado compartido y rate limiting para el JG74."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
import importlib.util
import logging
import os
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

from devices.normalizer import NormalizedReading
from devices.roadtrip_jg74 import (
    ROADTRIP_MODEL,
    BleUnavailableError,
    DeviceDisconnectedError,
    RoadtripJG74Adapter,
)


LOGGER = logging.getLogger(__name__)
STORE_INTERVAL_ENV = "ARES_BLE_STORE_INTERVAL_SECONDS"
DEFAULT_STORE_INTERVAL_SECONDS = 5.0
DEFAULT_TELEMETRY_TIMEOUT_SECONDS = 30.0


class DeviceManagerError(RuntimeError):
    pass


class DeviceSessionBusy(DeviceManagerError):
    pass


@dataclass(frozen=True)
class VerifiedSession:
    """Evidencia mínima que acompaña cada intento de persistencia."""

    astronaut_id: int
    device_id: str
    telemetry_confirmed: bool
    verified_physical: bool


StoreCallback = Callable[[NormalizedReading, VerifiedSession], Any]


def _positive_float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("%s no es numérico; se usará %.1f", name, default)
        return default
    if value <= 0:
        LOGGER.warning("%s debe ser positivo; se usará %.1f", name, default)
        return default
    return value


def _reading_document(reading: NormalizedReading) -> dict[str, Any]:
    result = asdict(reading)
    result["captured_at"] = reading.acquired_at
    heart_rate = reading.metrics.get("heart_rate")
    if heart_rate:
        result.update(
            metric="heart_rate",
            value=heart_rate["value"],
            unit=heart_rate["unit"],
            quality=heart_rate["quality"],
            heart_rate=heart_rate["value"],
        )
    return result


class DeviceManager:
    """Mantiene una sola sesión JG74 fuera de los threads de Flask."""

    def __init__(
        self,
        *,
        adapter_factory: Callable[..., Any] = RoadtripJG74Adapter,
        store_callback: StoreCallback | None = None,
        store_interval_seconds: float | None = None,
        telemetry_timeout_seconds: float = DEFAULT_TELEMETRY_TIMEOUT_SECONDS,
        max_reconnect_attempts: int = 3,
        reconnect_base_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter_factory = adapter_factory
        self.store_callback = store_callback
        self.store_interval_seconds = (
            _positive_float_from_env(
                STORE_INTERVAL_ENV,
                DEFAULT_STORE_INTERVAL_SECONDS,
            )
            if store_interval_seconds is None
            else float(store_interval_seconds)
        )
        if self.store_interval_seconds <= 0:
            raise ValueError("store_interval_seconds debe ser positivo")
        if telemetry_timeout_seconds <= 0:
            raise ValueError("telemetry_timeout_seconds debe ser positivo")
        if max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts no puede ser negativo")
        if reconnect_base_seconds < 0:
            raise ValueError("reconnect_base_seconds no puede ser negativo")
        self.telemetry_timeout_seconds = float(telemetry_timeout_seconds)
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_base_seconds = float(reconnect_base_seconds)
        self.clock = clock

        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._session_future: Future[Any] | None = None
        self._stop_event: threading.Event | None = None
        self._adapter: Any = None
        self._latest: dict[str, Any] | None = None
        self._device_id: str | None = None
        self._astronaut_id: int | None = None
        self._last_persisted_monotonic: float | None = None
        self._last_received_monotonic: float | None = None
        self._connected_monotonic: float | None = None
        self._received_in_connection = False

        ble_available = importlib.util.find_spec("bleak") is not None
        if store_callback is None:
            state = "integration_unavailable"
            message = "La persistencia wearable no está configurada."
        elif not ble_available:
            state = "ble_unavailable"
            message = "Bleak no está instalado; las demás funciones siguen disponibles."
        else:
            state = "disconnected"
            message = "JG74 desconectado."
        self._status: dict[str, Any] = {
            "model": ROADTRIP_MODEL,
            "state": state,
            "message": message,
            "connected": False,
            "telemetry_confirmed": False,
            "verified_physical": False,
            "last_received_at": None,
            "device_id": None,
            "astronaut_id": None,
            "reconnect_attempt": 0,
            "last_error": None,
            "ble_available": ble_available,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._expire_stale_telemetry_locked()
            result = deepcopy(self._status)
            result["latest"] = deepcopy(self._latest)
            result["store_interval_seconds"] = self.store_interval_seconds
            result["telemetry_timeout_seconds"] = self.telemetry_timeout_seconds
            return result

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._latest)

    def connect(self, *, device_id: str, astronaut_id: int) -> dict[str, Any]:
        if self.store_callback is None:
            raise DeviceManagerError("La persistencia wearable no está configurada")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id es obligatorio")
        if not isinstance(astronaut_id, int) or isinstance(astronaut_id, bool) or astronaut_id <= 0:
            raise ValueError("astronaut_id debe ser un entero positivo")

        self._ensure_loop()
        assert self._loop is not None
        with self._lock:
            if self._session_future is not None and not self._session_future.done():
                raise DeviceSessionBusy("Ya existe una sesión wearable activa")
            self._device_id = device_id.strip()
            self._astronaut_id = astronaut_id
            self._stop_event = threading.Event()
            self._last_persisted_monotonic = None
            self._last_received_monotonic = None
            self._connected_monotonic = None
            self._received_in_connection = False
            self._set_status_locked(
                state="scanning" if self._device_id.casefold() == "auto" else "connecting",
                message=(
                    "Buscando el Roadtrip Watch 4 JG74."
                    if self._device_id.casefold() == "auto"
                    else "Conectando con Roadtrip Watch 4 JG74."
                ),
                connected=False,
                telemetry_confirmed=False,
                verified_physical=False,
                last_received_at=None,
                device_id=self._device_id,
                astronaut_id=self._astronaut_id,
                reconnect_attempt=0,
                last_error=None,
                ble_available=True,
            )
            stop_event = self._stop_event
            self._session_future = asyncio.run_coroutine_threadsafe(
                self._connection_worker(
                    device_id=self._device_id,
                    astronaut_id=self._astronaut_id,
                    stop_event=stop_event,
                ),
                self._loop,
            )
        return self.status()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            self._set_status_locked(
                state="disconnected",
                message="Desconexión solicitada.",
                connected=False,
                telemetry_confirmed=False,
                verified_physical=False,
            )
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)
        return self.status()

    def shutdown(self) -> None:
        self.disconnect()
        future = self._session_future
        if future is not None and not future.done():
            try:
                future.result(timeout=5)
            except Exception:
                future.cancel()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._loop_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def _ensure_loop(self) -> None:
        if self._loop_thread is not None and self._loop_thread.is_alive():
            return
        self._loop_ready.clear()

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._loop_ready.set()
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

        self._loop_thread = threading.Thread(
            target=run_loop,
            name="ares-jg74-ble",
            daemon=True,
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=5):
            raise DeviceManagerError("No se pudo iniciar el loop BLE")

    async def accept_reading(self, reading: NormalizedReading) -> None:
        if not isinstance(reading, NormalizedReading):
            raise TypeError("DeviceManager requiere el NormalizedReading de Leandro")
        if reading.source != "smartwatch" or reading.provenance != "verified_device":
            raise ValueError("Solo se acepta telemetría smartwatch verificada")
        if not isinstance(reading.device_id, str) or not reading.device_id.strip():
            raise ValueError("La lectura verificada requiere device_id")
        if not isinstance(reading.astronaut_id, int) or isinstance(reading.astronaut_id, bool):
            raise ValueError("La lectura requiere astronaut_id entero")

        now = self.clock()
        first_valid = False
        should_persist = False
        previous_persisted: float | None = None
        with self._lock:
            if self._astronaut_id is not None and reading.astronaut_id != self._astronaut_id:
                raise ValueError("La lectura pertenece a otro astronauta")
            if self._device_id not in (None, "auto") and reading.device_id != self._device_id:
                raise ValueError("La lectura pertenece a otro dispositivo")
            if self._astronaut_id is None:
                self._astronaut_id = reading.astronaut_id
            if self._device_id in (None, "auto"):
                self._device_id = reading.device_id

            first_valid = not self._status.get("telemetry_confirmed", False)
            self._latest = _reading_document(reading)
            self._last_received_monotonic = now
            self._received_in_connection = True
            self._set_status_locked(
                state="receiving_telemetry",
                message="Telemetría cardíaca verificada recibida.",
                connected=True,
                telemetry_confirmed=True,
                verified_physical=True,
                last_received_at=reading.received_at,
                device_id=reading.device_id,
                astronaut_id=reading.astronaut_id,
                last_error=None,
                ble_available=True,
            )
            previous_persisted = self._last_persisted_monotonic
            should_persist = (
                previous_persisted is None
                or now - previous_persisted >= self.store_interval_seconds
            )
            if should_persist:
                self._last_persisted_monotonic = now
            verification = VerifiedSession(
                astronaut_id=reading.astronaut_id,
                device_id=reading.device_id,
                telemetry_confirmed=True,
                verified_physical=True,
            )

        LOGGER.info(
            "%s lectura cardíaca JG74 válida recibida",
            "Primera" if first_valid else "Nueva",
        )
        if should_persist:
            try:
                await asyncio.to_thread(self.store_callback, reading, verification)
            except Exception as error:
                LOGGER.error("No se pudo persistir la lectura JG74: %s", error)
                with self._lock:
                    self._last_persisted_monotonic = previous_persisted
                    self._status["last_error"] = f"{type(error).__name__}: {error}"

    async def _connection_worker(
        self,
        *,
        device_id: str,
        astronaut_id: int,
        stop_event: threading.Event,
    ) -> None:
        reconnect_attempt = 0
        terminal_state: str | None = None
        try:
            while not stop_event.is_set():
                self._received_in_connection = False
                self._last_received_monotonic = None
                with self._lock:
                    # En modo auto cada reconexión vuelve a autorizar el resultado
                    # del escaneo; Windows puede rotar identificadores BLE privados.
                    if device_id.casefold() == "auto":
                        self._device_id = "auto"
                    self._set_status_locked(
                        state=(
                            "scanning"
                            if reconnect_attempt == 0 and device_id.casefold() == "auto"
                            else "connecting" if reconnect_attempt == 0 else "reconnecting"
                        ),
                        message=(
                            "Buscando el Roadtrip Watch 4 JG74."
                            if reconnect_attempt == 0 and device_id.casefold() == "auto"
                            else "Conectando con Roadtrip Watch 4 JG74."
                            if reconnect_attempt == 0
                            else "Intentando reconectar el JG74."
                        ),
                        connected=False,
                        telemetry_confirmed=False,
                        verified_physical=False,
                        reconnect_attempt=reconnect_attempt,
                    )

                adapter = self.adapter_factory(
                    device_id=device_id,
                    astronaut_id=astronaut_id,
                    on_reading=self.accept_reading,
                    pair=True,
                )
                self._adapter = adapter
                error: Exception | None = None
                try:
                    await adapter.connect()
                    actual_device_id = getattr(adapter, "device_id", device_id)
                    with self._lock:
                        self._device_id = actual_device_id
                        self._status["device_id"] = actual_device_id
                    self._connected_monotonic = self.clock()
                    with self._lock:
                        if self._received_in_connection:
                            self._set_status_locked(
                                state="receiving_telemetry",
                                message="Telemetría cardíaca verificada recibida.",
                                connected=True,
                                telemetry_confirmed=True,
                                verified_physical=True,
                                reconnect_attempt=reconnect_attempt,
                                last_error=None,
                                ble_available=True,
                            )
                        else:
                            self._set_status_locked(
                                state="connected",
                                message="JG74 conectado; esperando la primera telemetría.",
                                connected=True,
                                telemetry_confirmed=False,
                                verified_physical=False,
                                reconnect_attempt=reconnect_attempt,
                                last_error=None,
                                ble_available=True,
                            )

                    while not stop_event.is_set():
                        if await adapter.wait_for_disconnect(timeout=0.5):
                            raise DeviceDisconnectedError(
                                "El JG74 perdió la conexión BLE"
                            )
                        with self._lock:
                            self._expire_stale_telemetry_locked()
                except BleUnavailableError as caught:
                    terminal_state = "ble_unavailable"
                    error = caught
                except asyncio.CancelledError:
                    raise
                except Exception as caught:
                    error = caught
                finally:
                    try:
                        await adapter.disconnect()
                    except Exception as disconnect_error:
                        LOGGER.warning(
                            "Error al cerrar el adaptador JG74: %s",
                            disconnect_error,
                        )
                    self._adapter = None

                if stop_event.is_set():
                    break
                if terminal_state == "ble_unavailable":
                    with self._lock:
                        self._set_status_locked(
                            state="ble_unavailable",
                            message=str(error),
                            connected=False,
                            telemetry_confirmed=False,
                            verified_physical=False,
                            last_error=f"{type(error).__name__}: {error}",
                            ble_available=False,
                        )
                    break

                if self._received_in_connection:
                    reconnect_attempt = 0
                reconnect_attempt += 1
                if reconnect_attempt > self.max_reconnect_attempts:
                    terminal_state = "error"
                    with self._lock:
                        self._set_status_locked(
                            state="error",
                            message="Se agotaron los intentos de reconexión JG74.",
                            connected=False,
                            telemetry_confirmed=False,
                            verified_physical=False,
                            reconnect_attempt=reconnect_attempt,
                            last_error=(
                                f"{type(error).__name__}: {error}" if error else None
                            ),
                        )
                    break

                delay = min(
                    self.reconnect_base_seconds * (2 ** (reconnect_attempt - 1)),
                    30.0,
                )
                with self._lock:
                    self._set_status_locked(
                        state="reconnecting",
                        message=f"Reconexión JG74 en {delay:.1f} segundos.",
                        connected=False,
                        telemetry_confirmed=False,
                        verified_physical=False,
                        reconnect_attempt=reconnect_attempt,
                        last_error=(
                            f"{type(error).__name__}: {error}" if error else None
                        ),
                    )
                await self._wait_or_stop(stop_event, delay)
        finally:
            if terminal_state not in ("error", "ble_unavailable"):
                with self._lock:
                    self._set_status_locked(
                        state="disconnected",
                        message="JG74 desconectado.",
                        connected=False,
                        telemetry_confirmed=False,
                        verified_physical=False,
                    )

    def _expire_stale_telemetry_locked(self) -> None:
        if (
            self._status.get("state") != "receiving_telemetry"
            or self._last_received_monotonic is None
        ):
            return
        if self.clock() - self._last_received_monotonic < self.telemetry_timeout_seconds:
            return
        self._set_status_locked(
            state="connected",
            message="JG74 conectado; la telemetría reciente expiró.",
            connected=True,
            telemetry_confirmed=False,
            verified_physical=False,
        )

    @staticmethod
    async def _wait_or_stop(stop_event: threading.Event, delay: float) -> None:
        deadline = time.monotonic() + delay
        while not stop_event.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _set_status_locked(self, **changes: Any) -> None:
        self._status.update(changes)
