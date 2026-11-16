"""Inspector BLE/GATT independiente para el Roadtrip Watch 4 (JG74).

Esta herramienta solo descubre y registra la superficie GATT que el dispositivo
expone. No interpreta protocolos propietarios ni integra datos con ARES HEALTH.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence


LOGGER = logging.getLogger("ble_inspector")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = PROJECT_ROOT / "diagnostics" / "ble" / "jg74_ble_report.json"

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BLUETOOTH_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"
UNKNOWN_DESCRIPTION = "unknown / vendor-specific"

EXIT_OK = 0
EXIT_DEPENDENCY = 2
EXIT_SCAN = 3
EXIT_SELECTION = 4
EXIT_CONNECTION = 5
EXIT_REPORT = 6
PAIRING_TIMEOUT_SECONDS = 60.0


# Etiquetas Bluetooth SIG comunes que son útiles durante el descubrimiento. Las
# descripciones que Bleak ya conoce también se conservan; nunca se asigna una
# semántica a un UUID que ambas fuentes desconozcan.
KNOWN_UUID_DESCRIPTIONS = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    HEART_RATE_SERVICE_UUID: "Heart Rate",
    "0000180f-0000-1000-8000-00805f9b34fb": "Battery Service",
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00002a04-0000-1000-8000-00805f9b34fb": (
        "Peripheral Preferred Connection Parameters"
    ),
    "00002a05-0000-1000-8000-00805f9b34fb": "Service Changed",
    "00002a19-0000-1000-8000-00805f9b34fb": "Battery Level",
    "00002a23-0000-1000-8000-00805f9b34fb": "System ID",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number String",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number String",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision String",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision String",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision String",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name String",
    HEART_RATE_MEASUREMENT_UUID: "Heart Rate Measurement",
    "00002a38-0000-1000-8000-00805f9b34fb": "Body Sensor Location",
    "00002a39-0000-1000-8000-00805f9b34fb": "Heart Rate Control Point",
    "00002900-0000-1000-8000-00805f9b34fb": (
        "Characteristic Extended Properties"
    ),
    "00002901-0000-1000-8000-00805f9b34fb": "Characteristic User Description",
    "00002902-0000-1000-8000-00805f9b34fb": (
        "Client Characteristic Configuration"
    ),
    "00002904-0000-1000-8000-00805f9b34fb": (
        "Characteristic Presentation Format"
    ),
}

_SHORT_UUID = re.compile(r"^(?:0x)?([0-9a-fA-F]{4}|[0-9a-fA-F]{8})$")
_UNKNOWN_LABELS = {
    "",
    "unknown",
    "unknown service",
    "unknown characteristic",
    "unknown descriptor",
    "vendor specific",
    "vendor-specific",
    UNKNOWN_DESCRIPTION,
}


@dataclass(frozen=True)
class BleakAPI:
    """Clases de Bleak cargadas de forma opcional."""

    scanner_class: Any
    client_class: Any


@dataclass(frozen=True)
class ScanEntry:
    """Dispositivo descubierto junto con sus metadatos de anuncio."""

    device: Any
    name: str | None
    identifier: str
    rssi: int | None
    advertisement_data: Any = None


class SelectionError(ValueError):
    """No se pudo elegir de forma segura un dispositivo."""


def load_bleak() -> BleakAPI | None:
    """Carga Bleak sin volverlo una dependencia del backend Flask."""

    try:
        bleak = importlib.import_module("bleak")
    except ImportError:
        return None
    return BleakAPI(
        scanner_class=bleak.BleakScanner,
        client_class=bleak.BleakClient,
    )


def create_bleak_client(
    client_class: Any,
    device: Any,
    *,
    request_pairing: bool = False,
) -> Any:
    """Crea el cliente y solicita pairing solo cuando el usuario lo autoriza."""

    if request_pairing:
        return client_class(
            device,
            pair=True,
            timeout=PAIRING_TIMEOUT_SECONDS,
        )
    return client_class(device)


def canonical_uuid(value: Any) -> str:
    """Normaliza UUIDs de 16/32 bits y UUIDs completos para compararlos."""

    text = str(value).strip().lower()
    match = _SHORT_UUID.fullmatch(text)
    if match:
        short = match.group(1).zfill(8)
        return f"{short}{BLUETOOTH_BASE_SUFFIX}"
    return text


def describe_uuid(value: Any, bleak_description: Any = None) -> str:
    """Devuelve una etiqueta SIG/Bleak o una clasificación explícita desconocida."""

    uuid = canonical_uuid(value)
    if uuid in KNOWN_UUID_DESCRIPTIONS:
        return KNOWN_UUID_DESCRIPTIONS[uuid]

    description = str(bleak_description or "").strip()
    if (
        description.casefold() not in _UNKNOWN_LABELS
        and description.casefold() != uuid.casefold()
    ):
        return description
    return UNKNOWN_DESCRIPTION


def _coerce_rssi(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_scan_results(raw_results: Any) -> list[ScanEntry]:
    """Adapta el resultado actual o heredado de ``BleakScanner.discover``."""

    if isinstance(raw_results, Mapping):
        values: Iterable[Any] = raw_results.values()
    else:
        values = raw_results or []

    entries_by_id: dict[str, ScanEntry] = {}
    for item in values:
        if isinstance(item, tuple) and len(item) == 2:
            device, advertisement = item
        else:
            device, advertisement = item, None

        identifier = str(
            getattr(device, "address", None)
            or getattr(device, "identifier", None)
            or device
        ).strip()
        if not identifier:
            continue

        advertised_name = getattr(advertisement, "local_name", None)
        device_name = getattr(device, "name", None)
        name = str(advertised_name or device_name).strip() if (advertised_name or device_name) else None
        advertised_rssi = (
            getattr(advertisement, "rssi", None)
            if advertisement is not None
            else None
        )
        rssi = _coerce_rssi(
            advertised_rssi
            if advertised_rssi is not None
            else getattr(device, "rssi", None)
        )
        entry = ScanEntry(
            device=device,
            name=name,
            identifier=identifier,
            rssi=rssi,
            advertisement_data=advertisement,
        )

        key = identifier.casefold()
        previous = entries_by_id.get(key)
        if previous is None or (
            entry.rssi is not None
            and (previous.rssi is None or entry.rssi > previous.rssi)
        ):
            entries_by_id[key] = entry

    return list(entries_by_id.values())


async def scan_devices(scanner_class: Any, scan_time: float) -> list[ScanEntry]:
    """Escanea BLE conservando RSSI y nombre del anuncio cuando están disponibles."""

    raw_results = await scanner_class.discover(
        timeout=scan_time,
        return_adv=True,
    )
    return normalize_scan_results(raw_results)


def display_devices(entries: Sequence[ScanEntry]) -> None:
    """Muestra todos los dispositivos, incluso los que no anuncian nombre."""

    LOGGER.info("Dispositivos BLE encontrados: %d", len(entries))
    for index, entry in enumerate(entries, start=1):
        name = entry.name or "(sin nombre)"
        candidate = (
            "  [candidato JG74/Roadtrip]"
            if any(token in name.casefold() for token in ("jg74", "roadtrip"))
            else ""
        )
        LOGGER.info(
            "[%d] %s%s\n    ID: %s\n    RSSI: %s",
            index,
            name,
            candidate,
            entry.identifier,
            entry.rssi if entry.rssi is not None else "no disponible",
        )


def select_device(
    entries: Sequence[ScanEntry],
    *,
    device_id: str | None = None,
    input_fn: Callable[[str], str] = input,
) -> ScanEntry:
    """Selecciona por ID, por coincidencia JG74 inequívoca o por índice."""

    if device_id:
        for entry in entries:
            if entry.identifier.casefold() == device_id.casefold():
                LOGGER.info("Dispositivo seleccionado por ID: %s", entry.identifier)
                return entry
        LOGGER.warning(
            "El ID %s no apareció durante el escaneo; se intentará la conexión directa.",
            device_id,
        )
        return ScanEntry(
            device=device_id,
            name=None,
            identifier=device_id,
            rssi=None,
        )

    if not entries:
        raise SelectionError("No se encontraron dispositivos BLE.")

    jg74_matches = [
        entry for entry in entries if entry.name and "jg74" in entry.name.casefold()
    ]
    if len(jg74_matches) == 1:
        selected = jg74_matches[0]
        LOGGER.info(
            "JG74 seleccionado automáticamente: %s (%s)",
            selected.name,
            selected.identifier,
        )
        return selected

    if len(jg74_matches) > 1:
        LOGGER.warning("Se encontraron varios dispositivos cuyo nombre contiene JG74.")
    else:
        roadtrip_matches = [
            entry
            for entry in entries
            if entry.name and "roadtrip" in entry.name.casefold()
        ]
        if roadtrip_matches:
            LOGGER.info(
                "Se encontraron %d candidato(s) Roadtrip; confirma el índice manualmente.",
                len(roadtrip_matches),
            )
        else:
            LOGGER.warning("No se encontró una coincidencia inequívoca para JG74.")

    while True:
        try:
            answer = input_fn(f"Selecciona un dispositivo [1-{len(entries)}]: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SelectionError("Selección cancelada.") from exc
        try:
            index = int(answer)
        except ValueError:
            LOGGER.warning("Introduce un índice numérico válido.")
            continue
        if 1 <= index <= len(entries):
            return entries[index - 1]
        LOGGER.warning("El índice debe estar entre 1 y %d.", len(entries))


def create_report(entry: ScanEntry | None = None) -> dict[str, Any]:
    """Crea el contrato base del reporte, todavía sin afirmar compatibilidad."""

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "device": (
            {
                "name": entry.name,
                "id": entry.identifier,
                "rssi": entry.rssi,
            }
            if entry
            else None
        ),
        "scan": {
            "successful": False,
            "devices_found": 0,
        },
        "connection": {
            "successful": False,
        },
        "services": [],
        "standard_heart_rate_service_detected": False,
        "standard_heart_rate_measurement_detected": False,
        "unknown_vendor_services": [],
    }


def save_report(report: Mapping[str, Any], path: Path = DEFAULT_REPORT_PATH) -> Path:
    """Crea el directorio de diagnóstico y guarda JSON UTF-8 legible."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _properties(characteristic: Any) -> list[str]:
    return [str(item).strip().lower() for item in (characteristic.properties or [])]


