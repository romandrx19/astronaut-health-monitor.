# ARES HEALTH 2.0 — Prototipo educativo

Versión final 
La regresión final verificó **195 pruebas Python, 358 subtests y 61 pruebas
JavaScript**, además de los dominios de telemetría, expediente médico, eventos,
NASA Research, OCT, persistencia y empaquetado Windows. No es un sistema clínico.

El JG74 se integra como una fuente adicional mediante Bleak. Sin una sesión activa
su estado normal es `disconnected`; si Bleak no está instalado se informa
`ble_unavailable` sin impedir que arranque el resto de ARES HEALTH. Una conexión
BLE solo pasa a `receiving_telemetry` después de recibir una notificación 0x2A37
válida y marcar `telemetry_confirmed=true` y `verified_physical=true`.

- [Auditoría, protección de datos y archivos cambiados](docs/AUDITORIA_FINAL.md).
- [Arquitectura y simulación compartida](docs/ARQUITECTURA.md).
- [Pruebas ejecutadas y límites](docs/PRUEBAS_FINALES.md).
- [Instalación, persistencia y construcción Windows](docs/INSTALACION_WINDOWS.md).
- [Demostración reproducible en base aislada](docs/GUIA_DEMOSTRACION.md).
- [Arquitectura BLE y persistencia JG74](docs/INTEGRACION_ROADTRIP.md).
- [Prueba física manual JG74](docs/JG74_HARDWARE_TEST.md).

La fuente final está en la carpeta `ARESHealth-integrated`. Los proyectos originales
de Leandro y Daniel se conservan separados como referencia y no deben usarse como
la entrega fusionada. Los ZIP y el directorio `release/` no se versionan: pueden
generarse de forma reproducible con `build_windows.ps1` y
`scripts/package_release.py`. Al distribuir Windows deben conservarse juntos
`ARESHealth.exe` y su carpeta `_internal`. Los documentos de fases anteriores
describen su etapa histórica; para el estado actual prevalecen este README,
`INTEGRACION_ROADTRIP.md` y `JG74_HARDWARE_TEST.md`.

La Fase 1 de ARES HEALTH 2.0 incorpora registros manuales, migración conservadora,
filtro de origen y lanzador pywebview. Consulta [el plan, los cambios y las pruebas](docs/FASE1.md).

El **historial médico individual** añade notas y referencias biométricas opcionales por
astronauta en la sección 05 del panel. Consulta [la implementación, API y pruebas](docs/HISTORIAL_MEDICO.md).
Los expedientes comienzan vacíos y se guardan solo al pulsar **Guardar expediente**.

La **Fase 2** compara las lecturas con esas referencias, analiza tendencias de
demostración y conserva episodios de eventos sin duplicarlos en cada consulta.
Consulta [las reglas, la API y el recorrido de prueba](docs/FASE2.md).

La **Fase 3** añadió lecturas manuales parciales, fechas, observaciones, origen y
protección frente a reintentos duplicados. Su placeholder Roadtrip fue reemplazado
en la versión final por `RoadtripJG74Adapter` y `DeviceManager`: conexión BLE real
opcional, verificación de 0x180D/0x2A37, notificaciones, reconexión, `latest`,
persistencia automática y rate limiting. La validación automatizada no sustituye
la [prueba física del reloj](docs/JG74_HARDWARE_TEST.md).

La **Fase 4** organiza un Centro de control que ocupa la ventana y un menú secundario de
módulos. Conserva el astronauta y los borradores al navegar, redimensiona el gráfico
al mostrarlo y mantiene visibles las prioridades confirmadas. Consulta
[la navegación, distribución y pruebas realizadas](docs/DASHBOARD.md).

