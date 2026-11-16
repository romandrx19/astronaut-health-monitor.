# ARES HEALTH 2.0 — Fase 2: evaluación individual y eventos

La [Fase 3](INTEGRACION_ROADTRIP.md) añade lecturas parciales y consolida los dos
formularios manuales de esta etapa en un único desplegable bajo Telemetría/Eventos.
También amplía los vínculos de eventos para conservar lecturas nuevas y antiguas.

Esta fase conecta las lecturas con las referencias guardadas en la Fase 1. Conserva
los siete indicadores, la silueta y sus sensores, la simulación cada cinco segundos,
los dos formularios manuales, los gráficos y los expedientes. No conecta un smartwatch.

## Archivos

- Nuevo `health_analysis.py`: funciones puras `evaluate_metric`, `analyze_trends`,
  `evaluate_astronaut` y `build_guidance`, reglas técnicas configurables y estados.
- Nuevo `health_events.py`: tabla e índices aditivos, consulta coherente de lecturas y
  referencias, transacciones y deduplicación por episodios de una condición.
- Modificado `app.py`: inicializa eventos después de las tablas principales y médicas;
  añade los dos endpoints sin cambiar los contratos de las rutas anteriores.
- Modificados `templates/index.html`, `static/js/app.js` y `static/css/style.css`:
  tarjetas desplegables en Eventos, cobertura de referencias, historial de episodios,
  inspector del sensor seleccionado y estados accesibles con texto, iconos y color.
- Nuevo `tests/test_health_analysis.py`; ampliado `tests/test_medical_frontend.cjs`.
- Actualizados `README.md` y las referencias a la documentación de Fase 1.

`alerts.py`, `database.py`, `medical.py`, `measurements.py`, `simulator.py` y los
lanzadores de escritorio conservan su código de la Fase 1.

## Estados y comparación

Para cada indicador se devuelve valor, unidad, referencia, comparación (`within`,
`above`, `below`, `no_reference` o `unavailable`), diferencia positiva respecto al
límite sobrepasado, fecha, descripción, orientación y análisis temporal.

| Estado | Significado |
|---|---|
| Normal según referencia | Valor dentro de la pareja configurada, incluidos los extremos. No equivale a salud clínicamente normal. |
| Sin referencia personalizada | No existe una pareja válida; nunca se inventan límites. |
| Variación detectada | Comparación fuera de referencia o patrón temporal de demostración. |
| Requiere revisión | Tres muestras consecutivas fuera de referencia bajo la regla de demostración; comprobar la captura y el contexto. |
| Atención prioritaria | Solo un protocolo explícitamente habilitado y declarado validado puede activar este estado. Ninguno viene configurado. |
| Datos insuficientes | Falta lectura, fecha con zona horaria, valor válido, suficientes muestras o un intervalo útil. Fechas futuras tampoco se evalúan como normales. |
| Lectura desactualizada | La última lectura supera la antigüedad técnica configurada. No se generan nuevos eventos a partir de ella. |

La interfaz enumera los indicadores sin referencia, incluso cuando las referencias
disponibles no muestran variaciones. Puede detectar una tendencia sin referencia;
en ese caso se identifica la fuente como **Análisis de tendencia** y se mantiene visible
que el indicador carece de referencia individual.

Las reglas originales de `alerts.py` se muestran en un grupo **Reglas educativas del
simulador · independientes**. Como en la aplicación anterior, también pueden aplicarse
a una lectura manual; la tarjeta distingue el origen de la lectura del origen de la regla.
No se usan para decidir el estado personalizado. Las respuestas de los endpoints anteriores,
incluidas las alertas educativas del registro manual, se conservan. Los contadores superiores
se etiquetan como educativos para evitar confundirlos con una evaluación clínica.

## Reglas temporales de demostración

Valores predeterminados de `AnalysisRules`, sin validación clínica:

| Parámetro | Valor | Función |
|---|---:|---|
| `history_limit` | 36 | Máximo de registros consultados por evaluación. |
| `trend_samples` | 4 | Muestras consecutivas para incremento o disminución estrictos. |
| `repeated_samples` | 3 | Muestras consecutivas fuera de referencia para revisión de la captura. |
| `max_age_seconds` | 300 | Antigüedad máxima de una lectura considerada actual. |
| `window_seconds` | 1800 | Ventana retrospectiva máxima de 30 minutos. |
| `max_gap_seconds` | 360 | Separación máxima de seis minutos entre muestras del tramo analizado. |
| `min_interval_seconds` | 1 | No construir patrones con muestras simultáneas o casi simultáneas. |
| `min_span_seconds` | 10 | Duración mínima observada para analizar un patrón. |
| `jump_range_fraction` | 0.5 | Cambio entre dos lecturas igual o mayor a la mitad de la amplitud de referencia. |

