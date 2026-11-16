# Salud ocular — OCT

## Alcance y fundamento

La tomografía de coherencia óptica utiliza principios ópticos e interferometría para
obtener cortes de tejidos. En el seguimiento ocular permite examinar estructuras
como retina, coroides y cabeza del nervio óptico. NASA incluye OCT en evaluaciones
antes, durante y después del vuelo para el seguimiento neuroocular. El documento
OCHMO-MTB-001 describe SANS como un conjunto de hallazgos oculares asociados a vuelos
espaciales prolongados y presenta su contexto de vigilancia médica. [NASA OCHMO-MTB-001](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf), [DICOM, terminología OCT](https://dicom.nema.org/medical/dicom/2026c/output/chtml/part16/chapter_D.html)

ARES HEALTH registra exámenes, presenta imágenes exportadas y calcula diferencias
entre mediciones documentadas. Una diferencia cuantitativa describe un cambio entre
registros. La interpretación de las estructuras, la valoración de SANS y el diagnóstico
requieren revisión profesional y el contexto clínico del astronauta. Los avisos de
este módulo orientan hacia esa revisión.

El uso actual está limitado a **datos ficticios o sintéticos en un entorno local**.
Todos los registros llevan la indicación **Datos OCT de demostración**. La declaración
del usuario identifica el modo de uso. La validación de formatos verifica el contenido
del archivo y su legibilidad, mientras que la procedencia anatómica debe comprobarla
el operador.

## Inicio y navegación

Desde la raíz del proyecto, instala las dependencias e inicia Flask.

```powershell
python -m pip install -r requirements.txt
python app.py
```

Abre `http://127.0.0.1:5000`, recarga con Ctrl+F5 tras actualizar y entra en
**Menú → 07 / SALUD OCULAR — OCT**. También puedes entrar desde el resumen
**Salud ocular — OCT** del expediente médico. El módulo utiliza la misma selección
de astronauta de ARES HEALTH.

La vista contiene identificación y selección del examen, visualizador, mediciones,
seguimiento longitudinal, registro/importación y revisión. Los borradores se conservan
al navegar entre módulos. Cambiar de astronauta descarta el borrador OCT y carga su
contexto ocular. Cambiar de examen carga la revisión de ese examen. Los registros
guardados permanecen en SQLite y las imágenes en almacenamiento privado.

## Registrar o importar un examen

1. Selecciona el astronauta y pulsa **Registrar examen**.
2. Registra ojo derecho OD u ojo izquierdo OS, fecha y hora local, fase de misión,
   equipo OCT, protocolo de exploración y calidad declarada.
3. Añade las mediciones disponibles con su indicador y valor. Completa unidad, región
   anatómica y método cuando estén documentados en el informe.
4. Indica la procedencia y el documento de origen. Para transcribir un informe usa
   **Transcripción manual**. El importador JSON registra **Informe exportado**.
5. Adjunta imágenes exportadas y observaciones. Si existe una revisión declarada en
   el informe, completa su estado, profesional responsable, fecha y observaciones.
6. Marca la referencia basal solo cuando quieras definir ese examen como basal para
   ese ojo, equipo y protocolo. Con el contexto documentado, esta elección sustituye
   la marca anterior de ese grupo.
7. Confirma el carácter ficticio o sintético del material y pulsa **Guardar examen OCT**.

Los campos RNFL, TRT, macular y coroideo son códigos disponibles para mediciones
documentadas. También se admiten otros códigos de hasta 64 letras, números y guiones
bajos, empezando por una letra. La unidad se guarda con cada valor. Los alias `um` y
`μm` se normalizan a `µm`. Las otras unidades se conservan exactamente y se comparan
con distinción entre mayúsculas y minúsculas.

Un examen puede contener imágenes, mediciones o datos descriptivos parciales. Los valores
ausentes permanecen ausentes. Los metadatos pendientes se muestran como tales e impiden
comparar automáticamente las mediciones afectadas. La extracción cuantitativa
desde la anatomía de una imagen queda fuera del alcance del visualizador actual.

### Resultados JSON

El selector **Importar resultados JSON con el esquema ARES OCT** acepta archivos UTF-8
de hasta 200 KiB. La API valida el objeto completo antes de completar el formulario.
El usuario revisa los campos y confirma los datos de demostración antes de guardarlos.
El contrato de intercambio corresponde al esquema ARES documentado en esta sección.
Los formatos propietarios de cada fabricante requieren una adaptación de su exportación.

Se incluyen dos ejemplos sintéticos y un patrón de imagen sin anatomía ni datos de
pacientes en la carpeta `docs/examples`.

- `oct-demo-baseline.json` define un basal explícito para OD.
- `oct-demo-followup.json` registra un seguimiento compatible del mismo ojo.
- `oct-demo-pattern.png` permite probar carga, ajuste y zoom con una imagen geométrica
  identificada como sintética.

Para recorrer el flujo completo, importa y guarda primero el JSON basal. Después importa
y guarda el seguimiento con el mismo astronauta. Puedes adjuntar el patrón a ambos
exámenes. Los ejemplos contienen dos mediciones ficticias con regiones y métodos
identificados. El seguimiento RNFL pasa de 90 a 92 µm, lo que produce una diferencia
de 2 µm y aproximadamente 2,22222 %. Estos valores sirven para verificar la aritmética.

El esquema mínimo de creación es el siguiente.

```json
{
  "is_demo": true,
  "eye": "OD",
  "exam_date": "2026-01-01T12:00:00Z",
  "mission_phase": "preflight",
  "device_model": "Equipo OCT de demostración",
  "scan_protocol": "Protocolo sintético A",
  "image_quality": "unknown",
  "is_baseline": false,
  "provenance": "manual_entry",
  "source_document": "",
  "notes": "Ejemplo ficticio",
  "measurements": []
}
```

Cada elemento de `measurements` identifica `metric` y `value`. Puede aportar `unit`,
`anatomical_region` y `measurement_method` cuando estén disponibles. La revisión puede aportar `review_status`,
`reviewed_by`, `reviewed_at` y `review_notes`. Las fechas de la API deben incluir zona
horaria y se normalizan a UTC. El formulario convierte la fecha local del navegador.
El estado `reviewed` requiere responsable y una fecha de revisión igual o posterior
a la del examen. La identificación del responsable constituye un registro declarado
por el usuario.

## Formatos, visualizador y almacenamiento

| Elemento | Implementación actual |
| --- | --- |
| PNG y JPEG | Validación de firma, formato y decodificación con Pillow |
| TIFF | Una sola página, con las variantes decodificables por el Pillow instalado |
| Tamaño | 10 MiB por imagen, 5 imágenes por operación y 10 por examen |
| Dimensiones | Hasta 16 millones de píxeles y 8192 píxeles por lado |
| Vista previa | PNG RGB de 8 bits, con lado máximo de 2048 píxeles |
| Intensidades de mayor profundidad | Escalado lineal para la vista previa, indicado en sus metadatos |
| Zoom | Ajuste al contenedor, ampliación y reducción de 25 a 800 % del ajuste, y restablecimiento |
| Exámenes volumétricos o multipágina | Requieren un visor específico y una exportación compatible |
| DICOM | Pendiente de un adaptador OCT validado por modelo y tipo de objeto |

La vista previa ofrece una representación para consultar la imagen. Las medidas se
obtienen del informe documentado del equipo o de la transcripción del operador.
La resolución y la profundidad de la vista previa se indican junto al archivo.
El original conserva sus bytes y su checksum SHA-256 en el almacenamiento privado.

La validación usa firmas de contenido, una lista limitada de decodificadores,
verificación y carga completa de la imagen, límites de dimensiones y detección de
bombas de descompresión. La vista previa se vuelve a codificar en PNG. Las extensiones
y el MIME enviados por el navegador actúan como indicaciones de selección. [Seguridad de Pillow](https://pillow.readthedocs.io/en/stable/handbook/security.html)

Los archivos se guardan junto a la base, bajo
`.ares-oct-demo/<nombre-de-la-base>/<UUID>/`. `ARES_OCT_STORAGE` en la configuración
Flask permite establecer otra raíz privada. Las carpetas `static` y `templates` se
rechazan como destino. Los nombres originales se normalizan y cada carpeta tiene un
identificador generado. `.gitignore` excluye el almacenamiento privado predeterminado.

La API de archivos entrega la vista previa tras comprobar sesión local, origen y
pertenencia al astronauta. Incluye cabeceras de privacidad y de tipo de contenido.
Las respuestas JSON de los exámenes contienen identificadores y URL de contenido.
Las rutas privadas permanecen en el repositorio del servidor.

La aplicación actual carece de autenticación individual. El módulo restringe sus rutas
a loopback y a los hosts locales permitidos. La página emite una cookie HttpOnly
SameSite=Strict y un token de sesión para las escrituras. Estos controles corresponden
al entorno local de demostración. El uso con personas reales requiere autorización por
expediente y autenticación individual, además de validar el flujo clínico y técnico.

### Persistencia y transacciones

Las tablas `oct_exams`, `oct_measurements` y `oct_files` usan claves foráneas. La creación
del examen, sus mediciones, asociaciones de archivos y sustitución explícita del basal
se guardan en una transacción. Un error limpia los archivos preparados por esa operación
y revierte la escritura. La clave de idempotencia del formulario permite recuperar un
guardado cuando se reintenta la misma solicitud.

Tras una interrupción abrupta del proceso entre preparar el archivo y confirmar SQLite,
puede quedar una carpeta sin referencia. Su revisión requiere contrastar las rutas con
`oct_files` antes de limpiar almacenamiento. Los respaldos deben conservar la base y
su carpeta privada conjuntamente. Al reiniciar Flask, abre de nuevo la página para
renovar la sesión local.

## Comparación longitudinal y orientación

La referencia basal debe estar marcada explícitamente. La comparación exige el mismo
astronauta, ojo, equipo, protocolo, indicador, unidad, región y método. El basal debe
tener una fecha igual o anterior al examen seleccionado. Las calidades declaradas
`poor` y `ungradable` impiden la comparación. Una calidad `unknown` se mantiene visible
como dato pendiente de comprobar por el operador.

Los campos vacíos y las indicaciones explícitas de contexto desconocido, como
«No informado», dejan pendiente la comparación. El registro conserva el valor disponible.

La coincidencia de nombres identifica registros comparables dentro de este prototipo.
La equivalencia entre protocolos, versiones del fabricante, segmentaciones y condiciones
de adquisición requiere comprobación profesional antes de interpretar cambios.

El selector del basal permite elegir entre exámenes marcados del mismo ojo. Si falta
una referencia compatible, se muestra **No existe un examen basal comparable**. Cada
medición que cumple las condiciones aporta basal, actual, diferencia con signo, magnitud
de diferencia y porcentaje cuando el basal es positivo. Un basal igual o menor que cero
conserva la diferencia y muestra el porcentaje como no calculable.

El gráfico y su tabla contienen exámenes guardados con fecha, fase, calidad y procedencia.
El periodo filtra los puntos temporales y conserva la comparación del examen seleccionado
con su basal. La tabla permite consultar los valores exactos que alimentan el gráfico.
La evaluación de signos vitales utiliza su propio historial y sus intervalos de captura.

Los avisos OCT describen ausencia de basal, falta de imagen, revisión pendiente,
incompatibilidades, importaciones rechazadas y reglas de revisión configuradas.
Los avisos de comparación se calculan desde los registros oculares y se presentan en
la vista OCT. El historial de episodios biométricos se consulta desde Historial de eventos.

`ARES_OCT_REVIEW_RULES` comienza como lista vacía. Una regla necesita contexto completo,
una magnitud de cambio positiva en la unidad declarada, razón y referencia de validación.
Sus campos son `metric`, `unit`, `anatomical_region`, `measurement_method`, `device_model`,
`scan_protocol`, `min_absolute_change`, `reason` y `validation_reference`.
Cuando se cumple, el aviso muestra valor anterior, actual, diferencia, fecha, calidad,
fuente de la regla y orientación para solicitar revisión. La decisión de configurar una
regla operacional requiere validación profesional de su ámbito.

## API y configuración

Todas las rutas OCT requieren haber abierto la página local para obtener la cookie.
POST y PATCH requieren además la cabecera `X-ARES-OCT-Token` emitida en la página.
En rutas de examen y archivo, `astronaut_id` es obligatorio para comprobar pertenencia.

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/api/astronauts/<id>/oct/exams` | Exámenes, mediciones, archivos, resumen y formatos |
| POST | `/api/astronauts/<id>/oct/exams` | Crea un examen con JSON o multipart |
| GET | `/api/oct/exams/<exam_id>?astronaut_id=<id>` | Examen individual |
| PATCH | `/api/oct/exams/<exam_id>?astronaut_id=<id>` | Basal, observaciones o revisión |
| GET | `/api/oct/exams/<exam_id>/measurements?astronaut_id=<id>` | Mediciones documentadas |
| GET | `/api/astronauts/<id>/oct/comparison` | Comparación y avisos con filtros |
| POST | `/api/oct/exams/<exam_id>/files?astronaut_id=<id>` | Añade imágenes al examen |
| GET | `/api/oct/files/<file_id>/content?astronaut_id=<id>` | Vista previa privada |
| GET | `/api/astronauts/<id>/oct/summary` | Resumen ocular para el expediente médico |
| POST | `/api/oct/validate` | Valida y normaliza el JSON de importación |

La comparación acepta `eye`, `current_id`, `baseline_id`, `from` y `to`. El periodo
usa `YYYY-MM-DD`. La creación multipart utiliza el campo `exam` con el objeto JSON y
uno o varios campos `files`. Las solicitudes reintentables de creación pueden aportar
`Idempotency-Key`. El token cambia con cada proceso del servidor.

La migración se ejecuta al iniciar Flask y agrega únicamente las tablas OCT. La
integración con el expediente utiliza un resumen de consulta con fecha del último
examen, basales marcados, cantidad, revisión registrada y observaciones.

## DICOM y conexión de equipos reales

Se investigó `pydicom` para una futura importación DICOM. El estándar define modalidad
`OPT` para tomografía oftálmica y varios tipos de objeto oftálmico. La adaptación debe
validar `Modality`, `SOPClassUID`, lateralidad y el contexto del estudio antes de asociar
contenido a un examen. La identificación de un objeto tomográfico exige revisar sus
atributos y la declaración de conformidad del fabricante. [DICOM, Ophthalmic Tomography Series Module](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.17.6.html)

`pydicom` permite acceder a los datos de píxel y a su sintaxis de transferencia. Las
imágenes comprimidas pueden necesitar decodificadores adicionales y los objetos
multiframe requieren un manejo explícito de sus cortes. El visualizador actual
implementa imágenes raster exportadas de una sola página. La carga DICOM devuelve
una explicación del adaptador pendiente. [Documentación de pydicom sobre Pixel Data](https://pydicom.github.io/pydicom/stable/tutorials/pixel_data/introduction.html)

Para conectar un equipo OCT concreto faltaría completar lo siguiente.

1. Obtener modelo, versión, manual de exportación, SDK o API autorizada y declaración
   de conformidad DICOM cuando exista.
2. Validar muestras autorizadas y la relación entre identificador del astronauta,
   ojo, fecha, equipo, protocolo, región, método, unidades y calidad.
3. Implementar y probar un adaptador de ese formato o interfaz, incluyendo objetos
   multiframe y sintaxis comprimidas si forman parte de su exportación.
4. Verificar la fidelidad de las imágenes y medidas frente al visor e informe del
   fabricante, con revisión oftalmológica y del personal médico de la misión.
5. Establecer autenticación y autorización por expediente para información real y
   validar almacenamiento, trazabilidad y recuperación en el entorno previsto.

El smartwatch Roadtrip aporta el contexto de signos vitales de su adaptador. La fuente
de un examen OCT es el equipo oftalmológico declarado y su exportación documentada.

## Pruebas ejecutadas

```powershell
python -m unittest discover -s tests -q
node --test tests/test_medical_frontend.cjs
```

La suite Python ejecutó 95 pruebas, incluidas 31 de OCT con bases temporales e imágenes
sintéticas. Cubren creación, ambos ojos y pertenencia, formatos, archivos dañados,
límites, rutas privadas, sesión local, transacciones con error inducido, reintentos,
referencias explícitas, incompatibilidades, periodos, basal cero, reglas configuradas,
revisión, resumen médico, registros parciales y reinicio de Flask con recuperación de la imagen.

Las 41 pruebas JavaScript incluyen nueve casos OCT para apertura, conservación de
borradores y separación del simulador, respuestas atrasadas, bloqueo y reintento de
guardado, importación atrasada de otro astronauta, conservación del indicador al cambiar
de periodo, ajuste/zoom del visualizador, bloqueo hasta actualizar el resumen médico
y descarte de una importación pendiente después de guardar el borrador, además de
mediciones parciales con unidad y contexto pendientes.

La revisión visual utilizó WebView2 y una base temporal. Se verificaron menú por teclado,
imagen sintética, zoom y restablecimiento, comparación y gráfico, expediente vacío,
cambio de astronauta, importación real de los ejemplos JSON y PNG mediante los selectores
de archivos, guardado y acceso desde el resumen del expediente médico. En áreas de
navegador de 1366×768 y 520×768 se observaron imágenes ajustadas y contenido accesible
mediante desplazamiento, con desbordamiento global de 0/0 px. El monitor temporal
registró cero errores JavaScript o rechazos de promesas sin manejar durante la revisión.

Las comprobaciones realizadas corresponden al entorno local y a los formatos de prueba.
La validación con un equipo OCT físico y el uso clínico quedan pendientes.