La **Fase 5** agrega **07 / SALUD OCULAR — OCT**, con exámenes por astronauta y ojo,
imágenes PNG/JPEG/TIFF, importación de resultados JSON, referencia basal explícita,
comparación longitudinal y resumen ocular en el expediente médico. Su uso actual está
limitado al entorno local con datos de demostración. La guía incluye ejemplos sintéticos,
formatos, API, fuentes NASA/DICOM y el alcance de una futura conexión a equipos OCT.
[Guía del módulo OCT](docs/MODULO_OCT.md)

Aplicación educativa con Flask, SQLite y JavaScript. Incluye tres astronautas ficticios, mediciones dinámicas, historial, gráfico, panel de alertas y orientaciones educativas. **No es un dispositivo ni sistema clínico.**

El menú **08 · Investigación NASA** consulta los conjuntos históricos del equipo:
52 participantes separados de los tres astronautas, con filtros, gráficos diarios,
tablas, banderas de calidad y procedencia. Los valores ausentes se mantienen como
no disponibles. La [guía de integración científica](docs/INVESTIGACION_NASA.md)
documenta el respaldo, la fusión segura, los endpoints, las pruebas ejecutadas y
las limitaciones. Se utilizó la base `health_ares_nasa_preservada.sqlite3`.

El acceso a datos utiliza ahora `PersistenceRepository`, con SQLite como adaptador
predeterminado. Los servicios y las rutas conservan sus formatos y el frontend
existente. Para conectar la base definitiva, consulta el
[contrato de integración de datos](docs/CONTRATO_INTEGRACION_DATOS.md), que describe
DTO, transacciones, unidades, API y selección de una fábrica funcional mediante
`ARES_DATA_BACKEND` y `ARES_REPOSITORY_FACTORY`.

## Requisitos

- Python 3.10 o superior; la integración final se verificó con Python 3.15.2.
- Navegador actualizado.
- Windows 11, Bluetooth habilitado y Bleak para la prueba física JG74.
- Node.js únicamente si se ejecutan las pruebas JavaScript.

## Inicio (Windows PowerShell)
```powershell
cd ARESHealth-integrated
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```
Abre http://127.0.0.1:5000 . Para detener, `Ctrl+C`.

Para conectar el Roadtrip Watch 4 JG74 instala la dependencia BLE opcional. Este
archivo ya incluye las dependencias base de `requirements.txt`:

```powershell
python -m pip install -r requirements-ble.txt
```

Si PowerShell restringe la activación, puedes omitirla usando `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` y después `.\.venv\Scripts\python.exe app.py`.

Si VS Code marca `Import "flask" could not be resolved`, selecciona
`ARESHealth-integrated\.venv\Scripts\python.exe` mediante
**Python: Select Interpreter**. No hay que modificar `app.py` ni instalar Flask
globalmente.

Para escritorio instala `requirements-desktop.txt` y ejecuta
`.\.venv\Scripts\python.exe desktop.py`. Para reconstruir el ejecutable usa
`.\build_windows.ps1 -InstallDependencies`; después
`.\.venv\Scripts\python.exe scripts/package_release.py` prepara ambos ZIP limpios.

