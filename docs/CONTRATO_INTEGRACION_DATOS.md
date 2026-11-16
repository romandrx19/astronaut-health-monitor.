# Contrato de integración de datos — ARES HEALTH 2.0

Este documento describe el contrato que consume el backend actual. No define el
esquema de la base definitiva. SQLite sigue siendo el adaptador predeterminado y
conserva las tablas, identificadores, expedientes, lecturas y archivos existentes.
Los ejemplos son sintéticos; las reglas incluidas son de demostración.

## 1. Dependencias y puntos de conexión

```text
templates/ + static/             Presentación y clientes HTTP existentes
             │ JSON
app.py + oct_service.py          Rutas, validación HTTP y sesión OCT
             │
medical.py / acquisition.py      Validación e ingestión
health_events.py                Evaluación y reconciliación de episodios
oct_repository.py               Operaciones OCT y sustitución explícita del basal
science_service.py              Referencias y catálogo de reglas del proveedor
             │ PersistenceRepository
persistence/provider.py         Selección e inyección del adaptador
             ├─ persistence/sqlite.py + schema.py  Implementación actual
             └─ fábrica del compañero              Implementación futura funcional
```

`health_analysis.py`, `oct_analysis.py`, `oct_domain.py`, `measurements.py` y la
normalización de dispositivos mantienen el cálculo y la validación independientes
del motor de datos. Los umbrales educativos están en `config/demo_rules.json`.
No se modificaron HTML, CSS ni JavaScript en esta separación.

El código nuevo debe depender de `persistence/contracts.py` y
`persistence/provider.py`, sin importar SQLite. Las funciones públicas anteriores
de `database.py`, `acquisition.py`, `medical.py`, `health_events.py` y
`oct_repository.py` se conservan como fachadas. `database.connect()` y
`acquisition.query_history(connection, ...)` son compatibilidad para mantenimiento
SQLite; las rutas y servicios no las utilizan.

## 2. Tipos, relaciones e identificadores

Los DTO son diccionarios Python y JSON, sin filas, conexiones, cursores, modelos
ORM, objetos `datetime` ni números decimales propios de un driver. Entregar
números JSON finitos; no entregar `NaN`, infinito ni booleanos como mediciones.
Los tipos de referencia están en `persistence/contracts.py`.

| Entidad | Campos y tipos |
|---|---|
| Astronauta | `id: int` positivo, `name`, `role`, `mission: str` |
| Medición completa anterior | `id: int`, `astronaut_id: int`, `captured_at: str`, siete indicadores numéricos, `source: str` |
| Lectura parcial | mismos indicadores, permitiendo `null`; `id: str` como `reading:42`, `reading_id: int`, `acquired_at`, `received_at`, `device_id`, `provenance`, `observations`, `request_id`, `quality`, `units` |
| Expediente | `astronaut_id: int`, `notes: str`, `updated_at: str \| null`, `references: object` indexado por indicador |
| Referencia individual | `min_value: number`, `max_value: number`, ambos finitos y mínimo menor que máximo |
| Evento persistido | `id`, `astronaut_id: int`, `measurement_id: int \| null`, `reading_id: int \| null`, `reading_key: str \| null`, indicador, tipo, severidad, valor, referencia, textos, fechas, estado, clave y contador |
| Examen OCT | `id`, `astronaut_id: int`, ojo, fecha, fase, equipo, protocolo, calidad, basal, revisión, notas, procedencia, fechas de auditoría y colecciones `measurements` / `files` |
| Medición OCT | `id`, `exam_id: int`, `metric: str`, `value: number`, `unit`, `anatomical_region`, `measurement_method: str` |
| Archivo OCT interno | `id`, `exam_id: int`, rutas privadas relativas, formato, nombre, checksum, tamaño, metadatos y fecha de carga |
| Regla | identificador, ámbito, indicador, unidad, condición, fuente, versión, validación, mensaje, orientación y habilitación |

Relaciones:

- `astronaut_id` relaciona lecturas, expediente, referencias, eventos y exámenes.
  Conservar los astronautas actuales **1, 2 y 3** y todos sus identificadores.
- `measurement_id` de un evento referencia una medición completa anterior.
  `reading_id` referencia un lote parcial. No establecer ambos a la vez.
  `measurement_key` de la evaluación y `reading_key` persistida distinguen
  `legacy:42` de `reading:42`. No interpretar un ID de historial como entero siempre.