El salto solo se analiza cuando existe una referencia. Es una etiqueta de variación,
**nunca un criterio de emergencia o prioridad**. Las tendencias usan fechas con zona
horaria ordenadas cronológicamente, un único astronauta y el mismo origen que la última
lectura. Un valor inválido o un intervalo inadecuado interrumpe el tramo. No se rellenan
valores ausentes ni se mezclan lecturas manuales y simuladas para fabricar una tendencia.
La respuesta incluye número de muestras y duración observada por indicador.

Estas reglas no distinguen fisiológicamente ejercicio y reposo: la interfaz lo explica
y pide comprobar el contexto. No se infieren enfermedades, causas ni síntomas.

La configuración local se realiza antes de servir la aplicación, por ejemplo:

```python
from app import app

app.config["ARES_ANALYSIS_RULES"] = {"max_age_seconds": 600, "trend_samples": 5}
app.run(host="127.0.0.1", port=5000, debug=False)
```

`ARES_SAFETY_PROTOCOLS` es una lista vacía por defecto en `app.config`. Para que una
regla pueda activar prioridad necesita `enabled=True`, `validated=True`, `id`, `title`,
`metric`, `operator` (`above` o `below`), un `threshold` numérico finito, `approved_by`
y `validation_reference` no vacíos. La comparación usa un límite propio del protocolo;
no deduce gravedad a partir de un porcentaje de la referencia individual.

La declaración de validación y esos campos **no prueban validación médica**: el
responsable de una instalación debe verificar externamente el protocolo y su aprobación.
Esta entrega no aporta ni habilita protocolos clínicos; las pruebas usan una ficha
sintética para comprobar exclusivamente el mecanismo de configuración. No hay un
endpoint para aceptar protocolos o cambiar su validación desde el navegador.

## API

`GET /api/astronauts/1/assessment` devuelve la última lectura evaluada, `measurement_id`,
`measurement_source`, estado y mensaje general, siete objetos en `metrics`, referencias
faltantes en `missing_references`, eventos actuales en `events` y reglas aplicadas.
Acepta `?source=manual` o `?source=simulated`, igual que el panel y los gráficos.

`GET /api/astronauts/1/events` devuelve episodios persistidos del astronauta, por primera
fecha de detección descendente y luego ID descendente. Filtros opcionales:

- `metric=heart_rate` (cualquiera de los siete indicadores).
- `status=active`, `unverified`, `resolved` o `superseded`.
- `limit=50` por defecto; admite enteros de 1 a 200.

Ambos endpoints devuelven 404 para un astronauta inexistente. Los filtros inválidos
devuelven 400 con un mensaje JSON. No se añaden rutas de borrado ni se cambia ninguna
medición o expediente durante una evaluación. El navegador evita caché en estas consultas.

El GET de evaluación también registra o actualiza los episodios detectados. El GET
de historial solo consulta. No hay proceso de evaluación en segundo plano: los eventos
se registran al consultar la evaluación del astronauta, sin reconstrucción retrospectiva
de eventos para todas las mediciones almacenadas.

## Persistencia y deduplicación

`health_events` guarda los campos solicitados más origen de medición, fecha de captura,
fuentes combinadas, última fecha de muestra, contador de muestras evaluadas y clave de
condición. Hay claves foráneas a astronautas y mediciones, más comprobaciones de que
la medición pertenece al astronauta del evento. La creación de tablas, índices y
disparadores es aditiva e idempotente; no borra datos anteriores.

Una condición se identifica por astronauta, origen de medición, indicador, tipo,
fuente y referencias relevantes. Una comparación y sus tendencias relacionadas se
explican juntas en una tarjeta; las reglas educativas permanecen separadas.

Si una condición continúa con otra lectura, conserva el ID y la primera fecha de
detección; actualiza su última muestra y su detalle. Consultar otra vez la misma
lectura no incrementa el contador. Un índice único para episodios abiertos y una
transacción `BEGIN IMMEDIATE` evitan duplicados en peticiones concurrentes.

- `active`: condición presente en la evaluación correspondiente.
- `unverified`: no hay datos actuales suficientes para volver a verificar un episodio;
  no significa que la condición haya desaparecido. Puede reactivarse con el mismo ID.
- `resolved`: condición ya no detectada con los datos y configuración actuales;
  no significa recuperación clínica. Una recurrencia posterior crea un nuevo episodio.
- `superseded`: cambió la referencia que originó el evento o se desactivaron los
  protocolos de prioridad. El episodio anterior se conserva para consulta.

