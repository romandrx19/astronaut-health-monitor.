# Inventario de bases de datos

Este documento identifica las bases SQLite que intervienen en ARES HEALTH,
su procedencia, función y política de distribución. Las bases científicas NASA
no sustituyen la base operativa y no constituyen un sistema paralelo.

## Resumen

| Archivo | Función | Distribución | Escritura en ejecución |
|---|---|---|---|
| `data/nasa/health_ares_nasa_preservada.sqlite3` | Fuente científica NASA preservada | Incluida en el código fuente final | No |
| `build-assets/data/nasa/bootstrap.sqlite3` | Arranque científico limpio para paquetes | Generada durante el build e incluida como `data/nasa/bootstrap.sqlite3` dentro de los paquetes | No |
| `health.sqlite3` | Base operativa de ARES HEALTH | Se crea en el equipo del usuario y nunca se versiona | Sí |

## Fuente científica NASA preservada

Ruta en el proyecto:

```text
data/nasa/health_ares_nasa_preservada.sqlite3
```

Es la fuente extraída del archivo recibido
`ARES_HEALTH_BASES_NASA_ADAPTADAS.zip`. El ZIP original y su `VALIDACION.json`
no están en el repositorio final; la SQLite preservada sí está incluida y permite
reproducir el recurso científico utilizado por la aplicación.

```text
Tamaño: 5.779.456 bytes
SHA-256: D38ACB264FD22238D3AE1935C2122C9F5BBFC19D06D18404F5284AD7E03D9005
```

Contiene 52 sujetos de investigación y los conjuntos `CRF`, `BRSMAct`, `MR010G`
y `MR016G`, separados de los tres astronautas ficticios de ARES HEALTH. Conserva
las tablas de signos vitales, sueño, nutrición, hidratación, laboratorio, días
de observación, procedencia y banderas de calidad. No debe abrirse como base
operativa ni copiarse sobre una `health.sqlite3` existente.

La integración aditiva y transaccional está implementada en
[`integrate_nasa.py`](../integrate_nasa.py). La procedencia detallada, conteos y
endpoints se documentan en [INVESTIGACION_NASA.md](INVESTIGACION_NASA.md).

## Bootstrap científico limpio

Ruta local generada durante una construcción:

```text
build-assets/data/nasa/bootstrap.sqlite3
```

El artefacto verificado en la integración actual tiene:

```text
Tamaño: 3.608.576 bytes
SHA-256: F422A44F37538D8F0461754F14A763343D9FD5F569A4D2C40E0432DD9D7A1258
```

[`scripts/prepare_build_assets.py`](../scripts/prepare_build_assets.py) crea una
SQLite nueva, inicializa el esquema de Leandro y fusiona únicamente las tablas
científicas permitidas. Después verifica que no contenga mediciones locales,
expedientes, eventos, OCT ni telemetría wearable.

`build-assets/` es un directorio generado y está excluido de Git. El archivo se
incorpora a las distribuciones como:

```text
data/nasa/bootstrap.sqlite3
```

En el primer arranque, si todavía no existe una base de destino, ARES HEALTH copia
este recurso mediante SQLite Backup API. Nunca reemplaza una base existente.

## Base operativa

`health.sqlite3` es la única base mutable utilizada por la aplicación. Contiene,
según corresponda, astronautas, simulación, captura manual, JG74, historial,
análisis, eventos, expedientes, OCT y las tablas científicas NASA.

La ruta se resuelve en este orden:

1. `ARES_DB_PATH`, cuando está definida.
2. `<ARES_DATA_DIR>/health.sqlite3`, cuando se configura `ARES_DATA_DIR`.
3. `%LOCALAPPDATA%/ARESHealth/health.sqlite3` en el ejecutable Windows.
4. `health.sqlite3` en la raíz durante ejecución desde código fuente.

Esta base se crea localmente, está cubierta por `*.sqlite3` en `.gitignore` y no
debe subirse a GitHub. El JG74 utiliza el mismo `PersistenceRepository` y esta
misma SQLite; no existe una base wearable independiente.

## Bases deliberadamente excluidas

No forman parte del proyecto final:

- `data/aether_nasa.sqlite` de la versión Daniel.
- `health_ares_nasa_demo.sqlite3`.
- `desktop-smoke.sqlite3`.
- Bases `health.sqlite3` de desarrollo, demostración o pruebas.
- La tabla paralela `aether_wearable_readings`.

Los datos de prueba no deben mezclarse con la fuente NASA preservada ni con la
base operativa de un usuario.

## Verificación en PowerShell

```powershell
Get-FileHash `
  .\data\nasa\health_ares_nasa_preservada.sqlite3 `
  -Algorithm SHA256

.\.venv\Scripts\python.exe scripts\prepare_build_assets.py

Get-FileHash `
  .\build-assets\data\nasa\bootstrap.sqlite3 `
  -Algorithm SHA256
```

La reconstrucción de `bootstrap.sqlite3` debe ejecutarse con la aplicación
detenida y no utiliza la base operativa activa como fuente.