## Inicio (macOS/Linux)
```bash
cd ARESHealth-integrated
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Estructura
```
ARESHealth-integrated/
├── app.py                 # API HTTP y página inicial
├── database.py            # Fachada compatible de telemetría
├── persistence/           # Contrato, selección, adaptador SQLite y migraciones
├── science_service.py     # Referencias y reglas del proveedor configurado
├── scientific_rules.py    # Validación declarativa de reglas y sus fuentes
├── config/demo_rules.json # Reglas educativas con fuente, versión y validación
├── medical.py             # Expedientes y referencias individuales independientes
├── health_analysis.py     # Comparaciones y tendencias deterministas
├── health_events.py       # Episodios de eventos persistentes y deduplicación
├── acquisition.py         # Lecturas parciales, procedencia e idempotencia
├── devices/
│   ├── base.py             # Contrato síncrono conservado de Leandro
│   ├── normalizer.py       # NormalizedReading canónico de Leandro
│   ├── roadtrip_jg74.py    # Parser 0x2A37 y adaptador BLE especializado
│   └── manager.py          # Thread/event loop, reconexión, latest y rate limiting
├── wearable_service.py    # Inyección del repositorio y persistencia wearable verificada
├── tools/ble_inspector.py # Inspector BLE/GATT independiente
├── simulator.py           # Generación de datos sintéticos y carga inicial
├── alerts.py              # Reglas ilustrativas y orientaciones
├── requirements.txt       # Dependencias Python
├── requirements-ble.txt   # Dependencias base + Bleak opcional
├── templates/
│   └── index.html         # Estructura HTML del dashboard
├── static/
│   ├── css/style.css      # Diseño oscuro y componentes originales
│   ├── css/dashboard.css  # Pantalla completa y distribución responsive
│   ├── js/navigation.js  # Ocho vistas, menú y resúmenes persistentes
│   ├── js/simulation.js  # Estado de simulación compartido con el backend
│   └── js/app.js          # Fetch, interacciones y gráfico Canvas
├── docs/
│   ├── GUIA.md            # Integración, pruebas y errores habituales
│   ├── INTEGRACION_ROADTRIP.md
│   └── JG74_HARDWARE_TEST.md
└── health.sqlite3         # Aparece automáticamente al primer inicio
```

## API
| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/` | Interfaz HTML |
| GET | `/api/health` | Estado del servidor |
| GET | `/api/astronauts` | Perfiles, última lectura y alertas |
| GET | `/api/astronauts/1/history?limit=36&source=smartwatch` | Lecturas recientes; `source` admite manual, simulation/simulated o smartwatch |
| GET | `/api/astronauts/1/medical` | Expediente individual, inicialmente vacío |
| POST | `/api/astronauts/1/medical` | Valida y guarda notas y referencias en una transacción |
| GET | `/api/astronauts/1/assessment` | Evaluación actual; registra episodios nuevos o actualiza los abiertos |
| GET | `/api/astronauts/1/events` | Historial de episodios; filtros metric, status y limit |
| POST | `/api/astronauts/1/measurements/manual` | Registra uno o varios indicadores con fecha y observaciones |
| GET | `/api/astronauts/1/measurements` | Historial combinado; origen canónico y metadatos |
| GET | `/api/devices/status` | Estado de conexión y verificación del JG74 |
| POST | `/api/devices/connect` | Inicia JG74 con `device_id` y `astronaut_id` entero |
| POST | `/api/devices/disconnect` | Detiene la sesión y desconecta limpiamente |
| GET | `/api/devices/latest` | Última notificación JG74 válida en memoria |
| GET | `/api/rules` | Catálogo de reglas, fuentes, versiones y validación declarada |
| GET/POST | `/api/simulation` | Consulta/cambia el estado global con JSON `{"running":true}` o `false` |
| POST | `/api/simulate` | Solicita un lote de tres lecturas; 201 si creó, 200 si no corresponde |

Conexión JG74 por descubrimiento automático:

```powershell
$body = @{ device_id = 'auto'; astronaut_id = 1 } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:5000/api/devices/connect' `
  -ContentType 'application/json' -Body $body
