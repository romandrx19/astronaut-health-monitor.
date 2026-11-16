# ARES HEALTH 2.0 — Fase 1

## Estado y plan por etapas

Se revisaron todos los archivos de código, configuración y documentación del proyecto.
No había lanzador de escritorio ni pruebas automatizadas. El `.gitignore` ya existía
dentro del proyecto (no en la carpeta exterior que contiene el entorno virtual).

| Módulo | Estado de partida | Próximo alcance |
|---|---|---|
| Centro de control | Resumen, tres perfiles y alertas | Ampliar estado de misión |
| Telemetría | Siete indicadores, silueta y sensores | Conexiones visuales hacia indicadores |
| Autoevaluación | Ausente | Ánimo, fatiga, síntomas y bienestar |
| Bienestar y prevención | Solo mensajes básicos de alertas | Rutinas y recordatorios |
| Riesgos espaciales | Ausente | Contenido educativo con fuentes |
| Historial e informes | Gráfico reciente | Tendencias, exportaciones e informes |

Fase 1 implementada: migración aditiva → validación y API manual → formulario y
trazabilidad del origen → pruebas y lanzador de escritorio. Los demás módulos se
reservan para fases posteriores; no se han eliminado funcionalidades existentes.

## Archivos y decisiones

- `database.py`: conexiones cerradas explícitamente y migración versión 1 con
  `PRAGMA user_version`. Añade `source` sin borrar o recrear tablas. Las mediciones
  anteriores quedan como `simulated`, porque el sistema previo solo generaba simulaciones.
  La migración es repetible. La inserción manual devuelve el registro por su ID exacto.
- `measurements.py`: validación de siete valores obligatorios, finitos y numéricos;
  rechaza booleanos, texto, campos adicionales y presión invertida. Fecha UTC y origen
  se fijan en el servidor. Captura actual; no incluye edición ni fechas retroactivas.
- `app.py`: nueva API manual y filtro opcional de origen en perfiles e historial.
- `simulator.py`: explicita `source=simulated` sin cambiar la generación anterior.
- `index.html`, `app.js`, `style.css`: formulario accesible, mensajes, filtro común,
  identificación de la última lectura y tabla con fecha, origen y valor. Los puntos
  del gráfico identifican cada origen; el eje temporal respeta las fechas reales.
  Las alertas siguen siendo reglas educativas y resaltan sensores e indicadores.
- `desktop.py`, `requirements-desktop.txt`: pywebview sirve la misma aplicación local
  con puerto disponible automático y sin depuración. Cierra el servidor al cerrar la ventana.
- `tests/test_phase1.py`: pruebas contra bases temporales, sin usar datos de trabajo.

## API manual

`POST /api/astronauts/1/measurements`, `Content-Type: application/json`:

```json
{"heart_rate":72,"spo2":98,"systolic":116,"diastolic":75,"glucose":94,"temperature":36.7,"sleep_hours":7.4}
```

Respuesta 201: `measurement` con ID, fecha UTC y origen `manual`, más `alerts` educativas.
Errores: 400 para datos inválidos, 404 para astronauta desconocido, 415 para contenido
sin JSON. Ningún error de validación inserta datos. `source` no se acepta en el cuerpo.

`GET /api/astronauts?source=manual` y
`GET /api/astronauts/1/history?source=manual&limit=36` filtran por origen.
También se acepta `simulated`; omitir `source` mantiene el comportamiento previo.

Los límites técnicos son: pulso entero 1–300, SpO₂ entero 0–100, sistólica entera
1–350, diastólica entera 1–250, glucosa entera 1–1000, temperatura 25–45 y sueño
0–24. Temperatura y sueño se almacenan a una cifra decimal. No son rangos clínicos.

## Cómo comprobarlo

Desde la carpeta interior `astronaut-health-monitor`, con el entorno existente exterior:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static/js/app.js
..\.venv\Scripts\python.exe app.py
```

Abre `http://127.0.0.1:5000`. Selecciona un astronauta, completa los siete campos y
guarda: debe aparecer «MANUAL · NO VALIDADO», el registro en la tabla y la simulación
pausada en esa pestaña. Cambia de astronauta para verificar el aislamiento de registros.
Prueba presión invertida: debe aparecer un error sin guardar. Cambia el filtro a
simulados y reanuda la simulación: siguen funcionando generación, alertas e historial.
Recarga la página para comprobar persistencia. El filtro y la pausa son locales a la
pestaña y se restablecen al recargar; los registros manuales permanecen en SQLite.

Para escritorio:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
..\.venv\Scripts\python.exe desktop.py
```

Repite el recorrido anterior en la ventana. Web y escritorio comparten la misma base;
una pestaña web adicional puede seguir generando muestras. Usa el filtro manual para
consultar registros manuales independientemente de esas muestras.

La migración se aplica automáticamente al iniciar. Conserva una copia de
`health.sqlite3` con la aplicación detenida antes de actualizar una instalación con
datos importantes. `ARES_DB_PATH` permite señalar una base distinta para pruebas.

## Alcance educativo

Los perfiles son ficticios. «Manual» identifica entrada humana, no validación de un
sensor ni autenticidad clínica. No se emiten diagnósticos o tratamientos. La aplicación
mantiene su alcance local sin autenticación y no debe exponerse como sistema médico.
