"""Diagnóstico BLE explícito y recepción opcional del perfil Heart Rate estándar."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from acquisition import ingest_from_adapter
from devices.base import DeviceAdapter, DeviceStatus, IntegrationUnavailable
from devices.normalizer import normalize_reading
from persistence.contracts import RepositoryUnavailable

# Bluetooth SIG Assigned Numbers. No identifica un protocolo propietario Roadtrip.
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def decode_heart_rate(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 2:
        raise ValueError("Notificación Heart Rate incompleta")
    flags = payload[0]
    if flags & 0xE0:
        raise ValueError("Flags reservados en el perfil Heart Rate")
    size = 2 if flags & 1 else 1
    offset = 1 + size
    if len(payload) < offset:
        raise ValueError("Valor Heart Rate truncado")
    heart_rate = int.from_bytes(payload[1:offset], "little")
    if flags & 8:
        offset += 2
        if len(payload) < offset:
            raise ValueError("Campo de energía truncado")
    remaining = len(payload) - offset
    if (flags & 16 and (remaining < 2 or remaining % 2)) or (not flags & 16 and remaining):
        raise ValueError("Longitud de intervalos RR no válida")
    quality = "poor" if flags & 4 and not flags & 2 else "unknown"
    return {"value": heart_rate, "unit": "bpm", "quality": quality}


async def discover(seconds=8, scanner=None):
    if scanner is None:
        from bleak import BleakScanner
        scanner = BleakScanner
    devices = await scanner.discover(timeout=seconds, return_adv=True)
    return [{"name": advertisement.local_name or device.name, "address": device.address,
             "rssi": advertisement.rssi, "service_uuids": list(advertisement.service_uuids),
             "manufacturer_ids": list(advertisement.manufacturer_data)}
            for device, advertisement in devices.values()]


def service_inventory(client):
    return [{"uuid": service.uuid, "description": service.description,
             "characteristics": [{"uuid": item.uuid, "description": item.description,
                                   "properties": list(item.properties)} for item in service.characteristics]}
            for service in client.services]


async def inspect_device(address, client_factory=None):
    if client_factory is None:
        from bleak import BleakClient
        client_factory = BleakClient
    async with client_factory(address) as client:
        return {"address": address, "connected": bool(client.is_connected), "services": service_inventory(client),
                "telemetry_confirmed": False}


class StandardHeartRateAdapter(DeviceAdapter):
    def __init__(self, client, address):
        self.client = client
        self.address = address
        self.reading = None

    def status(self):
        connected = bool(self.client.is_connected)
        confirmed = connected and self.reading is not None
        return DeviceStatus("Perfil BLE Heart Rate estándar", "receiving_telemetry" if confirmed else "connected_unconfirmed" if connected else "disconnected",
                            "Notificación estándar validada" if confirmed else "Recepción pendiente",
                            connected, confirmed, connected, self.reading.received_at if self.reading else None)

    def read(self, astronaut_id):
        if not self.client.is_connected or self.reading is None or self.reading.astronaut_id != astronaut_id:
            raise IntegrationUnavailable("Falta una notificación válida del dispositivo conectado y su astronauta")
        return self.reading


async def receive_heart_rate(address, astronaut_id, *, count=1, timeout=30, repository=None, client_factory=None):
    if client_factory is None:
        from bleak import BleakClient
        client_factory = BleakClient
    results, errors, storage_errors = [], [], []
    done = asyncio.Event()
    async with client_factory(address) as client:
        characteristic = client.services.get_characteristic(HEART_RATE_MEASUREMENT)
        if characteristic is None or "notify" not in characteristic.properties:
            raise IntegrationUnavailable("El dispositivo inspeccionado no expone notificaciones del perfil Heart Rate estándar")
        adapter = StandardHeartRateAdapter(client, address)

        def notification(sender, payload):
            if done.is_set() or not client.is_connected:
                return
            try:
                metric = decode_heart_rate(payload)
                adapter.reading = normalize_reading({"metrics": {"heart_rate": metric},
                    "observations": "Recepción de notificación BLE estándar. La fecha corresponde a su recepción; el paquete no incluye hora de captura."},
                    astronaut_id, source="smartwatch", device_id=f"ble:{address}", provenance="verified_device")
                saved, created = ingest_from_adapter(adapter, astronaut_id, f"ble:{uuid4()}", repository=repository)
                results.append({"id": saved["id"], "created": created})
                if len(results) >= count:
                    done.set()
            except (ValueError, LookupError, IntegrationUnavailable) as error:
                if len(errors) < 8:
                    errors.append(str(error))
            except RepositoryUnavailable as error:
                storage_errors.append(error)
                done.set()

        await client.start_notify(characteristic, notification)
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except TimeoutError:
            if not results:
                raise IntegrationUnavailable("No se recibió ninguna medición válida antes del tiempo límite") from None
        finally:
            if client.is_connected:
                await client.stop_notify(characteristic)
        if storage_errors:
            raise storage_errors[0]
        status = asdict(adapter.status())
    return {"measurements": results, "rejected": errors, "status_during_receipt": status,
            "connected_after_command": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "inspect", "receive-heart-rate"))
    parser.add_argument("--address", help="Dirección observada del dispositivo propio autorizado")
    parser.add_argument("--astronaut-id", type=int)
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120 or not 1 <= args.count <= 1000:
        parser.error("seconds debe estar entre 1 y 120; count entre 1 y 1000")
    if args.command != "scan" and not args.address:
        parser.error("inspect y receive-heart-rate requieren --address observado")
    if args.command == "receive-heart-rate" and (args.astronaut_id is None or args.astronaut_id <= 0):
        parser.error("La recepción requiere --astronaut-id positivo")
    try:
        if args.command == "scan":
            result = asyncio.run(discover(args.seconds))
        elif args.command == "inspect":
            result = asyncio.run(inspect_device(args.address))
        else:
            from runtime_paths import configure_runtime
            from persistence.provider import get_repository
            configure_runtime()
            repository = get_repository()
            repository.initialize("all")
            result = asyncio.run(receive_heart_rate(args.address, args.astronaut_id, count=args.count, timeout=args.seconds, repository=repository))
    except (ImportError, IntegrationUnavailable, RepositoryUnavailable, OSError) as error:
        parser.exit(1, f"Integración no disponible: {error}\n")
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "operation": args.command, "result": result}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        # Evita reemplazar un diagnóstico anterior por accidente.
        with args.output.open("x", encoding="utf-8") as output:
            output.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
