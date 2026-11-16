# ARES HEALTH 2.0 — Historial médico individual

Esta guía describe la Fase 1. La [Fase 2](FASE2.md) incorpora la comparación de estas
referencias con la telemetría, las tendencias y el historial de eventos. Sustituye las
limitaciones de esta guía que indicaban que todavía no se hacían comparaciones.

La sección **05 / HISTORIAL MÉDICO**, situada después de ANÁLISIS, permite consultar
y guardar un expediente independiente para cada astronauta. Conserva las secciones
anteriores, la silueta, el simulador, los gráficos y las reglas educativas de alertas.

## Inicio

Desde la carpeta que contiene `app.py`, utiliza el entorno de Python ya instalado:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Si utilizas el entorno virtual de la carpeta exterior:

```powershell
..\.venv\Scripts\python.exe app.py
```

Abre `http://127.0.0.1:5000`. Si la aplicación ya estaba ejecutándose, reiníciala
y recarga la página con `Ctrl+F5` para cargar el backend, JavaScript y CSS nuevos.
El lanzador `python desktop.py` reutiliza el mismo módulo y la misma base de datos.

## Recorrido de comprobación

1. Comprueba que aparecen Valentina, Mateo y Sofía, sus indicadores y el gráfico.
2. Selecciona un astronauta y baja hasta **05 / HISTORIAL MÉDICO**. Su nombre debe
   coincidir con la selección; inicialmente no hay antecedentes ni referencias.
3. Escribe una nota identificada como demostración. Si configuras un indicador,
   completa mínimo y máximo con números finitos y mínimo menor que máximo.
   Puedes dejar ambos campos vacíos. No uses información de pacientes reales.
4. Espera una actualización de telemetría: lo escrito debe permanecer sin guardarse.
5. Pulsa **Guardar expediente**. Debe aparecer la confirmación y la fecha UTC del
   último guardado. Durante la operación se bloquean la selección y otros guardados.
6. Cambia de astronauta: su expediente debe ser independiente. Vuelve al primero
   para recuperar lo guardado. También puedes cambiar rápidamente entre perfiles.
7. Recarga la página y reinicia Flask: la información guardada debe conservarse.
8. Introduce solo un extremo o un mínimo igual/superior al máximo: debe aparecer
   un error y mantenerse el expediente anterior en la base de datos.
9. Vacía ambos extremos y guarda para dejar ese indicador sin configurar.

Cambiar de astronauta descarta los cambios **sin guardar**; se indica en el formulario.
Un fallo de guardado conserva lo escrito para volver a intentarlo. Si falla la carga,
el formulario queda deshabilitado y aparece **Reintentar carga**.

## Datos y API

`medical.py` utiliza `database.connect()`, incluidas sus claves foráneas y transacciones.
La inicialización se ejecuta después de `init_db()`. Añade únicamente:

- `medical_profiles`: un registro por astronauta con notas y fecha UTC de modificación.
- `medical_references`: una pareja mínimo/máximo por astronauta e indicador, con clave
  primaria compuesta y comprobación de orden. Una referencia vacía no ocupa registros.

Consultar un expediente vacío devuelve notas vacías, fecha `null` y referencias `{}`;
no inserta antecedentes ni valores predeterminados. Reiniciar no borra ni duplica filas.

`GET /api/astronauts/1/medical` devuelve:

```json
{"astronaut_id":1,"notes":"","updated_at":null,"references":{}}
```

`POST /api/astronauts/1/medical`, con `Content-Type: application/json`, acepta:

```json
{
  "notes": "Texto de demostración, sin información clínica real.",
  "references": {
    "heart_rate": {"min_value": 1.25, "max_value": 2.75}
  }
}
```

Los números del ejemplo sirven exclusivamente para probar almacenamiento y validación.
Los indicadores admitidos son `heart_rate`, `spo2`, `systolic`, `diastolic`, `glucose`,
`temperature` y `sleep_hours`, con las unidades ya utilizadas por la telemetría.

