"""Pruebas sin hardware de la integración Roadtrip Watch 4 JG74."""

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from devices.manager import DeviceManager, VerifiedSession
from devices.normalizer import NormalizedReading
from devices.roadtrip_jg74 import (
    HEART_RATE_MEASUREMENT_UUID,
    HEART_RATE_SERVICE_UUID,
    BleUnavailableError,
    DeviceDisconnectedError,
    DeviceVerificationError,
    RoadtripJG74Adapter,
    _load_bleak_client,
    normalize_heart_rate,
    parse_heart_rate_measurement,
)
from persistence.sqlite import SQLiteRepository
from fake_repository import FakeRepository
from wearable_service import store_verified_wearable_reading


class FakeCharacteristic:
    def __init__(self, uuid, properties=()):
        self.uuid = uuid
        self.properties = list(properties)


class FakeService:
    def __init__(self, uuid, characteristics=()):
        self.uuid = uuid
        self.characteristics = list(characteristics)


def fake_services(*, include_heart=True, include_measurement=True):
    characteristics = []
    if include_measurement:
        characteristics.append(
            FakeCharacteristic(HEART_RATE_MEASUREMENT_UUID, ("notify",))
        )
    services = []
    if include_heart:
        services.append(FakeService(HEART_RATE_SERVICE_UUID, characteristics))
    return services


class FakeBleakClient:
    def __init__(self, services=None, name="Roadtrip Watch 4 JG74"):
        self.services = services if services is not None else fake_services()
        self.name = name
        self.is_connected = False
        self.notification_callback = None
        self.notify_characteristic = None
        self.disconnected_callback = None
        self.factory_device = None
        self.factory_kwargs = None
        self.stopped = False

    def factory(self, device, **kwargs):
        self.factory_device = device
        self.factory_kwargs = kwargs
        self.disconnected_callback = kwargs.get("disconnected_callback")
        return self

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    async def start_notify(self, characteristic, callback):
        self.notify_characteristic = characteristic
        self.notification_callback = callback

    async def stop_notify(self, _characteristic):
        self.stopped = True

    async def read_gatt_char(self, _characteristic):
        return self.name.encode()

    async def emit(self, payload):
        result = self.notification_callback(self.notify_characteristic, bytearray(payload))
        if asyncio.iscoroutine(result):
            await result

    def force_disconnect(self):
        self.is_connected = False
        self.disconnected_callback(self)


class FakeDiscoveredDevice:
    def __init__(self, address="54:95:EB:XX:XX:XX", name="Roadtrip Watch 4 JG74"):
        self.address = address
        self.name = name


class FakeAdvertisement:
    local_name = "Roadtrip Watch 4 JG74"


class FakeScanner:
    @classmethod
    async def find_device_by_filter(cls, predicate, timeout):
        device = FakeDiscoveredDevice()
        return device if predicate(device, FakeAdvertisement()) else None


class HeartRateParserTests(unittest.TestCase):
    def test_real_payload_06_4b_is_75(self):
        self.assertEqual(parse_heart_rate_measurement(bytes.fromhex("06 4B")), 75)

    def test_real_payload_06_4c_is_76(self):
        self.assertEqual(parse_heart_rate_measurement(bytes.fromhex("06 4C")), 76)

    def test_uint16_little_endian(self):
        self.assertEqual(parse_heart_rate_measurement(b"\x01\x2c\x01"), 300)

    def test_invalid_payloads_are_rejected(self):
        for payload in (b"", b"\x06", b"\x01\x2c", b"\x00\x00"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_heart_rate_measurement(payload)
        with self.assertRaises(TypeError):
            parse_heart_rate_measurement("06 4B")

    def test_normalization_uses_leandro_contract_and_integer_astronaut(self):
        timestamp = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)
        reading = normalize_heart_rate(
            bytes.fromhex("06 4B"),
            astronaut_id=1,
            device_id="AA:BB",
            received_at=timestamp,
        )
        self.assertIsInstance(reading, NormalizedReading)
        self.assertIsInstance(reading.astronaut_id, int)
        self.assertEqual(reading.astronaut_id, 1)
        self.assertEqual(
            reading.metrics["heart_rate"],
            {"value": 75, "unit": "bpm", "quality": "good"},
        )
        self.assertEqual(reading.source, "smartwatch")
        self.assertEqual(reading.provenance, "verified_device")
        self.assertEqual(reading.device_id, "AA:BB")
        self.assertIn("0x2A37", reading.observations)
        with self.assertRaises(ValueError):
            normalize_heart_rate(b"\x06\x4b", astronaut_id="1", device_id="AA:BB")


