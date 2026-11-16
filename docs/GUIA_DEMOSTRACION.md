# Demostración reproducible A–F

Usa exclusivamente datos ficticios y una base nueva. Desde la raíz del código:

```powershell
.\.venv\Scripts\python.exe scripts/create_demo.py --directory backups/demo-presentacion
$env:ARES_DB_PATH = (Resolve-Path backups/demo-presentacion/health.demo.sqlite3).Path
.\.venv\Scripts\python.exe desktop.py
```

Para usar navegador, sustituye la última línea por `python app.py` desde el mismo
entorno y abre `http://127.0.0.1:5000`. Si la carpeta de demo ya contiene la base,
elige otra carpeta: el script no sobrescribe destinos. Se crean tres perfiles
ficticios, referencia de pulso **60–90 solo de ensayo**, 72 lecturas simuladas,
cuatro lecturas manuales y dos ejemplos OCT del astronauta 1, ojo derecho.
Estos valores no son referencias personales ni criterios clínicos.

## A — Telemetría y simulación

Selecciona Valentina, Mateo y Sofía. Comprueba el nombre, la fecha, la fuente y
los pequeños gráficos. En fuente manual de Valentina solo existe pulso; los demás
indicadores permanecen ausentes. La silueta y los siete sensores se conservan.

Elige simulación y pulsa **Reanudar simulación**. Tras cinco segundos debe aparecer
un lote para los tres astronautas. Abre otra pestaña sobre el mismo servidor:
la frecuencia global no aumenta. Pausa desde una ventana: ambas reflejan el cambio
en su próxima consulta y siguen mostrando datos. **Generar lectura** es una acción
explícita de demo; respeta el mínimo de cinco segundos sin reanudar automáticamente.

## B — Expediente

Abre el menú y el historial médico de Valentina. Verás la nota ficticia y la
referencia de ensayo. Edita la nota, cambia a Mateo y vuelve: se recupera el
borrador de Valentina. Pulsa **Guardar expediente**, vuelve a cambiar de astronauta
y comprueba el guardado. Reinicia la aplicación conservando `ARES_DB_PATH`: sigue
en SQLite. No uses este procedimiento sobre la base principal.

## C — Lectura y explicación

En Valentina, abre Registro manual. Registra solo pulso **112** y una observación
“MEDICIÓN FICTICIA DE DEMOSTRACIÓN”, dejando fecha vacía. La interfaz confirma
registro y pausa compartida. Con referencia de ensayo 60–90, la explicación
describe **22 bpm sobre el máximo**, con fecha y origen manual. Revisa detalles,
orientación e historial de eventos. Actualizar la evaluación no crea un episodio
nuevo por cada consulta. Una lectura antigua o de mala calidad se trata como tal;
no fuerces una interpretación actual cuando el sistema indique desactualización.

Las orientaciones son educativas y provienen de reglas; no diagnostican ni
prescriben. El catálogo informa validación y procedencia. Nunca uses medias NASA
como referencia clínica individual.

## D — Investigación NASA

Abre Investigación NASA. Comprueba **4 conjuntos y 52 participantes**, sin cambiar
los tres tripulantes del Centro de control. Elige CRF, C1:C1G0001 y pulso. Revisa
tarjetas de disponibilidad, curva histórica, tabla, procedencia y banderas de calidad.
Aplica fechas de estudio, cambia indicador y navega la paginación. SpO₂ no está
disponible en estos conjuntos. Los huecos no se convierten en cero y las fechas
técnicas no se presentan como observaciones del estudio.

## E — OCT

Abre Salud ocular — OCT, Valentina, ojo derecho. La demo tiene los ejemplos
`oct-demo-baseline.json` y `oct-demo-followup.json`: uno es basal y el segundo
permite comparación descriptiva compatible. Revisa fecha, equipo, protocolo,
unidad y región antes de interpretar diferencias. Para probar imágenes carga
`docs/examples/oct-demo-pattern.png`: es un patrón artificial de prueba, no una
retina ni un examen médico. Se identifica como demostración.

Para demostrar la importación desde cero usa otra base aislada sin fixtures,
abre Registrar examen y carga esos JSON de ejemplo mediante la opción de
importación. En un astronauta/ojo sin exámenes la vista debe permanecer vacía.
El prototipo no analiza imágenes clínicamente ni diagnostica SANS.

## F — Escritorio y cierre

El mismo `desktop.py` abre la interfaz nativa con un único servidor local. Navega
entre módulos, cierra la ventana y vuelve a abrir conservando la ruta: se recuperan
los datos guardados. Otra instancia de escritorio para la misma base se rechaza.
Para probar el ejecutable sobre esta demo, inicia su ruta desde la misma consola
con `ARES_DB_PATH` configurado. Sin esa variable usa su propia carpeta de datos.

Al terminar, elimina la variable solo de esta consola para volver a la ruta normal:

```powershell
Remove-Item Env:ARES_DB_PATH
```

No elimines la base principal para volver a empezar. La demo y sus respaldos
se conservan en la carpeta elegida. Para ejecutar A–E automáticamente:

```powershell
.\.venv\Scripts\python.exe scripts/verify_demo.py
```

Ese comando usa un directorio temporal y comprueba HTTP, SQLite, referencias,
eventos, investigación y OCT; la ventana nativa se valida por separado. Roadtrip
debe seguir mostrando integración no disponible hasta una prueba física verificada.
