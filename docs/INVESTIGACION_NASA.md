# Investigación NASA — integración de los conjuntos recibidos

El inventario, hashes, función y política de distribución de cada SQLite se
encuentran en [BASES_DE_DATOS.md](BASES_DE_DATOS.md).

## Resultado y origen utilizado

Se integraron exclusivamente las tablas científicas de
`ARES_HEALTH_BASES_NASA_ADAPTADAS.zip` → `health_ares_nasa_preservada.sqlite3`.
La fuente extraída se conserva en `data/nasa/health_ares_nasa_preservada.sqlite3`;
su SHA-256 coincide con `VALIDACION.json` del ZIP:

```text
d38acb264fd22238d3ae1935c2122c9f5bbfc19d06d18404f5284ad7e03d9005
```

No se usó `health_ares_nasa_demo.sqlite3`. No se copiaron perfiles de investigación
a `astronauts` ni lecturas científicas a `measurements` o `reading_batches`.
La aplicación continúa usando su base activa `health.sqlite3`; los datos científicos
se fusionaron allí, sin reemplazarla por la copia anterior del equipo.

| Tabla científica | Registros incorporados |
|---|---:|
| `subjects` | 52 |
| `dataset_sources` | 4 |
| `observation_days` | 2.957 |
| `vital_signs` | 2.940 |
| `sleep` | 207 |
| `nutrition` | 2.125 |
| `hydration` | 2.689 |
| `lab_results` | 984 |
| `quality_flags` | 2.800 |
| `ares_nasa_import_manifest` | 1 |

También se conservaron los seis índices científicos existentes y nueve vistas:
`v_vital_daily`, `v_sleep_daily`, `v_nutrition_daily`, `v_hydration_daily`,
`v_lab_daily`, `v_aether_daily`, `v_aether_demo`, `v_ares_nasa_daily` y
`v_ares_nasa_demo_daily`. Las vistas con «demo» ya existían en la fuente preservada;
conservarlas no importa ni habilita perfiles demo en el dashboard.

## Respaldo y conservación local

Antes de modificar la base se hizo una copia coherente mediante SQLite Backup API:

```text
backups/before-nasa-20261115T204455Z/health.before-nasa.sqlite3
backups/before-nasa-20261115T204455Z/manifest.json
```

La herramienta de fusión hizo además su propio respaldo inmediatamente antes de
la transacción, con el informe de comparación de filas y huellas:

```text
backups/health.before-nasa-20261115T210115052461Z-7cf50a.sqlite3
backups/health.before-nasa-20261115T210115052461Z-7cf50a.json
```

La base activa contenía **3 astronautas, 10.860 mediciones y 498 eventos**, frente
a 10.569 mediciones y 458 eventos en el ZIP. Se conservaron los registros locales
adicionales y todos los IDs: las huellas y conteos de cada tabla local coinciden
antes y después de la fusión. Los expedientes, referencias, lecturas parciales,
tablas OCT y secuencias tampoco cambiaron. OCT estaba vacío y sigue vacío.

`PRAGMA integrity_check` devolvió `ok` y `PRAGMA foreign_key_check` no devolvió
errores. Las tablas científicas se compararon con la fuente mediante conteos y
huellas de sus filas, manteniendo valores y `NULL` originales.

## Archivos de implementación

| Archivo | Responsabilidad |
|---|---|
| `integrate_nasa.py` | respaldo, fusión aditiva transaccional, detección de conflictos, comprobaciones e informe |
| `research_service.py` | consultas SQL de solo lectura, filtros, métricas, series y calidad |
| `research_api.py` | blueprint `/api/research`, validación y respuestas JSON |
| `app.py` | dos líneas para importar y registrar el blueprint; conserva la fábrica y configuración existentes |
| `templates/research.html` | nueva sección de investigación, filtros, tablas, procedencia y estados vacíos |
| `templates/index.html` | carga de recursos, inclusión de la sección y opción del menú |
| `static/js/research.js` | consultas bajo demanda, paginación, gráficos y control de respuestas atrasadas |
| `static/js/navigation.js` | octava vista y separación del contexto de investigación y tripulación |
| `static/css/research.css` | presentación oscura, adaptable y de pantalla completa |
| `tests/test_nasa_research.py` | consultas reales de la fuente y fusión sobre copias temporales |
| `tests/test_research_frontend.cjs` | gráficos, valores ausentes, paginación, errores y concurrencia |
| `tests/test_medical_frontend.cjs` | mantiene el banco de pruebas del dashboard e incorpora la nueva navegación |

