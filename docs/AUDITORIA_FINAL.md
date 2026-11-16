# Auditoría final — ARES HEALTH 2.0

Fecha: 15 de noviembre de 2026, America/Lima. Raíz de trabajo:
`C:\Users\LAPTOP\Downloads\astronaut-health-monitor\astronaut-health-monitor`.
La raíz Git es la carpeta padre. El proyecto figura sin seguimiento; no se
crearon commits ni se descartaron cambios anteriores.

## Protección y estado inicial

Antes de modificar se inspeccionaron módulos, rutas Flask, contrato, esquema,
vistas, reglas, dependencias y pruebas. Se ejecutaron las suites existentes:
134 casos Python y 59 JavaScript aprobados. Se creó `.venv` y se instalaron las
dependencias; `pip check` no encontró incompatibilidades.

Se utilizó **SQLite Backup API** para crear el respaldo coherente
`backups/finalization-20261115T223521Z/health.before-finalization.sqlite3`.
En ese directorio están `source.before-finalization.zip`, `audit.before.json`
con SHA-256 de archivos originales y conteos, y `schema.before.json`. Se preservaron
las bases, respaldos y archivos existentes. No se copió una SQLite activa a ciegas.

| Tabla / grupo | Antes | Después |
|---|---:|---:|
| Astronautas | 3 | 3 |
| Mediciones completas | 11127 | 11127 |
| Eventos | 549 | 549 |
| Expedientes / referencias | 0 / 0 | 0 / 0 |
| Paquetes / valores parciales | 0 / 0 | 0 / 0 |
| Exámenes / mediciones / archivos OCT | 0 / 0 / 0 | 0 / 0 / 0 |
| Sujetos científicos | 52 | 52 |
| Conjuntos | 4 | 4 |
| Días de observación | 2957 | 2957 |
| Signos vitales | 2940 | 2940 |
| Sueño | 207 | 207 |
| Nutrición | 2125 | 2125 |
| Hidratación | 2689 | 2689 |
| Laboratorio | 984 | 984 |
| Banderas de calidad | 2800 | 2800 |
| Manifiesto NASA | 1 | 1 |
| Tablas de usuario | 20 | 21 |

El único cambio a la base activa es la migración aditiva `simulation_state`, con
una fila pausada. `verify_preservation.py` compara todos los registros originales
con `EXCEPT` y SHA-256 por tabla: **todas las filas anteriores siguen idénticas**,
incluida `sqlite_sequence`. Integridad `ok` y cero errores de claves foráneas.
Evidencia local: `preservation.after.json`. Los perfiles y exámenes ficticios se
crearon solo en SQLite temporales, la base de demo bajo `backups` y una carpeta
de datos nueva de la prueba del ejecutable, nunca en la base principal.
La carpeta nueva del ejecutable fue conservada después en `native-runtime-preserved`
dentro del respaldo para que el próximo inicio no reutilice esos perfiles de prueba.

No se reimportó NASA sobre la base activa. El manifiesto `aether-nasa-full`
conserva el SHA-256 de origen
`ef53f8681b362492da3d5102fe6a62eb8f9dc8b9694dad46983904d91aa68c51` y 14758 filas
de origen. Los respaldos de construcción solo pertenecen a recursos nuevos.

## Problemas corregidos

- Cada pestaña podía generar muestras y pausar solo su propio intervalo. Ahora
  una transacción compartida controla lote, intervalo, pausa y reanudación.
- El punto de entrada web activaba debug. Ahora escucha en loopback y debug
  requiere configuración explícita; se validan Host/origen/escrituras locales.
- Cambiar de astronauta descartaba cambios médicos. Se conservan borradores por
  ID durante la sesión y se advierte antes de abandonar con cambios pendientes.
- El cierre de la ventana no garantizaba toda la limpieza si fallaba el arranque.
  Se añadió `finally`, cierre/espera del servidor, bloqueo y registro de errores.
- El tamaño fijo de escritorio podía exceder la pantalla. Se adapta el tamaño
  inicial sin modificar la composición de la telemetría.
- Faltaba una construcción reproducible y separación de datos mutables. Se
  generó un ejecutable con recursos y arranque científico limpio, sin base activa.
- Faltaba diagnóstico BLE reproducible. Se prepararon descubrimiento, inventario
  y recepción opcional del perfil SIG estándar, sin afirmar compatibilidad Roadtrip.

Se mantuvieron los motores de evaluación, reglas, expedientes, vistas NASA y OCT.
`database.py`, `config/demo_rules.json`, los CSS y `static/js/desktop.py` no fueron
modificados. No se añadieron diagnósticos ni umbrales clínicos desde estadísticas.

