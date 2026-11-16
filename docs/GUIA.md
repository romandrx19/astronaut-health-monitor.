# Guía de aprendizaje y resolución de problemas

## Cómo se integran los archivos
1. `app.py` inicia Flask y solicita la inicialización de la base a `database.py` y la carga inicial a `simulator.py`.
2. Al abrir `/`, Flask renderiza `templates/index.html`, que referencia `static/css/style.css` y `static/js/app.js`.
3. `app.js` ejecuta `fetch('/api/astronauts')`; Flask obtiene los perfiles y lecturas de SQLite vía `database.py` y evalúa sus alertas con `alerts.py`.
4. Al abrir una ficha, `app.js` pide `/api/astronauts/{id}/history?limit=36` y dibuja el gráfico usando Canvas 2D (sin dependencias externas).
5. Cada cinco segundos (si no está pausada), la página hace `POST /api/simulate`; `simulator.py` crea tres lecturas y `database.py` las almacena. Después se recarga la interfaz.

## Responsabilidad de cada archivo
- `app.py`: define métodos y rutas HTTP, valida ID y límite de historial, devuelve JSON.
- `database.py`: crea tablas relacionadas (astronauts → measurements), inserta y consulta registros usando consultas parametrizadas.
- `simulator.py`: perfiles iniciales sintéticos, pequeñas variaciones, anomalías ocasionales, `Lock` para evitar colisiones de generación en un mismo proceso.
- `alerts.py`: analiza las lecturas más recientes y asigna mensajes y recomendaciones de demostración.
- `index.html`: elementos visuales vacíos con `id` que JavaScript rellena.
- `style.css`: colores, columnas, tarjetas y diseño responsive.
- `app.js`: estado de selección y pausa, llamadas HTTP, actualización de DOM sin inyección HTML y gráfico adaptativo.
- `requirements.txt`: versión compatible de Flask; `health.sqlite3`: archivo binario local autogenerado.

## Datos iniciales
- Valentina Cruz (comandante): pulso base 72 bpm, SpO₂ 98 %, presión 116/75, glucosa 94 mg/dL, temperatura 36.7 °C, sueño 7.4 h.
- Mateo Rivera (ingeniero de vuelo): 83 bpm, SpO₂ 97 %, presión 124/80, glucosa 106 mg/dL, temperatura 36.9 °C, sueño 6.7 h.
- Sofía Nakamura (especialista científica): 66 bpm, SpO₂ 99 %, presión 110/72, glucosa 89 mg/dL, temperatura 36.5 °C, sueño 8.0 h.

Estos son **valores base**, no lecturas exactas: cada muestra incorpora variación aleatoria. Al inicio se generan 24 registros por astronauta separados por cinco minutos; las nuevas lecturas se crean cada cinco segundos con marca UTC actual.

## Errores frecuentes y soluciones
| Síntoma | Causa habitual | Acción |
|---|---|---|
| `ModuleNotFoundError: No module named 'flask'` | Falta instalar dependencias o se usa otro Python | Activa `.venv` y ejecuta `python -m pip install -r requirements.txt` |
| `python` o `py` no reconocido | Python no instalado o sin acceso desde PATH | Instala Python y comprueba `py --version` o `python --version` |
| Puerto 5000 ocupado | Otro servidor usa ese puerto | Cierra el otro proceso o cambia el puerto en `app.py` |
| `sqlite3.OperationalError: database is locked` | Otra instancia o escritura concurrente | Cierra otros servidores/pestañas; no abras la BD con una transacción de escritura sostenida |
| `404` en `/api/astronauts/99/history` | ID inválido | Usa 1, 2 o 3 |
| `400` con `limit=abc` | El parámetro debe ser un entero entre 1 y 200 | Usa `?limit=36` |
| `405 Method Not Allowed` en `/api/simulate` | Se abrió la ruta en navegador con GET | Envía POST desde el botón o desde PowerShell |
| No aparecen datos en la web | Se abrió `index.html` con `file://` o Live Server | Abre la URL servida por Flask: `http://127.0.0.1:5000` |
| Se generan demasiadas lecturas | Múltiples pestañas abren el mismo simulador | Usa una sola pestaña; `Pausar` afecta solo a esa pestaña |
| Cambios de JS/CSS no aparecen | Caché del navegador | Haz recarga forzada con `Ctrl+F5` |
| PowerShell no permite activar `.venv` | Política de ejecución | Usa directamente `\.venv\Scripts\python.exe` |
| Los valores no coinciden exactamente con los ejemplos | Se producen al azar | Es el comportamiento previsto de la simulación |

## Pruebas sugeridas
1. Abre la página: aparecen tres fichas, siete indicadores, el historial y la conexión activa.
2. Cambia de astronauta: deben cambiar indicadores y gráfico.
3. Cambia el indicador del gráfico: la serie debe representar el nuevo campo.
4. Pulsa «Generar lectura»: aumenta en uno el historial de cada astronauta.
5. Pulsa «Pausar» y observa que esa pestaña ya no envía POST automáticos.
6. Recarga la página: las lecturas previas permanecen (SQLite).
7. Abre `/api/health`: devuelve `status: ok`.
8. Prueba `/api/astronauts/99/history`: devuelve HTTP 404.
9. Prueba `/api/astronauts/1/history?limit=abc`: devuelve HTTP 400.

## Límites importantes
- La simulación se desencadena desde el navegador abierto y no emula un dispositivo de salud real.
- La base de datos es local, no hay autenticación ni autorización: diseñado solo para localhost.
- Al usar `debug=True`, Flask no debe publicarse directamente en Internet.
- Se evita usar bibliotecas externas de gráficos para entender HTML Canvas y reducir dependencias.
- La presión arterial se almacena como dos campos (sistólica/diastólica), lo que facilita filtrar y graficar.