`database.py`, `persistence/`, el simulador, la normalización de dispositivos,
expedientes, reglas educativas y el módulo OCT no se modificaron para esta fase.
El servicio obtiene la ruta del repositorio SQLite configurado, respetando tanto
`ARES_DB_PATH` como la ruta explícita de `create_app`. No fija una segunda base
oculta ni consulta la variante demo. Un adaptador distinto de SQLite devuelve un
estado 503 en investigación mientras no implemente ese acceso; el resto de la
aplicación sigue utilizando su contrato actual.

## Conjuntos, campañas y participantes

Los conjuntos se identifican por `dataset_sources.source_code`; no son campañas:

| Código | Datos seleccionados |
|---|---|
| `CRF` | signos vitales diarios e hidratación |
| `BRSMAct` | sueño por actigrafía de Campaign 1 |
| `MR010G` | resultados de laboratorio seleccionados |
| `MR016G` | nutrición y energía |

Los prefijos de `source_file` vinculan los registros a estos códigos. Las banderas
agregadas con `source_file='nutrition'`, `'lab_results'`, `'hydration'` o
`'vital_signs'` se atribuyen a la familia correspondiente. Esta correspondencia
está explícita en el servicio y debe revisarse si se incorporan nuevos archivos.

Los 52 sujetos pertenecen a campañas `C1`, `C3` o `CFT70`. Cinco sujetos no tienen
registros en las familias de mediciones seleccionadas: permanecen consultables
en «Todos los conjuntos», con estado vacío. Al seleccionar un conjunto, el
selector muestra sus participantes con registros. Nunca se agregan estos sujetos
a los tres astronautas ficticios del centro de control.

## API y consultas implementadas

Todas las rutas de investigación son **GET**, bajo `/api/research`:

| Ruta | Consulta |
|---|---|
| `/datasets` | catálogo original `dataset_sources`, con sus familias disponibles |
| `/sources` | el mismo catálogo de procedencia, incluyendo archivo, SHA-256 y notas |
| `/subjects` | sujetos; filtros de conjunto/campaña y existencia de registros en el intervalo solicitado |
| `/vital-signs` | filas originales de `vital_signs` con banderas asociadas |
| `/sleep` | filas originales de sueño |
| `/nutrition` | filas originales de nutrición, incluidas las que carecen de fecha |
| `/hydration` | filas originales de hidratación |
| `/lab-results` | resultados por analito, unidad y procedencia original |
| `/quality-flags` | banderas originales y agregadas, con valor original y explicación |
| `/summary` | disponibilidad por indicador: conteos de valores, registros y registros fechados; conteos de banderas |
| `/series` | serie de un participante e indicador, a partir de las vistas diarias del proveedor |

Filtros:

- `dataset`: `CRF`, `BRSMAct`, `MR010G`, `MR016G`; omitir para todos.
- `subject_id`: identificador textual exacto, por ejemplo `C1:C1G0001`.
- `from`, `to`: fechas inclusivas `YYYY-MM-DD`, sin asignar zona horaria.
- `metric`: clave de indicador validada para cada familia; en laboratorio filtra
  `analyte`, y en banderas filtra `field_name`. En tablas de columnas anchas
  selecciona el indicador y sus metadatos sin eliminar filas con ese valor `NULL`.
- `entity`: requerido en `/series`, con `vital_signs`, `sleep`, `nutrition`,
  `hydration` o `lab_results`.
- `campaign`: filtro de `/subjects`.
- `flag`: filtro de `/quality-flags`, por ejemplo `conflicting_values`.
- `page`: entero desde 1; `page_size`: 1–200, 50 por defecto. Se aplican a las
  listas y series. `/summary` entrega el resumen del conjunto filtrado.

Las listas devuelven `items`, `total`, `page`, `page_size`, `pages` y metadatos
históricos. Su orden es estable por fecha e ID; los registros sin fecha aparecen
después de los fechados. Las fuentes son metadatos de archivo, por lo que sus
filtros aplicables son `dataset` y paginación. Las series requieren un sujeto;
no se superponen individuos ni se calculan promedios entre participantes.

Ejemplos:

