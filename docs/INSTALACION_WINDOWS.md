# Instalación y distribución Windows

Consulta [BASES_DE_DATOS.md](BASES_DE_DATOS.md) para distinguir la fuente NASA
preservada, el bootstrap distribuible y la base operativa creada por la aplicación.

## Ejecutable

Extrae `ARESHealth-Windows-x64.zip` en una carpeta con permisos de lectura y
ejecuta `ARESHealth/ARESHealth.exe`. No ejecutes desde dentro del ZIP ni separes
el archivo de su carpeta `_internal`. No requiere instalar Python. La construcción
probada es Windows x64 con Python 3.12, pywebview 6.2.1 y PyInstaller 6.22.3.
Requiere el entorno de escritorio Windows y Microsoft Edge WebView2; en el equipo
de validación ya estaba disponible. No se ejecutó un instalador de WebView2.

La primera apertura crea `%LOCALAPPDATA%\ARESHealth\health.sqlite3`, copia solo
el arranque científico limpio y prepara lecturas simuladas identificadas. Si la
base ya existe se conserva. Al actualizar, sustituye la carpeta del programa con
la aplicación cerrada; conserva el directorio de datos. No se incluye la base
principal del desarrollador ni los perfiles usados durante las pruebas.

El bloqueo de escritorio impide otra instancia para la misma base. Cerrar la
ventana apaga el servidor. Los errores quedan en
`%LOCALAPPDATA%\ARESHealth\logs\ares-health.log` con rotación. Si no abre, comprueba
ese registro, los permisos de datos y WebView2. No hay firma digital ni instalador
MSI: esta entrega es un prototipo portátil, no un producto distribuido con firma.

Para elegir una base explícita antes de iniciar:

```powershell
$env:ARES_DB_PATH = 'C:\ruta\de\prueba\health.demo.sqlite3'
.\ARESHealth.exe
```

`ARES_DB_PATH` tiene prioridad sobre `ARES_DATA_DIR`. Los archivos OCT quedan en
`.ares-oct-demo/<nombre-base>/` junto al archivo seleccionado; respaldar ambos.
El programa no guarda datos mutables dentro de un directorio temporal de PyInstaller.

## Código fuente

Extrae `ARESHealth-source.zip`, entra en `ARESHealth-source` y ejecuta:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop.py
```

Para la versión web usa el mismo entorno y `python app.py` desde él. Abre
`http://127.0.0.1:5000`; el servidor escucha solo en localhost, sin debug por defecto.
Desde código se reutiliza `health.sqlite3` en la raíz, salvo variables de ruta.
La entrega fuente también incluye el arranque limpio para bases nuevas. No copies
la base NASA sobre una base activa ni borres el archivo para actualizar.

`requirements-lock-windows.txt` registra las versiones exactas probadas para
Windows x64/Python 3.12, incluidas herramientas opcionales de construcción y BLE.
Para reproducir todo el entorno probado puede instalarse ese archivo. Los archivos
`requirements.txt`, `requirements-desktop.txt`, `requirements-build.txt` y
`requirements-ble.txt` permiten instalar solo el conjunto necesario.

## Construcción y empaquetado

```powershell
.\build_windows.ps1 -InstallDependencies
.\.venv\Scripts\python.exe scripts/package_release.py
```

El script prepara una SQLite nueva desde las tablas científicas permitidas de
`health_ares_nasa_preservada.sqlite3`; en el ZIP fuente usa el arranque científico
incluido como alternativa. **No lee la base activa para construir el recurso.**
Comprueba ausencia de mediciones, expedientes, eventos e imágenes OCT locales.
Después PyInstaller utiliza `ARESHealth.spec` en modo carpeta, sin consola.

Resultados: `dist/ARESHealth/ARESHealth.exe`, dos ZIP en `release` y un manifiesto
con SHA-256. El empaquetador utiliza listas permitidas y verifica la integridad ZIP;
excluye entornos, Git, cachés, respaldos y bases activas. No elimina los originales.
La reconstrucción incluye recursos actuales: no reutilices un `dist` anterior si
cambiaste código, HTML, JavaScript, reglas o datos de arranque.

Antes de una demostración usa la [base aislada](GUIA_DEMOSTRACION.md). Nunca
guardes referencias ficticias de prueba sobre un expediente real. El prototipo
local no incorpora los controles necesarios para datos clínicos reales.