- `exam_id` relaciona mediciones e imágenes OCT con su examen. Toda consulta de
  examen o archivo debe comprobar también el astronauta propietario.
- Un expediente inexistente se devuelve vacío y no se inserta por consultarlo.
  Una lista vacía significa que no hay registros; no inventar muestras ni basales.

Los IDs de lecturas anteriores y parciales pueden coincidir numéricamente en sus
tablas; el identificador público distingue ambos espacios. El frontend los trata
como identificadores, sin calcular con ellos.

## 3. Indicadores, unidades y valores ausentes

| Clave | Unidad canónica | Tipo de captura actual |
|---|---|---|
| `heart_rate` | `bpm` | entero |
| `spo2` | `%` | entero |
| `systolic` | `mmHg` | entero |
| `diastolic` | `mmHg` | entero |
| `glucose` | `mg/dL` | entero |
| `temperature` | `°C` | número |
| `sleep_hours` | `h` | número |

`devices/normalizer.py` contiene las unidades y `measurements.py` los límites
técnicos de captura. Esos límites detectan errores de entrada; no son criterios
clínicos. La persistencia no cambia ni convierte silenciosamente unidades.

Una lectura parcial conserva las siete claves de indicadores; las no capturadas
valen `null`. En `metrics`, `quality` y `units` solo aparecen los indicadores
capturados. **Cero no significa ausente.** Calidad: `unknown`, `good`, `poor` o
`invalid`. Una lectura de calidad mala o inválida se conserva, pero no se usa
para activar una evaluación de referencia o protocolo.

Referencias no configuradas: clave ausente en `references`, nunca un rango
predeterminado. Perfil vacío: `notes: ""`, `references: {}`, `updated_at: null`.
Guardar un expediente sustituye notas y referencias en conjunto; las referencias
omitidas se eliminan de ese expediente, según el comportamiento existente.
La validación completa ocurre antes de guardar y un error no debe modificar nada.

En OCT no se inventan unidad, región ni método ausentes. Se pueden almacenar
resultados incompletos, pero no se comparan hasta documentar su contexto. `um`
y `μm` se normalizan a `µm` al recibirlos; no hay conversión entre escalas.

## 4. Fechas, orden y procedencia

- Fechas y horas: texto ISO 8601 con zona explícita. Se acepta `Z` en la entrada;
  se normaliza a UTC (`+00:00`). Conservar las fechas históricas existentes.
- `captured_at` / `acquired_at`: instante de adquisición. `received_at`: recepción
  por el backend; `null` para registros anteriores donde no se conoce.
- Auditoría (`updated_at`, `detected_at`, `uploaded_at`, etc.): instantes UTC del
  backend. `reviewed_at` y `resolved_at` admiten `null` mientras no correspondan.
- Historial: seleccionar las últimas `limit` muestras y devolverlas de antigua a
  reciente. Desempatar con orden estable; SQLite usa instante, tipo y ID.
  Las fechas deben compararse como instantes, no solo como texto con zonas mixtas.
- Eventos: orden `detected_at DESC, id DESC`. Exámenes: `exam_date DESC, id DESC`.
- Filtros OCT `from` / `to`: días `YYYY-MM-DD`, inclusivos.

| Origen | Significado | Procedencias de lotes parciales |
|---|---|---|
| `simulated` | nombre histórico conservado en mediciones completas | no aplica |
| `simulation` | nombre canónico de simulación | `simulation`, `adapter_test` |
| `manual` | ingreso humano o importación identificada | `manual`, `imported` |
| `smartwatch` | telemetría física verificada por código local de confianza | `verified_device`, con `device_id` |

`reading_document()` añade `original_source` y entrega origen canónico. La API
histórica conserva el origen almacenado. Los filtros aceptan `simulation` y su
alias `simulated`. Las pruebas del adaptador se etiquetan como simulación.
Los clientes HTTP no pueden declarar una lectura manual como dispositivo.
Roadtrip continúa pendiente de identificar y verificar: no existe una ruta HTTP
que finja conexión, ni un protocolo Bluetooth inventado.

## 5. Métodos que debe implementar el adaptador

`PersistenceRepository` es un `Protocol` comprobable al configurar la aplicación.
La comprobación verifica presencia de métodos; el desarrollador debe verificar
también sus firmas, resultados y semántica con pruebas de integración.