```text
GET /api/research/datasets
GET /api/research/subjects?dataset=BRSMAct&page_size=50
GET /api/research/vital-signs?subject_id=C1%3AC1G0001&from=2004-09-01&to=2004-09-30&page_size=12
GET /api/research/lab-results?subject_id=C1%3AC1G0001&metric=glucose_mg_dl
GET /api/research/series?dataset=CRF&subject_id=C1%3AC1G0001&entity=vital_signs&metric=heart_rate_bpm
GET /api/research/quality-flags?dataset=MR016G&flag=conflicting_values&page=2
```

Fragmento de una serie (estructura, no una medición inventada):

```json
{
  "data_class":"historical_research",
  "time_basis":"study_date_without_timezone",
  "entity":"nutrition",
  "metric":"calcium_mg",
  "unit":"mg",
  "items":[{"subject_id":"C1:C1G0001","date":"2004-08-30","value":null,"quality_flags":["conflicting_values","duplicate_daily_key"]}]
}
```

La combinación exacta de banderas se obtiene del archivo recibido; el cliente no
las deduce de límites numéricos. Las consultas usan parámetros para sujetos,
fechas, filtros y paginación. Los únicos identificadores SQL dinámicos provienen
de listas internas o del catálogo de analitos verificado; no se interpolan
identificadores arbitrarios del cliente. Las conexiones de consulta activan
`PRAGMA query_only=ON` y una transacción de lectura coherente.

Consultas representativas, equivalentes a las implementadas:

```sql
SELECT * FROM vital_signs
WHERE subject_id=? AND date>=? AND date<=?
ORDER BY date,id LIMIT ? OFFSET ?;

SELECT subject_id,date,heart_rate_bpm AS value
FROM v_vital_daily WHERE subject_id=? ORDER BY date;

SELECT * FROM lab_results
WHERE subject_id=? AND analyte=? ORDER BY date,id LIMIT ? OFFSET ?;

SELECT flag,COUNT(*) AS count FROM quality_flags
WHERE subject_id=? GROUP BY flag ORDER BY count DESC,flag;
```

La asociación de banderas utiliza archivo/fila original y, para controles
agregados, familia/sujeto/fecha. Los indicadores de calidad dibujados en la curva
se refieren al día y familia, por lo que pueden advertir problemas en otros campos
de ese mismo registro: revisar la tabla de banderas antes de atribuirlos a un valor.

## Valores ausentes, fechas y gráficos

- No hay SpO₂ en los conjuntos recibidos. Se muestra «no disponible» y no se crea
  una serie ni se completa ese campo con promedios o ceros.
- Los valores `NULL` permanecen `null` en JSON y «No disponible» en la interfaz.
  Cero sigue siendo un valor numérico real. Las curvas se interrumpen ante `NULL`.
- Las tablas muestran los valores originales, incluso si están señalados. Las
  curvas utilizan las vistas existentes: devuelven `NULL` para valores diarios
  contradictorios y conservan las exclusiones de su implementación original.
  No se reemplazó esa lógica por umbrales clínicos nuevos.
- Las tarjetas muestran **conteos de disponibilidad**, no promedios del estudio
  convertidos en límites médicos.
- Hay **1.388 filas de nutrición sin fecha**. Se muestran en tablas sin asignarles
  un día técnico; no se colocan en el eje temporal. Al filtrar fechas, estas filas
  quedan fuera porque no es posible situarlas dentro del intervalo solicitado.
- `date`/`study_date` son días del estudio. `measurement_time` es una hora local sin
  zona verificable y así se presenta. No se usa el mediodía técnico de la variante
  demo como si fuera una adquisición real. El cálculo de posición del eje usa
  días calendario, sin mostrarlo como una hora de adquisición UTC.
- Se conservan las fechas sospechosas, incluidas las de 2001 señaladas con
  `suspect_year`. No se corrigen silenciosamente; el filtro permite acotar el estudio.
- Las curvas usan una sola unidad y un solo participante. Laboratorio usa
  `v_lab_daily` cuando el analito está en esa vista; los otros analitos utilizan
  la misma política de conflicto por día: valores distintos → `NULL`.

La nueva sección ocupa el área disponible y conserva navegación, formularios y
selección del dashboard. Su encabezado no muestra el nombre del astronauta actual
y oculta allí los controles de generación de telemetría. La simulación de los tres
perfiles mantiene su estado al navegar y nunca refresca las series científicas.