class RoadtripAdapterTests(unittest.TestCase):
    @staticmethod
    def make_adapter(client, readings):
        return RoadtripJG74Adapter(
            device_id="AA:BB",
            astronaut_id=1,
            on_reading=readings.append,
            client_factory=client.factory,
        )

    def test_simulated_connection_verifies_180d_and_2a37(self):
        client = FakeBleakClient()
        adapter = self.make_adapter(client, [])

        async def scenario():
            await adapter.connect()
            self.assertTrue(adapter.is_connected)
            self.assertEqual(
                client.notify_characteristic.uuid.lower(),
                HEART_RATE_MEASUREMENT_UUID,
            )
            await adapter.disconnect()

        asyncio.run(scenario())
        self.assertTrue(client.stopped)
        self.assertTrue(client.factory_kwargs["pair"])

    def test_valid_and_invalid_notifications(self):
        client = FakeBleakClient()
        readings = []
        adapter = self.make_adapter(client, readings)

        async def scenario():
            await adapter.connect()
            await client.emit(b"\x06\x4b")
            await client.emit(b"")
            await adapter.disconnect()

        asyncio.run(scenario())
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].metrics["heart_rate"]["value"], 75)
        self.assertEqual(adapter.invalid_notification_count, 1)

    def test_auto_scan_remembers_real_ble_identifier(self):
        client = FakeBleakClient()
        adapter = RoadtripJG74Adapter(
            device_id="auto",
            astronaut_id=1,
            on_reading=lambda _reading: None,
            client_factory=client.factory,
            scanner_class=FakeScanner,
        )

        async def scenario():
            await adapter.connect()
            await adapter.disconnect()

        asyncio.run(scenario())
        self.assertEqual(adapter.device_id, "54:95:EB:XX:XX:XX")
        self.assertIsInstance(client.factory_device, FakeDiscoveredDevice)

    def test_missing_service_or_characteristic_is_rejected(self):
        cases = (
            FakeBleakClient(fake_services(include_heart=False)),
            FakeBleakClient(fake_services(include_measurement=False)),
        )
        for client in cases:
            with self.subTest(services=client.services), self.assertRaises(
                DeviceVerificationError
            ):
                asyncio.run(self.make_adapter(client, []).connect())

    def test_wrong_physical_name_is_rejected(self):
        client = FakeBleakClient(name="Otro reloj")
        with self.assertRaises(DeviceVerificationError):
            asyncio.run(self.make_adapter(client, []).connect())

    def test_physical_disconnection_is_observable(self):
        client = FakeBleakClient()
        adapter = self.make_adapter(client, [])

        async def scenario():
            await adapter.connect()
            client.force_disconnect()
            self.assertTrue(await adapter.wait_for_disconnect(0.1))

        asyncio.run(scenario())

    def test_bleak_absent_is_controlled(self):
        with patch(
            "devices.roadtrip_jg74.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            with self.assertRaises(BleUnavailableError):
                _load_bleak_client()


def manager_reading(value=75, *, seconds=0, astronaut_id=1, device_id="AA:BB"):
    timestamp = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc) + timedelta(
        seconds=seconds
    )
    return normalize_heart_rate(
        bytes((0x06, value)),
        astronaut_id=astronaut_id,
        device_id=device_id,
        received_at=timestamp,
    )


class ReconnectingAdapter:
    def __init__(self, owner, on_reading):
        self.owner = owner
        self.on_reading = on_reading
        self.is_connected = False
        self.device_id = "AA:BB"

    async def connect(self):
        self.owner.connect_calls += 1
        if self.owner.connect_calls == 1:
            raise DeviceDisconnectedError("fallo inicial")
        self.is_connected = True
        await self.on_reading(manager_reading())

    async def wait_for_disconnect(self, timeout):
        await asyncio.sleep(min(timeout, 0.02))
        return not self.is_connected

    async def disconnect(self):
        self.is_connected = False


class ReconnectingFactory:
    def __init__(self):
        self.connect_calls = 0

    def __call__(self, **kwargs):
        return ReconnectingAdapter(self, kwargs["on_reading"])


