"""Empaqueta código y ejecutable mediante listas permitidas, sin tocar originales."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ASSETS = {".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}


def source_files():
    selected = {path for path in ROOT.glob("*.py")}
    selected.update(ROOT.glob("requirements*.txt"))
    selected.update(ROOT / name for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore", "ARESHealth.spec", "build_windows.ps1"))
    for directory, extensions in (("devices", {".py"}), ("tools", {".py"}), ("persistence", {".py"}),
                                  ("scripts", {".py"}), ("tests", {".py", ".cjs"}),
                                  ("templates", {".html"}), ("static", PUBLIC_ASSETS),
                                  ("docs", {".md", ".json", ".png"})):
        selected.update(path for path in (ROOT / directory).rglob("*")
                        if path.is_file() and path.suffix.lower() in extensions and "__pycache__" not in path.parts)
    selected.add(ROOT / "config/demo_rules.json")
    return sorted(selected)


def verify_bootstrap(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in ("measurements", "reading_batches", "reading_values", "medical_profiles", "medical_references", "health_events", "oct_exams", "oct_measurements", "oct_files"):
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0, table
        assert connection.execute("SELECT COUNT(*) FROM astronauts").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 52


def package():
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    bootstrap = ROOT / "build-assets/data/nasa/bootstrap.sqlite3"
    verify_bootstrap(bootstrap)
    source_zip = output / "ARESHealth-source.zip"
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            archive.write(path, "ARESHealth-source/" + path.relative_to(ROOT).as_posix())
        archive.write(bootstrap, "ARESHealth-source/data/nasa/bootstrap.sqlite3")
    executable_root = ROOT / "dist/ARESHealth"
    if not (executable_root / "ARESHealth.exe").is_file():
        raise FileNotFoundError("Construye primero el ejecutable con build_windows.ps1")
    windows_zip = output / "ARESHealth-Windows-x64.zip"
    with zipfile.ZipFile(windows_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(executable_root.rglob("*")):
            if path.is_file():
                archive.write(path, "ARESHealth/" + path.relative_to(executable_root).as_posix())
        archive.write(ROOT / "README.md", "ARESHealth/README.md")
        for path in sorted((ROOT / "docs").rglob("*")):
            if path.is_file() and path.suffix in {".md", ".json", ".png"}:
                archive.write(path, "ARESHealth/docs/" + path.relative_to(ROOT / "docs").as_posix())
    manifest = {"format": 1, "active_database_included": False, "bootstrap": "Tablas científicas preservadas; tres astronautas ficticios; sin mediciones, expedientes, eventos ni exámenes locales.", "artifacts": []}
    for path in (source_zip, windows_zip):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            assert archive.testzip() is None
            forbidden = {".venv", ".git", "__pycache__", "backups", ".ares-oct-demo", "logs", "build-assets"}
            assert not any(forbidden.intersection(Path(name).parts) for name in names)
            assert not any(name.endswith((".pyc", ".log", "health.sqlite3", "health.demo.sqlite3")) for name in names)
            assert all(not name.endswith(".sqlite3") or name.endswith("data/nasa/bootstrap.sqlite3") for name in names)
        manifest["artifacts"].append({"file": path.name, "bytes": path.stat().st_size, "files": len(names), "sha256": sha256(path.read_bytes()).hexdigest()})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(package(), ensure_ascii=False, indent=2))
