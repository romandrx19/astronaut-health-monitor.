# Arquitectura de finalización

Se conserva Flask → servicios Python → `PersistenceRepository` → SQLite, con
HTML/CSS/JavaScript existentes. `database.py`, los motores de análisis, las reglas
y el diseño de telemetría se mantienen. El escritorio reutiliza Flask y las mismas
rutas; no hay otro backend ni otro motor de alertas.

## Flujo de una medición

1. El formulario manual fija astronauta en la URL, valida entradas y conserva una
   clave de idempotencia durante los reintentos.
2. `devices/normalizer.py` mantiene unidades, calidad, adquisición, recepción y
   procedencia. No completa indicadores ausentes.
3. `acquisition.py` guarda paquete y valores con el repositorio existente. SQLite
   serializa escrituras e impide duplicar una clave para astronauta/origen.
4. `health_analysis.py` compara referencias individuales y tendencias comparables;
   `health_events.py` reconcilia episodios sin crear uno por cada consulta.
5. `science_service.py` y `scientific_rules.py` suministran reglas declaradas.
   El frontend muestra explicación, orientación, fuente, fecha y cobertura.
6. Telemetría e historial consultan los valores almacenados, no los generan para
   dibujar gráficos. Lecturas pobres/inválidas y `NULL` no se dibujan como cero.

Investigación consulta tablas/vistas científicas en una transacción de solo
lectura. Los 52 participantes no entran en `astronauts`. Sus estadísticas no
configuran límites de los expedientes ni disparan diagnósticos.

## Simulación compartida

Se añade `simulation_state`, una fila con `running`, `last_generated_at` y
`revision`. Es una migración aditiva; no altera filas anteriores. La decisión
del intervalo y las tres inserciones ocurren dentro de **la misma transacción
`BEGIN IMMEDIATE`**. Un bloqueo Python adicional coordina hilos del mismo proceso;
SQLite coordina procesos distintos que utilizan el mismo archivo.

| Ruta | Contrato |
|---|---|
| `GET /api/simulation` | Estado global, revisión e intervalo de cinco segundos |
| `POST /api/simulation` | JSON exacto `{"running": true}` o `false`; pausa/reanudación persistente |
| `POST /api/simulate` | JSON `{"automatic": true}` requiere estado activo; sin JSON o con `false` es un lote explícito |

La última ruta devuelve `created`, `samples`, `simulation`: 201 si creó tres
lecturas, 200 si creó cero por pausa/intervalo. La revisión impide que una respuesta
atrasada cambie un estado más reciente en el navegador. Los clientes antiguos deben
recargarse: las llamadas sin JSON son solicitudes explícitas por compatibilidad,
aunque todas respetan el mismo límite de frecuencia.

`static/js/simulation.js` consulta el estado en el intervalo ya existente. Las
consultas siguen activas al pausar. No hay procesos generadores, temporizadores
duplicados ni lotes retroactivos. Persistir `running=true` permite continuar la
demostración tras reiniciar; las bases nuevas empiezan pausadas. El intervalo es
un mínimo entre lotes, no una garantía de tiempo real.

Seleccionar manual/smartwatch o guardar en el formulario manual solicita pausa
global. Un lote solicitado explícitamente o pulsar Reanudar constituye activación
de demo. El backend de ingreso manual no altera el estado de simulación por sí
solo: los clientes API externos deben solicitar la pausa si la necesitan.

## Rutas, archivos y ventana nativa

`runtime_paths.py` prioriza `ARES_DB_PATH`, después `ARES_DATA_DIR`; en ejecutable
usa `%LOCALAPPDATA%/ARESHealth`, y en código fuente la raíz existente. El recurso
de arranque científico solo se copia si falta el destino, con SQLite Backup API,
bloqueo de inicialización y publicación atómica. No sobrescribe bases existentes.

`desktop.py` bloquea una segunda instancia de escritorio para la misma base,
crea un único servidor Werkzeug en `127.0.0.1` con puerto disponible y abre pywebview.
El tamaño inicial se ajusta a la pantalla. `finally` apaga, cierra y espera al hilo
del servidor incluso si falla la ventana. Los registros rotan junto a la base.

Las plantillas, recursos, reglas y el arranque científico están en `_internal`;
SQLite y archivos OCT mutables están fuera del ejecutable. OCT mantiene su
almacenamiento privado, comprobación de propietario, límites y token de sesión.

## Seguridad y alcance

`local_security.py` valida Host local y, para escrituras, dirección loopback,
Origin del mismo servidor y `Sec-Fetch-Site`. No habilita CORS. Los errores 500
devuelven un mensaje genérico; los detalles quedan en el registro local. Se limita
el cuerpo global a 51 MiB y se conservan los límites específicos OCT. Debug está
desactivado salvo configuración explícita del punto de entrada web.

La evaluación consultada por GET puede reconciliar eventos: este comportamiento
existente está documentado y no equivale a una API completamente sin efectos.
La protección de origen es proporcional a una demo local, no autenticación contra
otros programas del mismo equipo. Para uso clínico faltan identidad, permisos,
auditoría operacional y validación médica. Referencias técnicas:
[Flask](https://flask.palletsprojects.com/en/stable/web-security/),
[PyInstaller](https://pyinstaller.org/en/stable/runtime-information.html) y
[pywebview](https://pywebview.flowrl.com/guide/freezing).