## Repetir o trasladar la integración

En PowerShell, desde la carpeta de `app.py`:

```powershell
python integrate_nasa.py
python app.py
```

La herramienta usa `ARES_DB_PATH` para el destino, si está definida. Para otra
base local compatible:

```powershell
python integrate_nasa.py --source "C:\ruta\health_ares_nasa_preservada.sqlite3" --target "C:\ruta\mi_base.sqlite3" --backup-dir "C:\ruta\respaldos"
$env:ARES_DB_PATH = "C:\ruta\mi_base.sqlite3"
python app.py
```

El destino debe existir. Antes de cada fusión se realiza un respaldo nuevo.
La fuente se abre en modo de solo lectura, se valida su integridad y se rechaza
la variante con más de tres perfiles ARES. La herramienta copia únicamente la
lista permitida de tablas/vistas/índices científicos, en una transacción
`BEGIN IMMEDIATE`, sin `REPLACE`, `DELETE`, `DROP` ni actualizaciones de filas locales.

Si una clave ya existe con los mismos valores, no se duplica. Las filas científicas
locales ausentes de la fuente permanecen intactas. Un cambio de contenido bajo
la misma clave, una colisión de unicidad o un esquema incompatible revierte toda
la fusión y conserva el respaldo: se debe revisar el conflicto antes de importar
otra versión, sin sobreescribir automáticamente los datos locales.

Los archivos `.sqlite3` están excluidos por el `.gitignore` existente. Al compartir
el proyecto, incluir por separado la base activa respaldada o la fuente preservada
y ejecutar la herramienta; el código por sí solo no contiene las mediciones.

## Pruebas ejecutadas y límites de la verificación

```powershell
python -m unittest discover -s tests -q
node --test tests/test_medical_frontend.cjs tests/test_research_frontend.cjs
```

Resultados de esta entrega: **134 pruebas Python y 50 pruebas de frontend
aprobadas, sin pruebas omitidas**. Las pruebas científicas se ejecutaron con la
fuente real recibida sobre copias temporales e incluyeron:

- integridad SQLite/FK, comparación de filas científicas y preservación de datos
  locales con respaldo; fusión repetida y rollback ante conflicto;
- conteos, filtros, paginación, fechas, procedencia, banderas e intentos de inyección;
- separación entre tres astronautas y 52 sujetos, incluidos sujetos sin mediciones;
- conservación de `NULL`, fechas ausentes, valores atípicos y fechas sospechosas;
- funcionamiento de simulación, entradas manuales, expedientes, alertas, eventos,
  reglas educativas, dispositivos y estado vacío OCT;
- coordenadas y trazado Canvas: cero válido, huecos en valores ausentes, puntos
  señalados, indicador vacío, paginación, errores y respuestas atrasadas A→B→A.

Se inició además un servidor Flask sobre una copia temporal de la base integrada
y la página respondió HTTP 200. **La inspección visual en navegador no pudo
completarse**: Browser Use informó que su host no estaba disponible y Computer
Use falló al iniciar por un error de creación de recursos del kernel. Por tanto,
las pruebas del gráfico verifican datos y operaciones de dibujo, pero no equivalen
a una comprobación visual de píxeles o de todos los tamaños de pantalla.

## Limitaciones científicas pendientes

La procedencia se presenta tal como la entregó `dataset_sources`, con archivos y
huellas. Esta integración no contrasta de forma independiente los archivos de
origen o publicaciones NASA; esos materiales no están incluidos en el ZIP. Solo
se incorporaron las cuatro familias estructuradas recibidas; el material documental
PFEOUM mencionado en la guía del equipo no se presenta como mediciones.

Las 2.800 banderas incluyen duplicados, conflictos, números inválidos, fechas
ausentes/sospechosas e inconsistencias de día de estudio. «Sin bandera» no significa
validación clínica. Las vistas del proveedor incluyen criterios de depuración que
se conservaron y deben revisarse científicamente antes de un uso operacional.
No se conectaron estas observaciones a reglas de prioridad ni a diagnósticos.
Las reglas educativas ARES permanecen de demostración, y los datos históricos
no se atribuyen al reloj. No se crearon imágenes ni exámenes OCT; no hay datos
oftalmológicos compatibles en la entrega recibida.