El POST **reemplaza el expediente completo**: omitir `notes` equivale a `""` y omitir
`references` equivale a `{}`. Los indicadores omitidos quedan sin configurar. También
se aceptan parejas vacías (`{}`, `null` o ambos extremos `null`/`""`). Si hay un valor,
ambos deben ser números JSON finitos; no se aceptan booleanos ni cadenas numéricas.
Las notas admiten hasta 20 000 caracteres. No se imponen rangos clínicos personales.

Respuestas: 200 con el expediente guardado/consultado, 400 con `error` para datos
inválidos, 404 para un astronauta inexistente y 415 si el POST no es JSON. Las notas
y todas las referencias se guardan en una única transacción. No hay rutas de borrado.

## Protección frente a solicitudes concurrentes

El frontend conserva el ID de destino, cancela cargas anteriores y verifica un contador
de solicitud y el ID recibido. Esto también evita respuestas antiguas al ir y volver
al mismo astronauta. El formulario se vacía y bloquea mientras carga otro expediente.
La actualización de telemetría y el filtro de origen no recargan el expediente.

Durante el guardado se deshabilitan los botones de tripulación, los campos del
expediente y ambos botones de registro manual. Los manejadores también comprueban
el bloqueo. La telemetría puede seguir actualizándose sin cambiar el destino del POST.

## Correcciones previas necesarias

La revisión inicial encontró dos fallos en las cuatro pruebas existentes: la función
duplicada de inserción manual devolvía un ID donde la API esperaba un registro y una
migración duplicada usaba `simulation` en lugar de `simulated`. Se conserva la
implementación original esperada por esas pruebas. No se reescriben mediciones previas.

La plantilla también tenía dos formularios con los mismos IDs y dos manejadores para
el primero. Se conservan ambos paneles, se asignan IDs únicos al segundo y se utiliza
un único manejador compartido, con un POST por envío. Se añade el ID `reading-source`
que el JavaScript ya necesitaba y se retira un carácter suelto de la plantilla.

## Pruebas automatizadas

```powershell
python -m unittest discover -s tests -v
node --check static/js/app.js
node --test tests/test_medical_frontend.cjs
```

- `tests/test_phase1.py`: cuatro pruebas originales de registros, migración y API.
- `tests/test_medical.py`: catorce pruebas de expedientes vacíos, aislamiento, validación,
  claves foráneas, no duplicación, rollback ante un fallo real de SQLite, persistencia
  desde un proceso nuevo, integridad de tablas previas y regresiones de simulación/alertas.
- `tests/test_medical_frontend.cjs`: ocho pruebas con DOM mínimo y solicitudes controladas
  para reproducir respuestas fuera de orden, bloqueos, errores, validación y conservación
  del borrador con el intervalo de cinco segundos. No requiere paquetes de Node externos.

Las pruebas usan bases temporales e información sintética. `ARES_DB_PATH` permite
aislar también una comprobación manual sin escribir en la base de trabajo.

Resultado de esta implementación: **18 pruebas de Python y 8 de JavaScript aprobadas**;
la comprobación de sintaxis `node --check` también terminó correctamente.
La revisión visual se ejecutó en una ventana pywebview independiente con una base
temporal: tres tripulantes, telemetría en actualización, formulario en dos columnas,
conservación del borrador, confirmación de guardado, expediente vacío de otro astronauta,
recuperación del expediente guardado y mensaje de error para una pareja incompleta.
La persistencia tras reiniciar se verificó con un proceso Python nuevo en la prueba
automatizada correspondiente. No se realizó una prueba en un teléfono físico.

## Alcance y limitaciones

Prototipo educativo local, sin diagnósticos ni tratamientos. No se comparan referencias
con telemetría ni se cambian las alertas. El expediente no depende del simulador ni
de una fuente de medición. No se implementa conexión con smartwatch en esta fase.

No hay autenticación, autorización por roles, cifrado, auditoría ni control de versiones
del expediente. Si dos ventanas editan el mismo expediente, prevalece el último guardado.
Antes de usar información médica real se necesitan controles de acceso y protección
de datos adicionales. Conserva `health.sqlite3`: también contiene los expedientes.
