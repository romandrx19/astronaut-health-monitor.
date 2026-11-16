
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrate_nasa import merge_science
from persistence.sqlite import SQLiteRepository


def prepare_assets(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source = ROOT / "data/nasa/health_ares_nasa_preservada.sqlite3"
    if not source.is_file():
        source = ROOT / "data/nasa/bootstrap.sqlite3"
    target = destination / "data/nasa/bootstrap.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Cada construcción prepara un archivo nuevo y solo publica el resultado verificado.
    temporary = target.with_suffix(".building.sqlite3")
    if temporary.exists():
        raise FileExistsError(f"Hay una construcción pendiente en {temporary}")
    repository = SQLiteRepository(temporary)
    repository.initialize("all")
    merge_science(source, temporary, destination / "build-backups")
    with closing(sqlite3.connect(temporary)) as database:
        for table in ("measurements", "reading_batches", "medical_profiles", "medical_references", "health_events", "oct_exams", "oct_files"):
            if database.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
                raise ValueError(f"El recurso distribuible contiene registros locales inesperados en {table}")
        if database.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("El recurso tiene errores de claves foráneas")
    temporary.replace(target)
    # Los recursos públicos se preparan por extensión; se omiten copias Python antiguas.
    allowed = {".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}
    for path in (ROOT / "static").rglob("*"):
        if path.is_file() and path.suffix.lower() in allowed:
            output = destination / "static" / path.relative_to(ROOT / "static")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
    return target


if __name__ == "__main__":
    print(prepare_assets(ROOT / "build-assets"))
