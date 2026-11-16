# Prueba física Roadtrip Watch 4 JG74

Esta prueba manual valida BLE, normalización, persistencia e historial de frecuencia
cardíaca real. No se ejecuta dentro de `pytest`, no interpreta clínicamente el BPM
y no valida métricas distintas de `heart_rate`.

## Preparación

1. Usa Windows 11 con Bluetooth activo y el Roadtrip Watch 4 JG74 cargado y despierto.
2. Desde la raíz de ARES HEALTH crea o activa el entorno e instala las dependencias:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m pip install -r requirements-ble.txt
   ```

3. Opcionalmente inspecciona el reloj antes de iniciar Flask. No publiques el JSON
   generado sin anonimizar el identificador BLE:

   ```powershell
   .\.venv\Scripts\python.exe -m tools.ble_inspector --scan-time 10 --notify 0x2A37 --notify-time 20
   ```

4. Inicia ARES HEALTH con una base aislada y el intervalo definitivo de 5 segundos:

   ```powershell
   $env:ARES_DB_PATH = Join-Path $env:TEMP 'ares-jg74-hardware.sqlite3'
   $env:ARES_BLE_STORE_INTERVAL_SECONDS = '5'
   .\.venv\Scripts\python.exe app.py
   ```

La aplicación usa esa misma SQLite para simulación, manual, smartwatch, historial,
análisis y eventos. No crea `aether_wearable_readings` ni otra base wearable.

## Flujo completo

5. Comprueba que el astronauta entero existe:

   ```powershell
   Invoke-RestMethod 'http://127.0.0.1:5000/api/astronauts' |
     ConvertTo-Json -Depth 6
   ```

6. Conecta por descubrimiento automático y asigna el reloj al astronauta 1:

   ```powershell
   $body = @{ device_id = 'auto'; astronaut_id = 1 } | ConvertTo-Json
   Invoke-RestMethod -Method Post `
     -Uri 'http://127.0.0.1:5000/api/devices/connect' `
     -ContentType 'application/json' -Body $body |
     ConvertTo-Json -Depth 6
   ```

   Si hay varios JG74, sustituye `auto` por el identificador BLE observado localmente.
   No incluyas ese identificador privado en documentación o capturas públicas.

7. Inicia una medición de pulso en el reloj si el firmware solo emite 0x2A37 bajo
   demanda. Consulta el estado hasta ver `receiving_telemetry`:

   ```powershell
   Invoke-RestMethod 'http://127.0.0.1:5000/api/devices/status' |
     ConvertTo-Json -Depth 8
   ```

   `connected` no es suficiente. Solo una notificación válida debe producir:

   ```text
   state = receiving_telemetry
   telemetry_confirmed = true
   verified_physical = true
   ```

8. Consulta la lectura más reciente en memoria:

   ```powershell
   Invoke-RestMethod 'http://127.0.0.1:5000/api/devices/latest' |
     ConvertTo-Json -Depth 8
   ```

   Debe mostrar `astronaut_id=1`, `source=smartwatch`,
   `provenance=verified_device`, un `device_id` real y `heart_rate` en `bpm`.

9. Mantén o repite mediciones durante más de 10 segundos. Las notificaciones pueden
   actualizar `latest` continuamente, pero SQLite recibe como máximo una lectura por
   cada intervalo configurado; no se promedian ni inventan valores.

10. Consulta el historial smartwatch:

    ```powershell
    Invoke-RestMethod `
      'http://127.0.0.1:5000/api/astronauts/1/history?source=smartwatch&limit=200' |
      ConvertTo-Json -Depth 8
    ```

    Confirma varias filas en orden cronológico con `source=smartwatch`,
    `provenance=verified_device`, `device_id`, `heart_rate` y unidad `bpm`.

11. En la interfaz cambia **Origen visible** a Manual o Simulación (y navega a
    Investigación NASA si lo deseas). Mantén el reloj conectado y repite mediciones.
    El selector solo cambia consultas y presentación; nunca desconecta ni pausa JG74.

12. Vuelve a **Smartwatch verificado** y confirma que aparecen lecturas nuevas
    acumuladas durante el paso anterior.

13. Desconecta de forma explícita:

    ```powershell
    Invoke-RestMethod -Method Post `
      'http://127.0.0.1:5000/api/devices/disconnect' |
      ConvertTo-Json -Depth 8
    ```

14. Comprueba `state=disconnected` y que ya no se insertan lecturas.
15. Verifica que ARES HEALTH continúa operativo:

    ```powershell
    Invoke-RestMethod 'http://127.0.0.1:5000/api/health'
    Invoke-RestMethod 'http://127.0.0.1:5000/api/astronauts/1/history?limit=5'
    Invoke-RestMethod 'http://127.0.0.1:5000/api/research/datasets'
    ```

## Resultado que debe registrarse

Documenta fecha, Windows, versión de firmware, versión de Bleak, estado 0x180D,
estado 0x2A37, número de notificaciones válidas/rechazadas, filas persistidas y
resultado de desconexión. Anonimiza dirección, UUID de sistema, serial y cualquier
otro identificador privado antes de compartir el informe.

Si el reloj se aleja o Bluetooth se apaga, el manager pasa a `reconnecting` y aplica
reintentos limitados. Una interrupción no genera valores sintéticos. Si Bleak no está
instalado, el estado es `ble_unavailable` y el resto de ARES HEALTH sigue funcionando.