def raw_hex(data: bytes | bytearray | memoryview) -> str:
    return " ".join(f"{byte:02X}" for byte in bytes(data))


def display_raw(data: bytes | bytearray | memoryview) -> str:
    return " ".join(f"0x{byte:02X}" for byte in bytes(data)) or "(vacío)"


def _descriptor_report(descriptor: Any) -> dict[str, Any]:
    uuid = canonical_uuid(descriptor.uuid)
    return {
        "uuid": uuid,
        "description": describe_uuid(uuid, getattr(descriptor, "description", None)),
        "handle": getattr(descriptor, "handle", None),
    }


def _update_service_classification(report: dict[str, Any]) -> None:
    heart_service_found = False
    heart_measurement_found = False
    unknown_services: list[str] = []

    for service in report["services"]:
        if service["uuid"] == HEART_RATE_SERVICE_UUID:
            heart_service_found = True
            if any(
                characteristic["uuid"] == HEART_RATE_MEASUREMENT_UUID
                for characteristic in service["characteristics"]
            ):
                heart_measurement_found = True
        if service["description"] == UNKNOWN_DESCRIPTION:
            unknown_services.append(service["uuid"])

    report["standard_heart_rate_service_detected"] = heart_service_found
    report["standard_heart_rate_measurement_detected"] = heart_measurement_found
    report["unknown_vendor_services"] = unknown_services