| Método | Resultado / obligación |
|---|---|
| `initialize(scope="all")` | preparar almacenamiento propio de forma idempotente; scopes `all`, `telemetry`, `medical`, `events`, `oct`; no borrar ni reemplazar datos |
| `transaction(write=False)` | context manager que entrega una sesión con el mismo contrato; lectura coherente, commit al terminar y rollback ante error |
| `list_astronauts()` | lista ordenada por ID |
| `get_astronaut(astronaut_id)` | astronauta o `None` |
| `get_history(astronaut_id, limit=36, source=None)` | DTO combinados, orden antiguo→reciente, filtros canónicos |
| `has_measurements()` | booleano considerando completas y parciales |
| `get_simulation_state()` | documento independiente `{running: bool, last_generated_at: ISO UTC o None, revision: int}`; una fila global por base, inicialmente pausada |
| `save_simulation_state(state)` | persiste el estado completo dentro de la transacción activa, sin commit independiente |
| `add_measurements(items)` | inserción atómica de muestras completas de simulación; origen anterior por defecto `simulated` |
| `add_manual_measurement(sample)` | insertar y devolver exactamente la fila creada, sin consultar la última global |
| `store_reading(reading, request_id)` | recibe `NormalizedReading`; retorna `(Measurement, created: bool)`; ver idempotencia abajo |
| `get_medical_profile(astronaut_id)` | perfil con referencias; vacío si aún no está configurado |
| `save_medical_profile(id, notes, references, updated_at)` | sustitución atómica del perfil completo; devuelve el perfil guardado |
| `get_open_events(id, source)` | eventos del astronauta y origen con estado `active` o `unverified` |
| `insert_health_event(event)` | crear episodio con `occurrences=1`, `resolved_at=None`; devolver ID |
| `update_health_event(event_id, astronaut_id, changes)` | actualizar solo el evento del propietario; no decidir su estado clínico |
| `list_health_events(id, limit=50, metric=None, status=None)` | DTO persistidos, orientación y fuentes como listas Python |
| `list_oct_exams(astronaut_id)` | exámenes completos con mediciones y archivos internos, lectura coherente |
| `get_oct_exam(exam_id, astronaut_id)` | examen completo del propietario |
| `find_oct_request(request_key)` | examen interno previo o `None`, incluyendo hash de solicitud |
| `insert_oct_exam(exam, measurements)` | crear examen y mediciones; devolver ID; se llama dentro de transacción |
| `update_oct_exam(exam_id, astronaut_id, changes)` | actualizar examen del propietario y devolver DTO completo |
| `add_oct_files(exam_id, attachments)` | guardar metadatos de archivos ya preparados por el servicio; incluyen `uploaded_at` |
| `get_oct_file(file_id, astronaut_id)` | archivo interno comprobando propiedad mediante examen |
| `get_rule_definitions()` | lista de `RuleDefinition` serializable, sin funciones ni SQL |
| `oct_storage_root()` | `pathlib.Path` local privado para originales y vistas previas |

Todos los DTO devueltos deben ser documentos independientes: modificarlos en un
servicio no debe alterar registros persistidos sin llamar al método de escritura.
Las colecciones y metadatos JSON se decodifican dentro del adaptador.

**Transacciones y concurrencia.** Las llamadas a métodos de una sesión deben
participar en la misma transacción, sin abrir conexiones independientes ni hacer
commits intermedios. Una transacción de escritura serializa la reconciliación de
eventos, la idempotencia y la sustitución del basal incluso entre procesos. Las
escrituras individuales también deben ser atómicas. Las sesiones no son globales
ni se comparten entre solicitudes. SQLite usa `BEGIN IMMEDIATE` para escritura.

La simulación compartida requiere que `get_simulation_state()` y
`save_simulation_state()` participen en esa misma transacción junto con
`add_measurements()`. Actualiza los adaptadores personalizados con estos dos métodos
antes de usarlos con esta versión; el control de intervalo no debe implementarse
solo en memoria. Consulta [arquitectura](ARQUITECTURA.md) para el protocolo HTTP.

**Idempotencia de lecturas.** Unicidad de `(astronaut_id, source, request_id)`.
Misma clave y mismo `NormalizedReading.fingerprint`: devolver registro original
y `False`; distinta huella: lanzar `DuplicateRequest`. No actualizar la recepción
ni generar duplicados al reintentar. Clave HTTP de lecturas: 1–128 caracteres de
`A-Za-z0-9_.:-`. La normalización y generación de huella permanecen en el backend.

