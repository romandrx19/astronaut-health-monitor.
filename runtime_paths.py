"""Rutas persistentes y bloqueo de procesos para la aplicación local y empaquetada."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4


class AlreadyRunning(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            self.file = None
            raise AlreadyRunning("La aplicación ya está abierta para esta base de datos") from error
        return self

    def __exit__(self, *exc):
        if self.file is not None:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def database_path():
    if os.environ.get("ARES_DB_PATH"):
        return Path(os.environ["ARES_DB_PATH"]).expanduser().resolve()
    if os.environ.get("ARES_DATA_DIR"):
        return Path(os.environ["ARES_DATA_DIR"]).expanduser().resolve() / "health.sqlite3"
    if getattr(sys, "frozen", False):
        directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "ARESHealth"
        return directory / "health.sqlite3"
    return resource_root() / "health.sqlite3"


def configure_runtime():
    target = database_path()
    if getattr(sys, "frozen", False) or os.environ.get("ARES_DATA_DIR"):
        os.environ["ARES_DB_PATH"] = str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    seed = resource_root() / "data/nasa/bootstrap.sqlite3"
    if seed.is_file() and not target.exists():
        # El bloqueo protege la comprobación y la publicación atómica de la copia completa.
        with ProcessLock(target.with_suffix(".initialization.lock")):
            if not target.exists():
                temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                try:
                    with closing(sqlite3.connect(seed.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(temporary)) as destination:
                        source.backup(destination)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
    return target
