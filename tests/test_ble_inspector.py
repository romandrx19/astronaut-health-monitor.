"""Pruebas sin hardware para el inspector BLE/GATT."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import ble_inspector


class FakeDevice:
    def __init__(self, address: str, name: str | None = None, rssi: int | None = None):
        self.address = address
        self.name = name
        self.rssi = rssi


class FakeAdvertisement:
    def __init__(self, local_name: str | None = None, rssi: int | None = None):
        self.local_name = local_name
        self.rssi = rssi


class FakeDescriptor:
    def __init__(self, uuid: str, description: str = "Unknown", handle: int = 9):
        self.uuid = uuid
        self.description = description
        self.handle = handle


class FakeCharacteristic:
    def __init__(
        self,
        uuid: str,
        *,
        properties: tuple[str, ...] = (),
        description: str = "Unknown",
        descriptors: tuple[FakeDescriptor, ...] = (),
        handle: int = 3,
    ):
        self.uuid = uuid
        self.properties = properties
        self.description = description
        self.descriptors = descriptors
        self.handle = handle


class FakeService:
    def __init__(
        self,
        uuid: str,
        characteristics: tuple[FakeCharacteristic, ...] = (),
        description: str = "Unknown",
        handle: int = 1,
    ):
        self.uuid = uuid
        self.characteristics = characteristics
        self.description = description
        self.handle = handle


class FakeClient:
    def __init__(self, services, *, failures=(), values=None):
        self.services = services
        self.failures = {ble_inspector.canonical_uuid(uuid) for uuid in failures}
        self.values = {
            ble_inspector.canonical_uuid(uuid): value
            for uuid, value in (values or {}).items()
        }

    async def read_gatt_char(self, characteristic):
        uuid = ble_inspector.canonical_uuid(characteristic.uuid)
        if uuid in self.failures:
            raise RuntimeError("lectura rechazada")
        return self.values.get(uuid, bytearray(b"\x01"))


class FakeNotificationClient(FakeClient):
    def __init__(self, services):
        super().__init__(services)
        self.stopped = False

    async def start_notify(self, characteristic, callback):
        callback(characteristic, bytearray(b"\x06\x4a"))

    async def stop_notify(self, characteristic):
        self.stopped = True


def entry(name: str | None, identifier: str, rssi: int | None = None):
    device = FakeDevice(identifier, name, rssi)
    return ble_inspector.ScanEntry(device, name, identifier, rssi)


class BleInspectorTests(unittest.TestCase):
    def test_bleak_no_disponible_devuelve_error_claro(self):
        with (
            patch.object(ble_inspector, "load_bleak", return_value=None),
            patch.object(sys, "argv", ["ble_inspector.py"]),
            self.assertLogs("ble_inspector", level="ERROR") as logs,
        ):
            result = asyncio.run(ble_inspector.main())
        self.assertEqual(result, ble_inspector.EXIT_DEPENDENCY)
        self.assertIn("Bleak no está instalado", "\n".join(logs.output))

    def test_pair_es_optativo_y_amplia_el_timeout(self):
        calls = []

        def client_factory(device, **kwargs):
            calls.append((device, kwargs))
            return object()

        ble_inspector.create_bleak_client(client_factory, "AA", request_pairing=False)
        ble_inspector.create_bleak_client(client_factory, "BB", request_pairing=True)

        self.assertEqual(calls[0], ("AA", {}))
        self.assertEqual(
            calls[1],
            (
                "BB",
                {
                    "pair": True,
                    "timeout": ble_inspector.PAIRING_TIMEOUT_SECONDS,
                },
            ),
        )

    def test_argumento_pair_se_activa_solo_si_se_solicita(self):
        self.assertFalse(ble_inspector.parse_args([]).pair)
        self.assertTrue(ble_inspector.parse_args(["--pair"]).pair)

    def test_lista_de_escaneo_vacia(self):
        self.assertEqual(ble_inspector.normalize_scan_results({}), [])
        with self.assertRaises(ble_inspector.SelectionError):
            ble_inspector.select_device([])

    def test_normaliza_nombre_rssi_y_no_oculta_dispositivo_sin_nombre(self):
        named = FakeDevice("AA", "nombre del sistema", -80)
        unnamed = FakeDevice("BB", None, None)
        results = ble_inspector.normalize_scan_results(
            {
                "AA": (named, FakeAdvertisement("JG74", -52)),
                "BB": (unnamed, FakeAdvertisement(None, -70)),
            }
        )
        self.assertEqual([(item.name, item.rssi) for item in results], [("JG74", -52), (None, -70)])

    def test_seleccion_automatica_de_unico_jg74(self):
        devices = [entry("Otro", "AA"), entry("roadtrip jg74", "BB")]
        selected = ble_inspector.select_device(
            devices,
            input_fn=lambda _prompt: self.fail("no debía pedir selección manual"),
        )
        self.assertEqual(selected.identifier, "BB")

    def test_seleccion_manual_por_indice(self):
        devices = [entry("Roadtrip", "AA"), entry(None, "BB")]
        selected = ble_inspector.select_device(
            devices,
            input_fn=lambda _prompt: "2",
        )
        self.assertEqual(selected.identifier, "BB")

    def test_device_id_permita_conexion_directa_si_no_aparece(self):
        selected = ble_inspector.select_device([], device_id="AA:BB:CC")
        self.assertEqual(selected.device, "AA:BB:CC")
        self.assertIsNone(selected.rssi)

    def test_generacion_del_json(self):
        report = ble_inspector.create_report(entry("JG74", "AA", -52))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            ble_inspector.save_report(report, path)
            stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["device"]["name"], "JG74")
        self.assertFalse(stored["connection"]["successful"])

    def test_creacion_del_directorio_de_diagnostico(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics" / "ble" / "report.json"
            self.assertFalse(path.parent.exists())
            ble_inspector.save_report(ble_inspector.create_report(), path)
            self.assertTrue(path.is_file())

    def test_servicio_heart_rate_y_medicion_estandar(self):
        measurement = FakeCharacteristic(
            "2A37",
            properties=("notify",),
            descriptors=(FakeDescriptor("2902"),),
        )
        client = FakeClient([FakeService("180D", (measurement,))])
        report = ble_inspector.create_report()
        asyncio.run(ble_inspector.inspect_gatt(client, report))
        self.assertTrue(report["standard_heart_rate_service_detected"])
        self.assertTrue(report["standard_heart_rate_measurement_detected"])
        self.assertEqual(report["services"][0]["description"], "Heart Rate")
        self.assertEqual(
            report["services"][0]["characteristics"][0]["description"],
            "Heart Rate Measurement",
        )

    def test_uuid_desconocido_no_recibe_semantica_inventada(self):
        uuid = "12345678-1234-5678-1234-56789abcdef0"
        self.assertEqual(
            ble_inspector.describe_uuid(uuid, "Vendor specific"),
            ble_inspector.UNKNOWN_DESCRIPTION,
        )
        client = FakeClient([FakeService(uuid)])
        report = ble_inspector.create_report()
        asyncio.run(ble_inspector.inspect_gatt(client, report))
        self.assertEqual(report["unknown_vendor_services"], [uuid])

    def test_fallo_de_lectura_no_aborta_el_resto(self):
        failing = FakeCharacteristic("12340001-1234-5678-1234-56789abcdef0", properties=("read",))
        succeeding = FakeCharacteristic("12340002-1234-5678-1234-56789abcdef0", properties=("read",))
        client = FakeClient(
            [FakeService("12340000-1234-5678-1234-56789abcdef0", (failing, succeeding))],
            failures=(failing.uuid,),
            values={succeeding.uuid: bytearray(b"\x06\x4a")},
        )
        report = ble_inspector.create_report()
        asyncio.run(ble_inspector.inspect_gatt(client, report))
        results = [
            item["read_result"]
            for item in report["services"][0]["characteristics"]
        ]
        self.assertFalse(results[0]["success"])
        self.assertTrue(results[1]["success"])
        self.assertEqual(results[1]["raw_hex"], "06 4A")

    def test_no_intenta_leer_si_la_propiedad_read_no_existe(self):
        characteristic = FakeCharacteristic("2A37", properties=("notify",))
        client = FakeClient([FakeService("180D", (characteristic,))])
        report = ble_inspector.create_report()
        asyncio.run(ble_inspector.inspect_gatt(client, report))
        saved = report["services"][0]["characteristics"][0]
        self.assertNotIn("read_result", saved)

    def test_notificacion_optativa_guarda_raw_y_detiene_suscripcion(self):
        characteristic = FakeCharacteristic("2A37", properties=("notify",))
        client = FakeNotificationClient([FakeService("180D", (characteristic,))])
        report = ble_inspector.create_report()
        succeeded = asyncio.run(
            ble_inspector.test_notification(client, "2A37", 0, report)
        )
        self.assertTrue(succeeded)
        self.assertTrue(client.stopped)
        self.assertEqual(
            report["notification_test"]["events"][0]["raw_hex"],
            "06 4A",
        )


if __name__ == "__main__":
    unittest.main()