class DeviceManagerTests(unittest.TestCase):
    def test_rate_limiting_updates_latest_continuously(self):
        now = [0.0]
        stored = []

        def persist(item, session):
            stored.append((item, session))
            return {"id": f"test:{len(stored)}"}

        manager = DeviceManager(
            store_callback=persist,
            store_interval_seconds=5,
            clock=lambda: now[0],
        )
        asyncio.run(manager.accept_reading(manager_reading(75, seconds=0)))
        now[0] = 1.0
        asyncio.run(manager.accept_reading(manager_reading(76, seconds=1)))
        self.assertEqual(manager.latest()["value"], 76)
        self.assertEqual([item.metrics["heart_rate"]["value"] for item, _ in stored], [75])
        now[0] = 5.0
        asyncio.run(manager.accept_reading(manager_reading(76, seconds=5)))
        self.assertEqual(
            [item.metrics["heart_rate"]["value"] for item, _ in stored],
            [75, 76],
        )
        self.assertTrue(all(isinstance(session, VerifiedSession) for _, session in stored))
        self.assertTrue(stored[-1][1].telemetry_confirmed)
        self.assertTrue(stored[-1][1].verified_physical)

    def test_session_rejects_other_astronaut_or_device(self):
        manager = DeviceManager(store_callback=lambda _reading, _session: None)
        manager._astronaut_id = 1
        manager._device_id = "AA:BB"
        with self.assertRaises(ValueError):
            asyncio.run(manager.accept_reading(manager_reading(astronaut_id=2)))
        with self.assertRaises(ValueError):
            asyncio.run(manager.accept_reading(manager_reading(device_id="CC:DD")))

    def test_connected_is_not_receiving_until_notification(self):
        class SilentAdapter:
            def __init__(self):
                self.is_connected = False
                self.device_id = "AA:BB"

            async def connect(self):
                self.is_connected = True

            async def wait_for_disconnect(self, timeout):
                await asyncio.sleep(min(timeout, 0.02))
                return not self.is_connected

            async def disconnect(self):
                self.is_connected = False

        manager = DeviceManager(
            adapter_factory=lambda **_kwargs: SilentAdapter(),
            store_callback=lambda _reading, _session: None,
        )
        try:
            manager.connect(device_id="AA:BB", astronaut_id=1)
            deadline = time.monotonic() + 1
            while manager.status()["state"] != "connected" and time.monotonic() < deadline:
                time.sleep(0.01)
            status = manager.status()
            self.assertEqual(status["state"], "connected")
            self.assertTrue(status["connected"])
            self.assertFalse(status["telemetry_confirmed"])
            self.assertFalse(status["verified_physical"])
        finally:
            manager.shutdown()

    def test_telemetry_timeout_returns_to_connected(self):
        now = [0.0]
        manager = DeviceManager(
            store_callback=lambda _reading, _session: None,
            telemetry_timeout_seconds=5,
            clock=lambda: now[0],
        )
        asyncio.run(manager.accept_reading(manager_reading()))
        self.assertEqual(manager.status()["state"], "receiving_telemetry")
        now[0] = 6.0
        status = manager.status()
        self.assertEqual(status["state"], "connected")
        self.assertFalse(status["telemetry_confirmed"])
        self.assertFalse(status["verified_physical"])

    def test_reconnects_and_disconnects_cleanly(self):
        factory = ReconnectingFactory()
        manager = DeviceManager(
            adapter_factory=factory,
            store_callback=lambda _reading, _session: {"id": "fake"},
            max_reconnect_attempts=2,
            reconnect_base_seconds=0.01,
        )
        try:
            manager.connect(device_id="AA:BB", astronaut_id=1)
            deadline = time.monotonic() + 2
            while (
                manager.status()["state"] != "receiving_telemetry"
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertEqual(manager.status()["state"], "receiving_telemetry")
            self.assertGreaterEqual(factory.connect_calls, 2)
            stopped = manager.disconnect()
            self.assertEqual(stopped["state"], "disconnected")
        finally:
            manager.shutdown()


class WearablePersistenceTests(unittest.TestCase):
    @staticmethod
    def verification(reading, **changes):
        values = {
            "astronaut_id": reading.astronaut_id,
            "device_id": reading.device_id,
            "telemetry_confirmed": True,
            "verified_physical": True,
        }
        values.update(changes)
        return VerifiedSession(**values)

    def test_verified_reading_uses_existing_sqlite_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ares.sqlite3"
            repository = SQLiteRepository(path)
            repository.initialize("all")
            reading = manager_reading()
            result = store_verified_wearable_reading(
                reading,
                self.verification(reading),
                repository=repository,
            )
            self.assertTrue(result["created"])
            history = repository.get_history(1, source="smartwatch")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["heart_rate"], 75)
            self.assertEqual(history[0]["source"], "smartwatch")
            self.assertEqual(history[0]["provenance"], "verified_device")
            self.assertEqual(history[0]["device_id"], "AA:BB")
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertIn("reading_batches", tables)
            self.assertIn("reading_values", tables)
            self.assertNotIn("aether_wearable_readings", tables)

    def test_persistence_rejects_unverified_or_mismatched_session(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "ares.sqlite3")
            repository.initialize("all")
            reading = manager_reading()
            cases = (
                self.verification(reading, telemetry_confirmed=False),
                self.verification(reading, verified_physical=False),
                self.verification(reading, astronaut_id=2),
                self.verification(reading, device_id="CC:DD"),
            )
            for verification in cases:
                with self.subTest(verification=verification), self.assertRaises(ValueError):
                    store_verified_wearable_reading(
                        reading,
                        verification,
                        repository=repository,
                    )
            self.assertEqual(repository.get_history(1, source="smartwatch"), [])

    def test_unknown_integer_astronaut_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "ares.sqlite3")
            repository.initialize("all")
            reading = manager_reading(astronaut_id=999)
            with self.assertRaises(LookupError):
                store_verified_wearable_reading(
                    reading,
                    self.verification(reading),
                    repository=repository,
                )

    def test_flask_service_uses_exact_configured_repository_object(self):
        from app import create_app

        with tempfile.TemporaryDirectory() as directory:
            repository = FakeRepository(directory)
            application = create_app(
                {
                    "TESTING": True,
                    "ARES_DATA_BACKEND": "custom",
                    "ARES_REPOSITORY_FACTORY": lambda _config: repository,
                    "ARES_SEED_DEMO": False,
                }
            )
            service = application.extensions["ares_wearable_service"]
            self.assertIs(application.extensions["ares_repository"], repository)
            self.assertIs(service.repository, repository)
            self.assertIs(service.store.repository, repository)
            try:
                asyncio.run(service.manager.accept_reading(manager_reading()))
                history = repository.get_history(1, source="smartwatch")
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["source"], "smartwatch")
            finally:
                service.shutdown()

    def test_automatic_storage_and_smartwatch_history_ignore_visual_filter(self):
        from app import create_app

        with tempfile.TemporaryDirectory() as directory:
            repository = FakeRepository(directory)
            now = [0.0]
            managers = []

            def manager_factory(*, store_callback):
                manager = DeviceManager(
                    store_callback=store_callback,
                    store_interval_seconds=5,
                    clock=lambda: now[0],
                )
                managers.append(manager)
                return manager

            application = create_app(
                {
                    "TESTING": True,
                    "ARES_DATA_BACKEND": "custom",
                    "ARES_REPOSITORY_FACTORY": lambda _config: repository,
                    "ARES_DEVICE_MANAGER_FACTORY": manager_factory,
                    "ARES_SEED_DEMO": False,
                }
            )
            client = application.test_client()
            manager = managers[0]
            try:
                asyncio.run(manager.accept_reading(manager_reading(75, seconds=0)))
                # El origen visible es solo una consulta; no controla la adquisición.
                self.assertEqual(
                    client.get("/api/astronauts?source=manual").status_code,
                    200,
                )
                now[0] = 1.0
                asyncio.run(manager.accept_reading(manager_reading(76, seconds=1)))
                self.assertEqual(manager.latest()["value"], 76)
                now[0] = 5.0
                asyncio.run(manager.accept_reading(manager_reading(76, seconds=5)))

                response = client.get(
                    "/api/astronauts/1/history?source=smartwatch&limit=36"
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json), 2)
                self.assertTrue(
                    all(item["source"] == "smartwatch" for item in response.json)
                )
                self.assertEqual(
                    client.get(
                        "/api/astronauts/1/history?source=manual&limit=36"
                    ).json,
                    [],
                )
                self.assertEqual(manager.status()["state"], "receiving_telemetry")
            finally:
                application.extensions["ares_wearable_service"].shutdown()