```

`astronaut_id` debe ser entero y existir en el repositorio configurado. `connected`
solo confirma el enlace BLE; no equivale a telemetría verificada. Las notificaciones
válidas actualizan `latest` continuamente, mientras SQLite almacena como máximo una
lectura por `ARES_BLE_STORE_INTERVAL_SECONDS` —cinco segundos de forma predeterminada—.

La simulación comienza pausada en bases nuevas. **Reanudar simulación** activa la
demostración para todas las ventanas que comparten base. Una transacción SQLite
limita las solicitudes automáticas a un lote de tres lecturas cada cinco segundos,
aunque haya varias pestañas o procesos. Pausar conserva las consultas y el historial.
Sin clientes abiertos no hay generación en segundo plano ni recuperación de ciclos.
**Generar lectura** solicita un lote explícito incluso estando pausada, respetando
el mismo intervalo. Elegir manual/smartwatch o guardar desde el formulario manual
pausa la generación compartida; Reanudar la activa explícitamente como demo.
El estado persiste entre reinicios. Recarga pestañas antiguas después de actualizar.

El selector de origen solo controla la visualización y las consultas. Un JG74
conectado continúa guardando automáticamente aunque se muestre otro origen. Consulta
la [arquitectura Roadtrip](docs/INTEGRACION_ROADTRIP.md) y la
[prueba física](docs/JG74_HARDWARE_TEST.md).

## Verificar API desde PowerShell
```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health
Invoke-RestMethod http://127.0.0.1:5000/api/astronauts
Invoke-RestMethod -Method Post http://127.0.0.1:5000/api/simulate
Invoke-RestMethod 'http://127.0.0.1:5000/api/astronauts/1/history?limit=5'
```

## Conservar los datos
`health.sqlite3` contiene mediciones y expedientes; se conserva al reiniciar. No lo borres
para actualizar la aplicación. Para pruebas aisladas, configura `ARES_DB_PATH` con una
ruta distinta antes de iniciar Flask.

En código fuente la ruta predeterminada sigue siendo la raíz del proyecto. El
ejecutable utiliza `%LOCALAPPDATA%\ARESHealth\health.sqlite3`; `ARES_DB_PATH` y
`ARES_DATA_DIR` permiten cambiarla. OCT se guarda junto a la base, en
`.ares-oct-demo/<nombre-base>/`. El recurso científico limpio `bootstrap.sqlite3`
solo se copia cuando falta la base de destino; nunca reemplaza una existente.
En una base vacía se preparan 24 lecturas simuladas por astronauta, identificadas
como tales. Los expedientes y OCT nuevos permanecen vacíos.

Los borradores médicos se recuperan por astronauta durante la sesión; al cerrar
con cambios pendientes el navegador avisa. Hay que guardar para persistirlos.

## Demostración y pruebas

```powershell
.\.venv\Scripts\python.exe scripts/create_demo.py --directory backups/demo-presentacion
$env:ARES_DB_PATH = (Resolve-Path backups/demo-presentacion/health.demo.sqlite3).Path
.\.venv\Scripts\python.exe desktop.py
```

El script crea perfiles, referencias y exámenes exclusivamente ficticios en una
base nueva; rechaza reutilizar un destino existente. No altera la base principal.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/test_medical_frontend.cjs tests/test_research_frontend.cjs
.\.venv\Scripts\python.exe scripts/verify_demo.py
```

La regresión final completa produjo `195 passed, 358 subtests passed` en Python y
`61 passed` en JavaScript, sin hardware físico. El JG74 real se valida siguiendo
`docs/JG74_HARDWARE_TEST.md`; esa prueba no debe incluirse dentro de la suite
automatizada.

`requirements-lock-windows.txt` registra las versiones exactas del entorno probado
con Windows x64/Python 3.12, incluidas herramientas opcionales de construcción y BLE.
La distribución limpia excluye `.venv`, `.git`, cachés, respaldos y bases activas;
no se borran los originales. El servidor escucha solo en localhost y no activa
debug por defecto. `ARES_DEBUG=1` se reserva al desarrollo web. Sin autenticación
y autorización clínica, este prototipo no debe usarse con datos clínicos reales.

## Advertencia
Los valores, las anomalías y las reglas de `config/demo_rules.json` se han creado exclusivamente como material de desarrollo. No reflejan criterios médicos validados, datos reales de NASA ni recomendaciones clínicas. Para utilizarlo en un entorno operacional habría que validar criterios con profesionales médicos, garantizar integridad de sensores, trazabilidad y seguridad, y establecer un procedimiento de escalamiento.
