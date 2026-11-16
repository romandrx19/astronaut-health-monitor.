"""Paquetes y clientes ficticios aislados. No accede a hardware Bluetooth."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devices.base import IntegrationUnavailable
from devices.ble_probe import HEART_RATE_MEASUREMENT, decode_heart_rate, discover, inspect_device, receive_heart_rate
from fake_repository import FakeRepository
from persistence.contracts import RepositoryUnavailable


class BlePreparationTests(unittest.TestCase):
    def test_standard_u8_u16_energy_and_rr_packets(self):
        self.assertEqual(decode_heart_rate(bytes([0, 72]))["value"], 72)
        self.assertEqual(decode_heart_rate(bytes([1, 0, 1]))["value"], 256)
        self.assertEqual(decode_heart_rate(bytes([24, 72, 1, 0, 0, 4]))["value"], 72)
        self.assertEqual(decode_heart_rate(bytes([4, 72]))["quality"], "poor")

    def test_malformed_packets_are_rejected(self):
        for packet in (b"", b"\x01\x48", b"\x08\x48", b"\x10\x48", b"\x10\x48\x00", b"\x20\x48", b"\x00\x48\x00"):
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode_heart_rate(packet)

    def test_discovery_only_returns_observed_advertisements(self):
        class Scanner:
            @staticmethod
            async def discover(**options):
                self.assertTrue(options["return_adv"])
                return {"test": (SimpleNamespace(name="Fake", address="test-address"), SimpleNamespace(local_name=None, rssi=-50, service_uuids=[], manufacturer_data={1: b"x"}))}
        result = asyncio.run(discover(scanner=Scanner))
        self.assertEqual(result[0]["address"], "test-address")
        self.assertEqual(result[0]["service_uuids"], [])

    def client_factory(self, packets, characteristic=True):
        item = SimpleNamespace(uuid=HEART_RATE_MEASUREMENT, description="Fixture", properties=["notify"])
        class Services(list):
            def get_characteristic(inner, uuid):
                return item if characteristic else None
        class Client:
            def __init__(inner, address):
                inner.is_connected = False
                inner.services = Services([SimpleNamespace(uuid="test-service", description="Fixture", characteristics=[item])])
            async def __aenter__(inner):
                inner.is_connected = True
                return inner
            async def __aexit__(inner, *error):
                inner.is_connected = False
            async def start_notify(inner, characteristic, callback):
                for packet in packets:
                    callback(item, packet)
            async def stop_notify(inner, characteristic):
                pass
        return Client

    def test_inventory_does_not_claim_telemetry(self):
        result = asyncio.run(inspect_device("test", self.client_factory([])))
        self.assertFalse(result["telemetry_confirmed"])
        self.assertEqual(result["services"][0]["characteristics"][0]["uuid"], HEART_RATE_MEASUREMENT)

    def test_verified_ingestion_uses_existing_repository_contract(self):
        with tempfile.TemporaryDirectory() as root:
            repository = FakeRepository(Path(root))
            result = asyncio.run(receive_heart_rate("test", 1, repository=repository, client_factory=self.client_factory([bytes([0, 72])]), timeout=.1))
            self.assertEqual(len(result["measurements"]), 1)
            reading = repository.get_history(1)[0]
            self.assertEqual((reading["source"], reading["provenance"], reading["heart_rate"]), ("smartwatch", "verified_device", 72))
            self.assertIsNone(reading["spo2"])
            self.assertFalse(result["connected_after_command"])

    def test_unknown_profile_or_no_valid_measurement_never_stores_readings(self):
        with tempfile.TemporaryDirectory() as root:
            repository = FakeRepository(Path(root))
            for factory in (self.client_factory([], characteristic=False), self.client_factory([b"bad-packet"])):
                with self.assertRaises(IntegrationUnavailable):
                    asyncio.run(receive_heart_rate("test", 1, repository=repository, client_factory=factory, timeout=.01))
            self.assertEqual(repository.get_history(1), [])

    def test_storage_failure_is_reported_without_waiting_for_timeout(self):
        with patch("devices.ble_probe.ingest_from_adapter", side_effect=RepositoryUnavailable("Fixture: almacenamiento no disponible")):
            with self.assertRaises(RepositoryUnavailable):
                asyncio.run(receive_heart_rate("test", 1, client_factory=self.client_factory([bytes([0, 72])]), timeout=.01))