**Eventos.** El servicio decide claves, estados y contadores. El adaptador conserva
`condition_key`, `reading_key`, `measurement_id`, `reading_id`, `metric`,
`event_type`, `severity`, `current_value`, `reference_min/max`, `description`,
`guidance: list[str]`, `source`, `sources: list[str]`, `measurement_source`,
`captured_at`, `detected_at`, `last_seen_at`, `status`, `occurrences`, `resolved_at`.
En una actualización activa se modifican enlaces a lectura, severidad, valor,
descripción, orientación, fuentes, captura, última observación, contador y estado.
Una lectura repetida no incrementa el contador. Estados: `active`, `unverified`,
`resolved`, `superseded`. Severidades: `variation`, `review`, `priority`,
`educational`. Fuentes: `individual_reference`, `trend_analysis`, `simulation_rule`,
`configured_protocol`. No cambiar estos nombres: los consume el frontend.

**Errores.** Propietario o recurso inexistente: `LookupError` con texto legible;
los servicios HTTP lo traducen a 404. Conflicto de reintento de lectura:
`persistence.contracts.DuplicateRequest` → 409. Conflicto OCT: `OctConflict` → 409.
Datos de entrada no válidos: `ValueError` → 400 en las rutas correspondientes.
Fallo operativo de almacenamiento: `RepositoryUnavailable` → JSON 503. El
adaptador debe traducir errores operativos de su driver sin exponer credenciales.
Una fábrica ausente o incompleta falla al iniciar; no se cambia silenciosamente a SQLite.

## 6. OCT y archivos privados

Campos del examen: `eye: OD|OS`, `exam_date`,
`mission_phase: preflight|inflight|postflight`, `device_model`, `scan_protocol`,
`image_quality: good|poor|ungradable|unknown`, `is_baseline: bool`,
`review_status: pending|review_requested|reviewed`, `reviewed_by`,
`reviewed_at: str|null`, `review_notes`, `notes`,
`provenance: manual_entry|exported_report`, `source_document`, `is_demo: true`,
`created_at`, `updated_at`, `measurements`, `files`.
La revisión requiere responsable y fecha coherente con la fecha del examen.

Los DTO internos añaden `request_key: str|null`, `request_hash: str|null`.
La fachada los elimina antes de responder. Un reintento de examen compara el
hash del astronauta, payload normalizado y checksums ordenados; clave idéntica y
hash diferente produce conflicto. La clave OCT es global y admite 1–128 letras,
números, guiones o guiones bajos. Conservar su unicidad ante concurrencia.

Solo una selección explícita de basal reemplaza basales comparables del mismo
astronauta, ojo, equipo y protocolo. El servicio aplica esta política dentro de
una única transacción con la creación/actualización y sus adjuntos.

Cada archivo interno contiene `file_path`, `preview_path` relativos a la raíz
privada, `file_type: PNG|JPEG|TIFF`, `original_name`, `checksum` SHA-256,
`size_bytes: int`, `metadata: object` y `uploaded_at`. Las rutas nunca se envían
al frontend: se publica `content_url` con el ID del archivo y su propietario.
La respuesta de contenido sirve la vista previa PNG bajo la sesión OCT existente.

El servicio valida el contenido y crea originales y vistas previas antes del
commit; limpia archivos si la escritura falla o el reintento recupera un examen
existente. Máximo actual: cinco archivos por solicitud, diez por examen.
Conservar restricciones de checksum por examen y rutas únicas.
Los archivos deben respaldarse junto con la base. SQLite usa por defecto
`.ares-oct-demo/<nombre-de-base>/` junto al archivo SQLite; `ARES_OCT_STORAGE`
puede fijar otra raíz privada fuera de `static/` y `templates/`.

Este contrato usa un directorio local para imágenes. Un futuro almacenamiento
remoto de objetos necesitaría una interfaz adicional de archivos y pruebas;
no basta con devolver una URL remota desde `oct_storage_root()`.

## 7. API conservada y ejemplos

Los clientes existentes no necesitan cambiar sus rutas ni formatos. Única ruta
nueva: `GET /api/rules`, que expone metadatos del backend, sin ejecutar código
recibido ni acreditar científicamente una fuente por sí misma.