async def inspect_gatt(client: Any, report: dict[str, Any]) -> None:
    """Enumera GATT y prueba únicamente lecturas declaradas como seguras."""

    report["services"] = []
    for service in client.services:
        service_uuid = canonical_uuid(service.uuid)
        service_description = describe_uuid(
            service_uuid,
            getattr(service, "description", None),
        )
        LOGGER.info(
            "Service:\n%s\n%s",
            service_uuid,
            service_description,
        )
        service_data: dict[str, Any] = {
            "uuid": service_uuid,
            "description": service_description,
            "handle": getattr(service, "handle", None),
            "characteristics": [],
        }

        for characteristic in service.characteristics:
            characteristic_uuid = canonical_uuid(characteristic.uuid)
            characteristic_description = describe_uuid(
                characteristic_uuid,
                getattr(characteristic, "description", None),
            )
            properties = _properties(characteristic)
            LOGGER.info(
                "  Characteristic:\n  %s\n  %s\n  Properties: %s",
                characteristic_uuid,
                characteristic_description,
                ", ".join(properties) if properties else "ninguna anunciada",
            )
            characteristic_data: dict[str, Any] = {
                "uuid": characteristic_uuid,
                "description": characteristic_description,
                "handle": getattr(characteristic, "handle", None),
                "properties": properties,
                "descriptors": [],
            }

            for descriptor in getattr(characteristic, "descriptors", ()) or ():
                descriptor_data = _descriptor_report(descriptor)
                characteristic_data["descriptors"].append(descriptor_data)
                LOGGER.info(
                    "    Descriptor: %s — %s (handle: %s)",
                    descriptor_data["uuid"],
                    descriptor_data["description"],
                    descriptor_data["handle"],
                )

            if "read" in properties:
                try:
                    value = await client.read_gatt_char(characteristic)
                except Exception as exc:  # BLE/WinRT expone varios tipos de error.
                    characteristic_data["read_result"] = {
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    LOGGER.warning(
                        "    La lectura de %s falló y la inspección continuará: %s: %s",
                        characteristic_uuid,
                        type(exc).__name__,
                        exc,
                    )
                else:
                    characteristic_data["read_result"] = {
                        "success": True,
                        "raw_hex": raw_hex(value),
                    }
                    LOGGER.info("    Raw: %s", display_raw(value))

            service_data["characteristics"].append(characteristic_data)
        report["services"].append(service_data)

    _update_service_classification(report)
    display_notify_characteristics(report)


def display_notify_characteristics(report: Mapping[str, Any]) -> None:
    notify_items = [
        characteristic
        for service in report["services"]
        for characteristic in service["characteristics"]
        if "notify" in characteristic["properties"]
    ]
    if not notify_items:
        LOGGER.info("Características con notify: ninguna.")
        return
    LOGGER.info("Características con notify:")
    for characteristic in notify_items:
        LOGGER.info(
            "  %s — %s",
            characteristic["uuid"],
            characteristic["description"],
        )


def find_characteristic(services: Iterable[Any], uuid: str) -> Any | None:
    target = canonical_uuid(uuid)
    for service in services:
        for characteristic in service.characteristics:
            if canonical_uuid(characteristic.uuid) == target:
                return characteristic
    return None


async def test_notification(
    client: Any,
    requested_uuid: str,
    duration: float,
    report: dict[str, Any],
) -> bool:
    """Escucha una sola característica solicitada y conserva bytes sin interpretar."""

    uuid = canonical_uuid(requested_uuid)
    notification_report: dict[str, Any] = {
        "uuid": uuid,
        "duration_seconds": duration,
        "success": False,
        "events": [],
    }
    report["notification_test"] = notification_report
    characteristic = find_characteristic(client.services, uuid)
    if characteristic is None:
        message = f"La característica {uuid} no fue encontrada."
        notification_report["error"] = message
        LOGGER.error(message)
        return False
    if "notify" not in _properties(characteristic):
        message = f"La característica {uuid} no anuncia la propiedad notify."
        notification_report["error"] = message
        LOGGER.error(message)
        return False

    def callback(sender: Any, data: bytearray) -> None:
        received_at = datetime.now().astimezone()
        sender_uuid = canonical_uuid(getattr(sender, "uuid", uuid))
        payload = raw_hex(data)
        notification_report["events"].append(
            {
                "received_at": received_at.isoformat(timespec="milliseconds"),
                "uuid": sender_uuid,
                "raw_hex": payload,
            }
        )
        LOGGER.info(
            "%s\nUUID: %s\nRAW: %s",
            received_at.strftime("%H:%M:%S.%f")[:-3],
            sender_uuid,
            payload or "(vacío)",
        )

    started = False
    try:
        LOGGER.info(
            "Escuchando notificaciones de %s durante %.1f segundos...",
            uuid,
            duration,
        )
        await client.start_notify(characteristic, callback)
        started = True
        await asyncio.sleep(duration)
        notification_report["success"] = True
        return True
    except Exception as exc:
        notification_report["error"] = f"{type(exc).__name__}: {exc}"
        LOGGER.error(
            "Falló la prueba de notificación. Error técnico: %s: %s",
            type(exc).__name__,
            exc,
        )
        LOGGER.debug("Detalle de la notificación", exc_info=True)
        return False
    finally:
        if started:
            try:
                await client.stop_notify(characteristic)
                LOGGER.info("Suscripción detenida limpiamente.")
            except Exception as exc:
                notification_report["stop_error"] = f"{type(exc).__name__}: {exc}"
                LOGGER.warning(
                    "No se pudo confirmar el cierre de la suscripción: %s: %s",
                    type(exc).__name__,
                    exc,
                )


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("debe ser un número") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return seconds


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Descubre y documenta servicios GATT del Roadtrip Watch 4 JG74 "
            "sin interpretar telemetría propietaria."
        )
    )
    parser.add_argument(
        "--scan-time",
        type=_positive_seconds,
        default=10.0,
        help="duración del escaneo BLE en segundos (predeterminado: 10)",
    )
    parser.add_argument(
        "--device-id",
        help="dirección o identificador BLE que se desea inspeccionar",
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help=(
            "solicita emparejamiento antes de conectar; puede requerir "
            "confirmación en Windows o en el reloj"
        ),
    )
    parser.add_argument(
        "--notify",
        metavar="UUID",
        help="prueba una única característica que anuncie notify",
    )
    parser.add_argument(
        "--notify-time",
        type=_positive_seconds,
        default=15.0,
        help="duración de la escucha de notify en segundos (predeterminado: 15)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="activa logging DEBUG, incluido el detalle técnico de Bleak",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def _write_report_or_log(report: Mapping[str, Any]) -> bool:
    try:
        path = save_report(report)
    except OSError as exc:
        LOGGER.error(
            "No fue posible guardar el reporte JSON. Error técnico: %s: %s",
            type(exc).__name__,
            exc,
        )
        return False
    LOGGER.info("Reporte JSON guardado en: %s", path)
    return True


async def main() -> int:
    """Ejecuta un ciclo completo de escaneo, inspección y desconexión."""

    args = parse_args()
    configure_logging(args.verbose)

    api = load_bleak()
    if api is None:
        LOGGER.error("Bleak no está instalado.")
        LOGGER.error(
            "Instala las dependencias Bluetooth del proyecto antes de ejecutar "
            "este inspector: python -m pip install -r requirements-ble.txt"
        )
        return EXIT_DEPENDENCY

    report = create_report()
    LOGGER.info("Escaneando dispositivos BLE durante %.1f segundos...", args.scan_time)
    try:
        entries = await scan_devices(api.scanner_class, args.scan_time)
    except Exception as exc:
        report["scan"]["error"] = f"{type(exc).__name__}: {exc}"
        LOGGER.error(
            "No fue posible iniciar el adaptador Bluetooth. Comprueba que Bluetooth "
            "está activado y que Windows permite el acceso a dispositivos BLE."
        )
        LOGGER.error("Error técnico: %s: %s", type(exc).__name__, exc)
        LOGGER.debug("Detalle del fallo de escaneo", exc_info=True)
        return EXIT_SCAN if _write_report_or_log(report) else EXIT_REPORT

    report["scan"] = {
        "successful": True,
        "devices_found": len(entries),
    }
    display_devices(entries)
    if not entries and not args.device_id:
        LOGGER.warning(
            "No se encontraron dispositivos BLE. Acerca el reloj, activa Bluetooth "
            "y vuelve a intentar el escaneo."
        )
        return EXIT_SELECTION if _write_report_or_log(report) else EXIT_REPORT

    try:
        selected = select_device(entries, device_id=args.device_id)
    except SelectionError as exc:
        LOGGER.error("No se seleccionó un dispositivo: %s", exc)
        return EXIT_SELECTION if _write_report_or_log(report) else EXIT_REPORT

    report["device"] = {
        "name": selected.name,
        "id": selected.identifier,
        "rssi": selected.rssi,
    }
    report["pairing_requested"] = args.pair

    client = None
    exit_code = EXIT_OK
    connected = False
    try:
        client = create_bleak_client(
            api.client_class,
            selected.device,
            request_pairing=args.pair,
        )
        LOGGER.info("Connecting...")
        await client.connect()
        connected = bool(client.is_connected)
        if not connected:
            raise RuntimeError("Bleak finalizó connect() sin una conexión activa")
        report["connection"]["successful"] = True
        if args.pair:
            report["pairing_succeeded"] = True
        LOGGER.info("Connected")

        try:
            await inspect_gatt(client, report)
        except Exception as exc:
            report["gatt_error"] = f"{type(exc).__name__}: {exc}"
            LOGGER.error(
                "La enumeración GATT no pudo completarse. Error técnico: %s: %s",
                type(exc).__name__,
                exc,
            )
            LOGGER.debug("Detalle del fallo GATT", exc_info=True)
            exit_code = EXIT_CONNECTION

        if args.notify and connected:
            if not await test_notification(
                client,
                args.notify,
                args.notify_time,
                report,
            ):
                exit_code = EXIT_CONNECTION
    except Exception as exc:
        if args.pair:
            report["pairing_succeeded"] = False
        report["connection"]["error"] = f"{type(exc).__name__}: {exc}"
        LOGGER.error(
            "No fue posible conectar con %s. Error técnico: %s: %s",
            selected.identifier,
            type(exc).__name__,
            exc,
        )
        LOGGER.debug("Detalle del fallo de conexión", exc_info=True)
        exit_code = EXIT_CONNECTION
    finally:
        if client is not None and (
            connected or bool(getattr(client, "is_connected", False))
        ):
            try:
                await client.disconnect()
                report["connection"]["disconnected_cleanly"] = True
                LOGGER.info("Disconnected")
            except Exception as exc:
                report["connection"]["disconnect_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                LOGGER.warning(
                    "La desconexión devolvió un error técnico: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                exit_code = EXIT_CONNECTION

    if not _write_report_or_log(report):
        return EXIT_REPORT
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