class FakeApiManager:
    def __init__(self):
        self.disconnect_calls = 0
        self.latest_reading = None
        self.device_id = None
        self.astronaut_id = None
        self.state = "disconnected"

    def status(self):
        receiving = self.latest_reading is not None and self.state == "receiving_telemetry"
        return {
            "model": "Roadtrip Watch 4 JG74",
            "state": self.state,
            "message": "Estado de prueba",
            "connected": self.state in ("connected", "receiving_telemetry"),
            "telemetry_confirmed": receiving,
            "verified_physical": receiving,
            "last_received_at": None,
            "device_id": self.device_id,
            "astronaut_id": self.astronaut_id,
            "reconnect_attempt": 0,
            "last_error": None,
            "ble_available": True,
        }

    def connect(self, *, device_id, astronaut_id):
        self.device_id = device_id
        self.astronaut_id = astronaut_id
        self.state = "connecting"
        return self.status()

    def disconnect(self):
        self.disconnect_calls += 1
        self.state = "disconnected"
        return self.status()

    def latest(self):
        return dict(self.latest_reading) if self.latest_reading else None

    def shutdown(self):
        pass


class JG74ApiTests(unittest.TestCase):
    def setUp(self):
        from app import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.repository = FakeRepository(self.temp.name)
        self.manager = FakeApiManager()
        self.application = create_app(
            {
                "TESTING": True,
                "ARES_DATA_BACKEND": "custom",
                "ARES_REPOSITORY_FACTORY": lambda _config: self.repository,
                "ARES_DEVICE_MANAGER_FACTORY": lambda **_kwargs: self.manager,
                "ARES_SEED_DEMO": False,
            }
        )
        self.client = self.application.test_client()

    def tearDown(self):
        self.application.extensions["ares_wearable_service"].shutdown()
        self.temp.cleanup()

    def test_status_uses_real_manager_contract(self):
        response = self.client.get("/api/devices/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["devices"][0]["state"], "disconnected")
        self.assertFalse(response.json["verified_telemetry_available"])

    def test_connect_validates_integer_astronaut_and_returns_immediately(self):
        response = self.client.post(
            "/api/devices/connect",
            json={"device_id": "auto", "astronaut_id": 1},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["state"], "connecting")
        self.assertEqual(response.json["astronaut_id"], 1)
        invalid = self.client.post(
            "/api/devices/connect",
            json={"device_id": "AA:BB", "astronaut_id": 999},
        )
        self.assertEqual(invalid.status_code, 404)
        wrong_type = self.client.post(
            "/api/devices/connect",
            json={"device_id": "AA:BB", "astronaut_id": "1"},
        )
        self.assertEqual(wrong_type.status_code, 400)

    def test_disconnect_endpoint(self):
        response = self.client.post("/api/devices/disconnect")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"], "disconnected")
        self.assertEqual(self.manager.disconnect_calls, 1)

    def test_latest_endpoint_exposes_verified_reading(self):
        self.assertEqual(self.client.get("/api/devices/latest").status_code, 404)
        reading = manager_reading()
        self.manager.latest_reading = {
            "astronaut_id": reading.astronaut_id,
            "metrics": reading.metrics,
            "source": reading.source,
            "provenance": reading.provenance,
            "device_id": reading.device_id,
            "acquired_at": reading.acquired_at,
            "received_at": reading.received_at,
            "value": 75,
            "unit": "bpm",
        }
        self.manager.device_id = "AA:BB"
        self.manager.astronaut_id = 1
        self.manager.state = "receiving_telemetry"
        response = self.client.get("/api/devices/latest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["value"], 75)
        self.assertTrue(response.json["telemetry_confirmed"])
        self.assertTrue(response.json["verified_physical"])

    def test_visual_source_queries_never_disconnect_acquisition(self):
        self.assertEqual(self.client.get("/api/astronauts").status_code, 200)
        self.assertEqual(
            self.client.get("/api/astronauts?source=smartwatch").status_code,
            200,
        )
        self.assertEqual(self.manager.disconnect_calls, 0)

    def test_bleak_absent_does_not_break_flask(self):
        from app import create_app

        repository = FakeRepository(self.temp.name)
        with patch("devices.manager.importlib.util.find_spec", return_value=None):
            application = create_app(
                {
                    "TESTING": True,
                    "ARES_DATA_BACKEND": "custom",
                    "ARES_REPOSITORY_FACTORY": lambda _config: repository,
                    "ARES_SEED_DEMO": False,
                }
            )
        try:
            client = application.test_client()
            self.assertEqual(client.get("/api/health").status_code, 200)
            status = client.get("/api/devices/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json["devices"][0]["state"], "ble_unavailable")
        finally:
            application.extensions["ares_wearable_service"].shutdown()

if __name__ == "__main__":
    unittest.main()