| Método | Ruta | Entrada / resultado |
|---|---|---|
| GET | `/` | dashboard y cookie local OCT |
| GET | `/api/health` | `{"status":"ok","mode":"simulation"}` |
| GET | `/api/astronauts` | lista de astronautas con `latest` y `alerts`; filtro `source` |
| GET | `/api/astronauts/<id>/history` | lista histórica compatible, `source`, `limit` 1–200 (36 por defecto) |
| POST | `/api/astronauts/<id>/measurements` | siete indicadores completos; `measurement`, `alerts`; 201 |
| POST | `/api/astronauts/<id>/measurements/manual` | uno o varios indicadores, adquisición y observaciones; `measurement`, `created`, `assessment`, `alerts`; 201/200 |
| GET | `/api/astronauts/<id>/measurements` | documentos normalizados con `metrics`; filtros `source`, `limit` |
| GET/POST | `/api/astronauts/<id>/medical` | consultar/sustituir expediente; JSON completo |
| GET | `/api/astronauts/<id>/assessment` | evaluación actual; puede crear/actualizar episodios; filtro `source` |
| GET | `/api/astronauts/<id>/events` | lista de eventos; `metric`, `status`, `limit` 1–200 (50 por defecto) |
| GET | `/api/devices/status` | capacidades reales de integración del dispositivo |
| GET/POST | `/api/simulation` | estado global; POST JSON exacto `{"running":true}` o `false`; 200 |
| POST | `/api/simulate` | JSON opcional `{"automatic":true}`; `created`, `samples`, `simulation`; 201 si creó, 200 por intervalo/pausa |
| GET | `/api/rules` | `rules`, `analysis_settings`, `configured_protocols`, `oct_review_rules` |
| GET/POST | `/api/astronauts/<id>/oct/exams` | lista con resumen/capacidades; crear devuelve `exam`, `created`; 201/200 |
| GET | `/api/astronauts/<id>/oct/summary` | resumen ocular, fechas, basales y última revisión |
| GET | `/api/astronauts/<id>/oct/comparison` | comparación descriptiva; `eye`, `current_id`, `baseline_id`, `from`, `to` |
| POST | `/api/oct/validate` | valida JSON OCT sin persistir |
| GET/PATCH | `/api/oct/exams/<exam_id>?astronaut_id=<id>` | detalle / actualizar basal, notas o revisión |
| GET | `/api/oct/exams/<exam_id>/measurements?astronaut_id=<id>` | lista de resultados documentados |
| POST | `/api/oct/exams/<exam_id>/files?astronaut_id=<id>` | adjuntar imágenes multipart; examen actualizado, 201 |
| GET | `/api/oct/files/<file_id>/content?astronaut_id=<id>` | vista previa PNG privada |

Las escrituras JSON requieren `Content-Type: application/json` (415 cuando falta).
Los errores HTTP son `{"error":"Descripción"}`. OCT conserva el acceso local,
cookie `ares_oct_session`, `X-ARES-OCT-Token` en escrituras y comprobación de origen.
No se añade una autenticación multiusuario mediante este refactor.

### Lectura parcial

`POST /api/astronauts/1/measurements/manual`, con `Idempotency-Key: entrada-demo-1`:

```json
{"heart_rate":80,"acquired_at":"2026-01-01T12:00:00Z","observations":"Ejemplo sintético"}
```

Ejemplo del objeto `measurement` en la respuesta (las fechas de recepción las
asigna el servidor; la muestra debe cumplir su validación temporal):

```json
{
  "id":"reading:42","reading_id":42,"astronaut_id":1,
  "captured_at":"2026-01-01T12:00:00+00:00",
  "acquired_at":"2026-01-01T12:00:00+00:00",
  "received_at":"2026-01-01T12:00:01+00:00",
  "heart_rate":80,"spo2":null,"systolic":null,"diastolic":null,
  "glucose":null,"temperature":null,"sleep_hours":null,
  "source":"manual","original_source":"manual","device_id":null,
  "provenance":"manual","observations":"Ejemplo sintético","request_id":"entrada-demo-1",
  "quality":{"heart_rate":"unknown"},"units":{"heart_rate":"bpm"},
  "metrics":{"heart_rate":{"value":80,"unit":"bpm","quality":"unknown"}}
}
```

### Expediente

`POST /api/astronauts/1/medical`:

```json
{"notes":"Ejemplo sintético","references":{"heart_rate":{"min_value":60,"max_value":85}}}
```

La respuesta agrega `astronaut_id` y `updated_at`. Estos números son ejemplos de
formato y no una recomendación clínica. Enviar `{"notes":"","references":{}}`
sustituye el perfil por uno vacío, sin eliminar lecturas ni eventos históricos.

### OCT

Ejemplo de creación de examen JSON, con el token de sesión local:

```json
{
  "is_demo":true,"eye":"OD","exam_date":"2026-01-01T12:00:00Z",
  "mission_phase":"preflight","device_model":"Equipo de prueba",
  "scan_protocol":"Protocolo ficticio","image_quality":"good","is_baseline":true,
  "provenance":"manual_entry","notes":"Ejemplo sintético",
  "measurements":[{"metric":"rnfl","value":90,"unit":"µm",
    "anatomical_region":"Región ficticia","measurement_method":"Informe de prueba"}]
}
```

Para imágenes, enviar multipart con `exam` conteniendo ese JSON y uno o varios
campos `files`. La respuesta incorpora IDs, fechas, revisión y metadatos de
archivos. No se deduce un grosor anatómico de los píxeles. Las diferencias OCT
son descriptivas y solo se evalúan reglas configuradas con contexto comparable.
Más ejemplos y formatos: [MODULO_OCT.md](MODULO_OCT.md).

## 8. Reglas científicas: fuente, versión y validación

El proveedor devuelve registros declarativos, nunca expresiones ejecutables.
Ejemplo **de demostración** incluido actualmente:

```json
{
  "id":"ares.demo.heart_rate","scope":"educational","metric":"heart_rate","unit":"bpm",
  "condition":{"operator":"outside","min_value":55,"max_value":105},
  "source":{"title":"Reglas educativas ARES HEALTH",
    "reference":"Configuración de demostración del proyecto","url":null},
  "version":"1","validation_status":"demonstration",
  "message":"Frecuencia cardíaca fuera del rango de demostración",
  "guidance":["Revisar la lectura y contrastarla con el contexto de actividad."],"enabled":true
}
```

- `id` identifica la regla; `version` es texto no vacío. El catálogo entrega la
  versión declarada. Si se necesita historial de cambios de reglas, corresponde
  implementarlo en el almacenamiento definitivo; no se crearon tablas nuevas.
- `source` identifica título, referencia bibliográfica/documental y URL o `null`.
  El compañero debe incorporar fuentes comprobadas de NASA u otras investigaciones,
  con su contexto y autorización de aplicación. El programa no valida por sí solo
  la evidencia de una publicación ni convierte una bandera en validación médica.
- `validation_status`: `demonstration`, `unvalidated`, `validated`.
  `enabled` decide si la regla está habilitada. Un catálogo vacío produce cero
  reglas, sin recuperar silenciosamente los umbrales incluidos.
- `scope=educational`: indicadores/unidades canónicos; operadores `above`, `below`
  (comparación estricta contra `threshold`) u `outside` (fuera de mínimo/máximo,
  límites incluidos). Sigue mostrando avisos educativos y no genera prioridad.
- `scope=safety_protocol`: operador `above` o `below`. Para activar prioridad se
  exige `enabled=true`, `validation_status=validated`, `approved_by` y
  `validation_reference` no vacíos. La evaluación incluye fuente, versión y
  orientación configuradas; la versión participa en la identidad del episodio.
- `scope=oct_review`: operador `absolute_change_at_least`, `threshold>0` en la
  unidad declarada. Exige el mismo estado habilitado/validado, responsable y
  referencia, además de `context` con `anatomical_region`, `measurement_method`,
  `device_model` y `scan_protocol`. Solo compara datos del contexto correspondiente.
- Una regla declarada validada debe identificar una referencia o URL. El validador
  rechaza IDs duplicados, condiciones desconocidas, números no finitos y unidades
  incompatibles de telemetría. No usa `eval`.

`science_service.py` consulta referencias individuales mediante el repositorio y
compila reglas para los evaluadores existentes. Conserva también la configuración
anterior `ARES_ANALYSIS_RULES`, `ARES_SAFETY_PROTOCOLS` y `ARES_OCT_REVIEW_RULES`.
Los protocolos de seguridad anteriores conservan prioridad de selección. Las
reglas nuevas del proveedor no evitan las comprobaciones de calidad y antigüedad.
`ARES_ANALYSIS_RULES` sigue controlando parámetros técnicos de tendencias, no
rangos clínicos. `/api/rules` permite inspeccionar todas estas configuraciones.