Evaluar el origen manual no cierra episodios simulados. El historial de la interfaz
incluye ambos orígenes y permite limitarse al indicador seleccionado.

## Arranque y comprobación de una alerta personalizada

Desde la carpeta que contiene `app.py`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Abre `http://127.0.0.1:5000`; si había un servidor o ventana de escritorio abiertos,
reinícialos y recarga la web con `Ctrl+F5`. Para escritorio sigue disponible
`python desktop.py`. Ambos reutilizan los mismos módulos y SQLite.

Recorrido exclusivamente con datos ficticios:

1. Selecciona Valentina y guarda una nota claramente identificada como demostración.
2. En su expediente configura frecuencia cardíaca mínima 60 y máxima 80. Son números
   ficticios para probar la comparación, no una recomendación de rango personal.
3. Registra una medición manual sintética: pulso 112, SpO₂ 98, sistólica 116, diastólica
   75, glucosa 94, temperatura 36.7 y sueño 7.4. El formulario existente pausa la
   simulación y selecciona el origen manual en esta pestaña.
4. En Eventos debe aparecer **Variación detectada**, valor 112 bpm, referencia 60–80,
   diferencia 32 bpm por encima del máximo y fuente **Referencia médica individual**.
5. Despliega la tarjeta para consultar explicación, orientación general, fecha y origen.
   Los otros seis indicadores deben seguir identificados como carentes de referencia.
6. Pulsa el sensor de temperatura: cambia el indicador del gráfico, aparece su valor y
   se filtran sus eventos. El sensor cardíaco recupera el evento anterior; desmarca
   **Solo indicador seleccionado** para ver todos los eventos actuales.
7. Abre **Historial de eventos del astronauta**. Repite la consulta: el mismo episodio
   no debe duplicarse. Cambia de astronauta y vuelve para comprobar el aislamiento.
8. Reinicia Flask para comprobar persistencia. Reanuda la simulación para verificar que
   las lecturas, gráficos y reglas educativas originales siguen funcionando.

Para aislar esta comprobación, establece `$env:ARES_DB_PATH` con una ruta temporal antes
de arrancar. Conserva `health.sqlite3`; ahora también almacena los eventos.

## Pruebas y límites

```powershell
python -m unittest discover -s tests -v
node --check static/js/app.js
node --test tests/test_medical_frontend.cjs
```

Las pruebas cubren límites incluidos, diferencias por encima y por debajo, referencias
ausentes/parciales, valores y fechas inválidos, escasez y antigüedad de muestras,
incrementos/disminuciones/saltos/repetición, separación de astronautas y orígenes,
protocolos deshabilitados, persistencia en un proceso nuevo, deduplicación concurrente,
guardado del expediente, simulación, selección rápida, sensores y dibujo del gráfico.
Todas usan bases temporales y datos sintéticos.

Resultado final ejecutado: **44 pruebas de Python y 16 pruebas de JavaScript aprobadas**.
`node --check static/js/app.js` también finalizó correctamente. Estas cifras incluyen
las pruebas originales de registros manuales y expedientes de la Fase 1.

La revisión visual se realizó en una ventana pywebview independiente con base temporal:
comparación ficticia 112 frente a 60–80, detalle de 32 bpm, silueta, cambio de sensor y
selector del gráfico, ausencia explícita de referencias e historial de eventos cargado.
No se probaron un smartwatch físico, un teléfono físico ni protocolos clínicos reales.

El sistema sigue siendo local y educativo: no tiene autenticación, roles, cifrado ni
auditoría clínica. No ofrece diagnósticos, medicación ni tratamiento. Requiere validación
profesional y controles de seguridad antes de utilizar información médica real.

## Fuentes y alcance de la orientación

Las reglas numéricas de tendencia son decisiones técnicas de esta demostración;
**no provienen de umbrales clínicos de NASA o FDA**. Las instrucciones generales de
verificación no sustituyen el protocolo de un dispositivo o misión.

- [FDA — Pulse Oximeter Basics](https://www.fda.gov/consumers/consumer-updates/pulse-oximeter-basics):
  contexto para las precauciones sobre SpO₂: seguir las instrucciones del fabricante,
  registrar las lecturas y considerar los síntomas junto con la medición. No se copian
  ni se aplican sus valores numéricos como referencias personales de astronautas.
- [NASA — 6.0 Medical Operations](https://www.nasa.gov/reference/6-0-medical-operations/):
  contexto de los requisitos médicos específicos de cada programa. Este enlace no
  acredita que las reglas de ARES HEALTH sean protocolos NASA validados.