## Consultas verificadas

Se reutiliza `research_service.py`: conjuntos/procedencia desde `dataset_sources`,
participantes desde `subjects`, datos originales de las cinco familias, resúmenes,
series mediante `v_vital_daily`, `v_sleep_daily`, `v_nutrition_daily`,
`v_hydration_daily`, `v_lab_daily`, y banderas desde `quality_flags`.
Filtros `dataset`, `subject_id`, `from`, `to`, `metric`, `entity`, `flag`, `campaign`,
con `page` y `page_size` (máximo 200). Los valores se parametrizan; las tablas,
vistas y columnas se seleccionan de catálogos permitidos.

Rutas conservadas: `/api/research/datasets`, `/sources`, `/subjects`, `/summary`,
`/series`, `/quality-flags`, `/vital-signs`, `/sleep`, `/nutrition`, `/hydration`
y `/lab-results`. No fue necesario reemplazar estos servicios. La tabla nueva de
simulación se consulta/actualiza mediante el repositorio, con parámetros SQL.

## Estado de funcionalidades

| Funcionalidad | Clasificación | Alcance comprobado |
|---|---|---|
| Flask y controles locales | COMPLETADA Y PROBADA | HTTP, validación, errores y orígenes |
| SQLite y preservación | COMPLETADA Y PROBADA | Integridad, FK y filas originales idénticas |
| Telemetría y simulación | COMPLETADA Y PROBADA | Concurrencia entre hilos/procesos y UI nativa |
| Expedientes y borradores | COMPLETADA Y PROBADA | Guardado, aislamiento, reinicio y frontend |
| Alertas y orientación educativa | COMPLETADA Y PROBADA | Referencias, tendencias, calidad e idempotencia |
| Investigación NASA | COMPLETADA Y PROBADA | Consultas, filtros, ausencias y gráfico observado |
| OCT de demostración | COMPLETADA Y PROBADA | Importación, archivos, basal y comparación descriptiva |
| Escritorio Windows | COMPLETADA Y PROBADA | Construcción, ventana, recursos, persistencia y cierre |
| Diagnóstico/recepción SIG BLE | IMPLEMENTADA SIN PRUEBA COMPLETA | Pruebas con dobles; ningún dispositivo físico |
| Roadtrip físico | BLOQUEADA POR HARDWARE | Sin reloj, protocolo ni medición auténtica |
| Validación clínica y despliegue clínico | PENDIENTE | Fuera del alcance del prototipo educativo |

Las pruebas de software no acreditan precisión de sensores ni validez científica
de reglas clínicas. Las limitaciones y evidencias están en [pruebas](PRUEBAS_FINALES.md)
y [Roadtrip](INTEGRACION_ROADTRIP.md). El inventario siguiente diferencia archivos
existentes modificados y archivos nuevos; los respaldos y productos de construcción
se documentan aparte y no se distribuyen como datos activos.

<!-- INVENTORY_START -->
## Archivos existentes modificados

- `.gitignore`
- `app.py`
- `desktop.py`
- `docs/CONTRATO_INTEGRACION_DATOS.md`
- `docs/INTEGRACION_ROADTRIP.md`
- `integrate_nasa.py`
- `persistence/contracts.py`
- `persistence/schema.py`
- `persistence/sqlite.py`
- `README.md`
- `simulator.py`
- `static/js/app.js`
- `templates/index.html`
- `tests/fake_repository.py`
- `tests/test_acquisition.py`
- `tests/test_medical_frontend.cjs`
- `tests/test_nasa_research.py`
- `tests/test_oct.py`

## Archivos creados

- `ARESHealth.spec`
- `build_windows.ps1`
- `devices/ble_probe.py`
- `docs/ARQUITECTURA.md`
- `docs/AUDITORIA_FINAL.md`
- `docs/GUIA_DEMOSTRACION.md`
- `docs/INSTALACION_WINDOWS.md`
- `docs/PRUEBAS_FINALES.md`
- `local_security.py`
- `requirements-ble.txt`
- `requirements-build.txt`
- `requirements-lock-windows.txt`
- `runtime_paths.py`
- `scripts/create_demo.py`
- `scripts/package_release.py`
- `scripts/prepare_build_assets.py`
- `scripts/verify_demo.py`
- `scripts/verify_preservation.py`
- `static/js/simulation.js`
- `tests/test_ble_preparation.py`
- `tests/test_finalization.py`

<!-- INVENTORY_END -->