La estructura persistida anterior de eventos no contiene una copia completa del
catálogo científico. Para una auditoría histórica completa de fuentes y versiones,
el adaptador definitivo deberá conservar sus versiones; no sobrescribir el catálogo
histórico ni asumir que consultar la regla actual reconstruye decisiones anteriores.

SQLite carga `config/demo_rules.json` por defecto. `ARES_RULES_PATH` puede apuntar
a otro archivo JSON que cumpla el contrato, sin modificar el frontend. Las siete
reglas actuales permanecen expresamente de demostración y no citan evidencia NASA.

## 9. Selección de adaptador y sustitución de SQLite

Configuración actual, sin cambios necesarios:

```powershell
python app.py
```

SQLite utiliza `ARES_DB_PATH` si está definida; de lo contrario `health.sqlite3`.
`create_app(config)` permite fijar por aplicación `ARES_DB_PATH`,
`ARES_RULES_PATH`, `ARES_SEED_DEMO` y las configuraciones de análisis/OCT.
La instancia queda en `app.extensions["ares_repository"]`.

Para integrar una implementación definitiva:

1. Implementar todos los métodos de `PersistenceRepository`, con DTO compatibles,
   propiedad, unicidad y transacciones. La fábrica recibe `app.config` y devuelve
   una instancia funcional; las credenciales pertenecen a la configuración de ese
   adaptador y no al frontend.
2. Migrar a la base elegida preservando IDs, fechas, orígenes, referencias y enlaces.
   Copiar también originales y vistas previas OCT conservando rutas relativas.
   Comparar registros y relaciones antes de cambiar la aplicación.
3. Probar la implementación usando los mismos flujos HTTP y pruebas de servicio.
   `tests/fake_repository.py` es solo un doble de prueba, no una base productiva.
4. Configurar la fábrica real ya implementada (los siguientes nombres ilustran
   el mecanismo; ese paquete no se incluye ni se finge conectar):

```powershell
$env:ARES_DATA_BACKEND = "custom"
$env:ARES_REPOSITORY_FACTORY = "mi_paquete.adaptador:crear_repositorio"
python app.py
```

La función `crear_repositorio(config)` debe existir e implementar el contrato.
También se puede pasar el callable directamente con `create_app({...})`.
En `custom`, la carga automática de datos ficticios está desactivada por defecto.
La simulación explícita sigue usando los astronautas de demostración 1, 2 y 3;
una base con otros tripulantes debe conservar esos fixtures para usar ese modo.

5. Incorporar reglas mediante `get_rule_definitions()` y referencias individuales
   mediante `get_medical_profile()`, después de revisar fuentes y validación.
   No cambiar `app.js`, las claves de JSON ni los estados de evaluación.
6. Para volver a SQLite, quitar las variables anteriores o usar
   `ARES_DATA_BACKEND=sqlite`. Respaldar la base y archivos antes de migraciones;
   cambiar el adaptador no copia datos automáticamente entre sistemas.

Para tareas locales sin HTTP, inyectar explícitamente `repository=...` en los
servicios, o usar `with use_repository(repository): ...`. La selección por contexto
se restaura incluso ante excepciones y no modifica otras aplicaciones Flask.

## 10. Verificación y alcance

```powershell
python -m unittest discover -s tests -q
node --test tests/test_medical_frontend.cjs
```

La suite existente cubre selección, simulación, lecturas, expedientes, evaluación,
eventos, migraciones, reinicios y OCT. `test_data_integration.py` prueba además
servicios y HTTP con un repositorio en memoria y `sqlite3.connect` bloqueado:
idempotencia, aislamiento entre astronautas/aplicaciones, reemplazo de referencias,
episodios y rollback, reglas inyectadas, almacenamiento privado y basales OCT.
Las pruebas de frontend cubren navegación y comportamiento del dashboard existente.

Quedan a cargo del equipo la implementación y migración de la base definitiva,
incorporación y revisión de evidencia científica, y la integración física verificada
de dispositivos. OCT continúa limitado a datos locales de demostración y no admite
DICOM ni telemetría directa de equipos. Este refactor no añade diagnósticos ni
afirma una validación clínica.
