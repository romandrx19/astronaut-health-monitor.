# Pruebas finales

Ejecutadas el 15 de noviembre de 2026 en Windows x64, Python 3.12 y Node.js.
Dependencias en `.venv`: Flask 3.1.3, Pillow 13.0.0, pywebview 6.2.1,
PyInstaller 6.22.3 y Bleak 3.1.0. `pip check`: sin dependencias incompatibles.
Las bases de prueba son temporales o nuevas; no se ejecutaron pruebas destructivas
contra `health.sqlite3`.

## Automatización

| Comando | Resultado ejecutado |
|---|---|
| `.venv\Scripts\python.exe -m unittest discover -s tests -q` | **152 aprobadas**, sin omitidas |
| `node --test tests/test_medical_frontend.cjs tests/test_research_frontend.cjs` | **61 aprobadas**, cero fallos y omitidas |
| `.venv\Scripts\python.exe scripts/verify_demo.py` | A–E e integridad SQLite aprobados |
| `.venv\Scripts\python.exe -m pip check` | Sin incompatibilidades |
| `scripts/prepare_build_assets.py` + PyInstaller con `ARESHealth.spec` | Ejecutable construido en Windows |

Antes de cambios: 134 Python y 59 JavaScript. Se añadieron 11 pruebas de
finalización, siete de preparación BLE y dos de frontend. Las regresiones
existentes siguen ejecutándose. Dos expectativas anteriores de simulación se
adaptaron al intervalo global: una petición inmediata ya no crea otro lote; para
probar un segundo lote se avanza el reloj seis segundos. No se eliminó cobertura.

`test_finalization.py` comprueba estado pausado, consultas disponibles, concurrencia
de ocho hilos, coordinación de dos procesos, persistencia de pausa, lote explícito,
validación, peticiones locales, 500 sin detalle sensible, bloqueo de instancia,
arranque limpio y limpieza del servidor incluso ante fallo de ventana. El registro
de error deliberado `secret-test-value` procede de una prueba del manejador 500;
no es un error de la suite ni una credencial real.

Las suites existentes verifican idempotencia concurrente, transacciones/rollback,
asociación por astronauta, procedencia, unidades, fechas, lecturas antiguas, mala
calidad, referencias individuales, eventos sin duplicados, reglas, OCT/archivos,
consultas NASA, `NULL`, paginación, SQL inválido, gráficos y respuestas atrasadas.
JavaScript también comprueba borradores por astronauta y pausa global.

`test_ble_preparation.py` usa paquetes y clientes **ficticios**, sin acceso a radio:
decodificación de Heart Rate SIG, rechazo de paquetes malformados, anuncios,
inventario, ingreso por el contrato existente, ausencias, falta de perfil y fallo
de almacenamiento. No constituye una conexión física ni verificación Roadtrip.

## Escenarios integrales A–F

| Escenario | Verificación ejecutada |
|---|---|
| A Telemetría | HTTP: tres astronautas, HTML, lote de tres, segundo lote inmediato cero, pausa e historial. UI nativa: tarjetas, sensores, conectores y gráficos de 24 lecturas; lote explícito en ejecutable final |
| B Expediente | HTTP: crear/editar referencia ficticia 60–90, aislamiento entre IDs y recuperación con otra aplicación. UI: guardado de expediente ficticio de Mateo confirmado |
| C Anomalía | HTTP: pulso ficticio 113, comparación individual, orientación y mismos IDs de eventos al reevaluar. UI: lectura manual 112 guardada, confirmación de pausa compartida y persistencia al volver a abrir |
| D Investigación | HTTP: cuatro fuentes, 52 sujetos, serie histórica. UI: conjunto CRF, participante C1:C1G0001, tarjetas con conteos, gráfico real, procedencia y aviso de SpO₂ no disponible |
| E OCT | HTTP: dos JSON de `docs/examples`, basal, comparación compatible, carga y recuperación de PNG de prueba. UI nativa nueva sin exámenes: estado vacío e imágenes ausentes |
| F Escritorio | Dos construcciones abiertas; en la final se recuperaron datos, generó un lote, consultó SQLite y cerró la ventana; el puerto dejó de escuchar y no quedó proceso ARESHealth |

El recorrido automatizado utiliza `scripts/create_demo.py` en un directorio temporal;
no acredita la interacción visual de todos los formularios. La importación OCT,
comparación y protección se comprobaron por HTTP y pruebas del frontend, no con
un equipo oftalmológico. Se observó la UI nativa maximizada a 1536×864 y la ventana
adaptada al área disponible. A menor altura existe desplazamiento en paneles;
no se verificaron físicamente todos los tamaños de pantalla.

La validación del ejecutable creó una carpeta nueva de datos de usuario separada
de la base principal. Los registros introducidos estaban marcados como ficticios.
En esa prueba: tres astronautas, 75 lecturas simuladas tras un lote, un expediente,
una lectura manual, cero OCT y 52 participantes históricos. Los ZIP no incluyen
esa carpeta. Evidencias locales bajo `backups/finalization-20261115T223521Z`:
`python-final.log`, `python-final-status.json`, `javascript-final.log`,
`e2e-final.json`, `native-final.json` y `preservation.after.json`.

Tras cerrar y comprobar ausencia del proceso/puerto, la carpeta nueva de datos
ficticios del ejecutable se trasladó íntegra a `native-runtime-preserved` dentro
de ese respaldo. No había una carpeta ARESHealth previa a la prueba. El siguiente
inicio del ejecutable crea un estado limpio; no se borraron los datos de validación.

## Preservación y entrega

Se compararon todos los registros originales, no solo cantidades: son idénticos
al respaldo. `PRAGMA integrity_check`: `ok`; `foreign_key_check`: cero errores.
No se reimportó NASA en la base activa. El arranque distribuible se verifica sin
mediciones, perfiles, referencias, eventos ni archivos OCT locales. El empaquetador
comprueba integridad ZIP y ausencia de bases activas, cachés y respaldos.

Se extrajo `ARESHealth-source.zip` en `release-smoke` bajo el respaldo y se ejecutó
desde esa copia: **152 Python, 61 JavaScript, A–E y preparación de recursos de
construcción aprobados**, todos con salida 0. Esto comprueba que el paquete fuente
funciona con su `bootstrap.sqlite3`, sin depender de la base activa o del archivo
preservado excluido del ZIP. Los registros están en `release-smoke/results.json`
y sus cuatro archivos `.log`. El ejecutable incluido coincide con el probado;
no se comprobó la instalación en una segunda computadora.

## Límites de las conclusiones

No se probó BLE físico, reloj, teléfono, puente móvil, exactitud de sensores,
equipo OCT, DICOM de producción, uso clínico ni instalación en otro PC. No se
dispone de protocolo propietario verificado, firma digital ni autenticación
multiusuario. Los resultados NASA preservan las limitaciones del proveedor:
fechas ausentes, conflictos, agregación diaria y falta de SpO₂. No se imputaron
valores ni se convirtieron promedios de investigación en límites médicos.
