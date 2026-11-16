# Integración Roadtrip Watch 4 JG74

## Estado

ARES HEALTH integra el JG74 como una fuente adicional de frecuencia cardíaca BLE.
El flujo implementado usa Heart Rate Service `0x180D` y Heart Rate Measurement
`0x2A37`; la prueba física final sigue siendo manual y está descrita en
[JG74_HARDWARE_TEST.md](JG74_HARDWARE_TEST.md).

La conexión BLE no es una validación clínica. El módulo decodifica el valor estándar,
conserva su procedencia y no diagnostica, promedia ni completa métricas ausentes.

## Arquitectura

```text
Roadtrip Watch 4 JG74
        │  BLE / Bleak / 0x180D / 0x2A37
        ▼
RoadtripJG74Adapter
        │  payload → BPM → normalize_reading()
        ▼
DeviceManager
        │  sesión verificada + rate limiting
        ▼
VerifiedWearableStore
        │  repositorio inyectado por create_app()
        ▼
PersistenceRepository
        │
        ▼
SQLite ARES HEALTH
  reading_batches + reading_values + telemetry_measurements
```

No existe otra SQLite wearable ni la tabla `aether_wearable_readings`.

## Contratos conservados

- `devices/base.py` mantiene el `DeviceAdapter` síncrono `status()/read()` usado por
  los componentes anteriores.
- `devices/normalizer.py` mantiene el `NormalizedReading` oficial con
  `astronaut_id: int`, `metrics`, fechas ISO, origen, dispositivo, procedencia,
  observaciones y huella.
- `devices/roadtrip_jg74.py` es un componente asíncrono especializado. Su salida se
  crea exclusivamente mediante `normalize_reading()`.
- `devices/manager.py` posee el thread y event loop BLE, no Flask ni SQLite.
- `wearable_service.py` valida la sesión e inyecta el mismo objeto almacenado en
  `app.extensions["ares_repository"]`.

Una lectura válida queda identificada así:

```text
astronaut_id = entero existente
metric = heart_rate
unit = bpm
quality = good
source = smartwatch
provenance = verified_device
device_id = identificador BLE real
```

## Parser

`parse_heart_rate_measurement()` es una función pura. El bit 0 de flags selecciona
uint8 o uint16 little-endian:

```text
06 4B → 75 bpm
06 4C → 76 bpm
01 2C 01 → 300 bpm (uint16)
```

El parser no interpreta salud. Después del parseo se aplican los límites técnicos
existentes del normalizador Leandro; un valor fuera de ellos no se persiste.

## Estados

El API distingue:

```text
integration_unavailable
ble_unavailable
disconnected
scanning
connecting
connected
receiving_telemetry
reconnecting
error
```

`connected` no equivale a `receiving_telemetry`. Solo una notificación 0x2A37 válida
activa simultáneamente `telemetry_confirmed=true` y `verified_physical=true`.
Al vencer el timeout de telemetría, el enlace puede seguir conectado, pero esas dos
banderas vuelven a falso hasta una nueva notificación válida.

## Almacenamiento automático

Mientras una sesión verificada entrega datos, el manager actualiza `latest` con cada
notificación válida y guarda como máximo una lectura por intervalo. El valor por
defecto es 5 segundos y puede configurarse antes de iniciar la aplicación:

```powershell
$env:ARES_BLE_STORE_INTERVAL_SECONDS = '5'
```

El selector **Origen visible** solo filtra consultas. Elegir manual, simulación,
smartwatch o navegar a investigación NASA no controla la sesión BLE.

## API

| Método | Ruta | Función |
|---|---|---|
| GET | `/api/devices/status` | Estado, banderas de verificación y sesión |
| POST | `/api/devices/connect` | Inicia conexión sin bloquear el request |
| POST | `/api/devices/disconnect` | Solicita cierre y desconexión limpia |
| GET | `/api/devices/latest` | Última lectura válida en memoria |
| GET | `/api/astronauts/1/history?source=smartwatch` | Historial existente filtrado |

Ejemplo de conexión:

```json
{"device_id": "auto", "astronaut_id": 1}
```

El astronauta se comprueba en el repositorio configurado antes de iniciar la sesión.

## Dependencia opcional e inspector

Bleak se instala únicamente cuando se requiere Bluetooth:

```powershell
python -m pip install -r requirements-ble.txt
python -m tools.ble_inspector --scan-time 10
```

Si Bleak falta, Flask se importa y todas las funciones no wearable continúan. El
inspector es independiente de Flask y permite escanear, seleccionar, emparejar de
forma explícita, enumerar GATT y escuchar una característica con `notify`. Los
reportes locales pueden contener identificadores privados y están excluidos por
`.gitignore`.

## Pruebas

Las pruebas automatizadas usan clientes BLE y repositorios ficticios o SQLite
temporal; no requieren hardware. Cubren parser, notificaciones, conexión, errores,
Bleak ausente, reconexión, timeout, rate limiting, repositorio inyectado, API e
historial. La comprobación real del reloj no se automatiza.
