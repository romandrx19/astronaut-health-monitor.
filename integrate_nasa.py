"""Fusión aditiva de las tablas científicas; nunca importa perfiles ni telemetría ARES."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

TABLES = ("subjects", "dataset_sources", "observation_days", "vital_signs", "sleep",
          "nutrition", "hydration", "lab_results", "quality_flags", "ares_nasa_import_manifest")
VIEWS = ("v_vital_daily", "v_sleep_daily", "v_nutrition_daily", "v_hydration_daily", "v_lab_daily",
         "v_aether_daily", "v_aether_demo", "v_ares_nasa_daily", "v_ares_nasa_demo_daily")


class MergeConflict(ValueError):
    pass


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def check_integrity(connection, schema="main"):
    if connection.execute(f"PRAGMA {schema}.integrity_check").fetchall() != [("ok",)]:
        raise MergeConflict(f"Integridad SQLite incorrecta: {schema}")
    if connection.execute(f"PRAGMA {schema}.foreign_key_check").fetchall():
        raise MergeConflict(f"Claves foráneas incorrectas: {schema}")


def table_digest(connection, table):
    digest = sha256()
    count = 0
    for row in connection.execute(f"SELECT * FROM main.{quote(table)} ORDER BY rowid"):
        digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8") + b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def backup_database(target, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = directory / f"{target.stem}.before-nasa-{stamp}-{uuid4().hex[:6]}.sqlite3"
    backup.touch(exist_ok=False)
    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(backup)) as copy:
        source.backup(copy)
        check_integrity(copy)
    return backup


def merge_science(source, target, backup_dir):
    source, target = Path(source).resolve(strict=True), Path(target).resolve(strict=True)
    if source == target:
        raise ValueError("La fuente científica y la base activa deben ser archivos distintos")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as incoming:
        check_integrity(incoming)
        names = {name for name, in incoming.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES).issubset(names):
            raise ValueError("La fuente no contiene el esquema científico preservado esperado")
        # La variante demo se rechaza incluso si alguien la pasa accidentalmente al CLI.
        if incoming.execute("SELECT COUNT(*) FROM astronauts").fetchone()[0] != 3:
            raise ValueError("Usa health_ares_nasa_preservada.sqlite3, con solo tres perfiles ARES")
    backup = backup_database(target, backup_dir)
    report = {"source": str(source), "source_sha256": sha256(source.read_bytes()).hexdigest(),
              "target": str(target), "backup": str(backup), "backup_sha256": sha256(backup.read_bytes()).hexdigest(),
              "created_at": datetime.now(timezone.utc).isoformat(), "inserted": {}}
    with closing(sqlite3.connect(target, timeout=30, uri=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS incoming", (source.as_uri() + "?mode=ro",))
        try:
            connection.execute("BEGIN IMMEDIATE")
            local_tables = [name for name, in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
                            if name not in TABLES]
            before = {name: table_digest(connection, name) for name in local_tables}
            for table in TABLES:
                sql = connection.execute("SELECT sql FROM incoming.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                previous = connection.execute("SELECT sql FROM main.sqlite_master WHERE name=?", (table,)).fetchone()
                if previous:
                    if re.sub(r"\s+", " ", previous[0]).strip() != re.sub(r"\s+", " ", sql).strip():
                        raise MergeConflict(f"Esquema científico diferente: {table}; no se modificó la base")
                else:
                    connection.execute(sql)
                columns = connection.execute(f"PRAGMA incoming.table_info({quote(table)})").fetchall()
                fields = [item[1] for item in columns]
                keys = [item[1] for item in sorted(columns, key=lambda item: item[5]) if item[5]]
                if not keys:
                    raise MergeConflict(f"La tabla {table} necesita una clave para fusionarse sin duplicados")
                same_key = " AND ".join(f"local.{quote(key)} IS remote.{quote(key)}" for key in keys)
                different = " OR ".join(f"local.{quote(field)} IS NOT remote.{quote(field)}" for field in fields)
                conflict = connection.execute(f"SELECT 1 FROM main.{quote(table)} local JOIN incoming.{quote(table)} remote ON {same_key} WHERE {different} LIMIT 1").fetchone()
                if conflict:
                    raise MergeConflict(f"Conflicto de contenido en {table}; se conserva la base local y se revierte toda la fusión")
                field_list = ", ".join(quote(field) for field in fields)
                connection.execute(f"INSERT INTO main.{quote(table)} ({field_list}) SELECT {', '.join('remote.' + quote(field) for field in fields)} FROM incoming.{quote(table)} remote WHERE NOT EXISTS (SELECT 1 FROM main.{quote(table)} local WHERE {same_key})")
                report["inserted"][table] = connection.execute("SELECT changes()").fetchone()[0]
            placeholders = ",".join("?" for _ in TABLES)
            objects = connection.execute(f"SELECT name,sql FROM incoming.sqlite_master WHERE (type='index' AND sql IS NOT NULL AND tbl_name IN ({placeholders})) OR (type='view' AND name IN ({','.join('?' for _ in VIEWS)}))", (*TABLES, *VIEWS)).fetchall()
            for name, sql in objects:
                previous = connection.execute("SELECT sql FROM main.sqlite_master WHERE name=?", (name,)).fetchone()
                if previous and re.sub(r"\s+", " ", previous[0]).strip() != re.sub(r"\s+", " ", sql).strip():
                    raise MergeConflict(f"Definición local diferente: {name}")
                if not previous:
                    connection.execute(sql)
            after = {name: table_digest(connection, name) for name in local_tables}
            if before != after:
                raise MergeConflict("La fusión alteró tablas locales; operación revertida")
            check_integrity(connection)
            for view in VIEWS:
                connection.execute(f"SELECT * FROM {quote(view)} LIMIT 1").fetchall()
            report.update(local_before=before, local_after=after, integrity="ok", foreign_key_errors=0)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    report_path = backup.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report"] = str(report_path)
    return report


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    source = root / "data/nasa/health_ares_nasa_preservada.sqlite3"
    if not source.is_file():
        source = source.with_name("bootstrap.sqlite3")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=source)
    parser.add_argument("--target", type=Path, default=Path(os.environ.get("ARES_DB_PATH", root / "health.sqlite3")))
    parser.add_argument("--backup-dir", type=Path, default=root / "backups")
    args = parser.parse_args()
    print(json.dumps(merge_science(args.source, args.target, args.backup_dir), ensure_ascii=False, indent=2))
