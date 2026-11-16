"""Compara todos los registros de un respaldo con otra SQLite, en modo solo lectura."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def verify(before, after):
    before, after = Path(before).resolve(strict=True), Path(after).resolve(strict=True)
    with closing(sqlite3.connect(after.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("ATTACH DATABASE ? AS previous", (before.as_uri() + "?mode=ro",))
        assert connection.execute("PRAGMA main.integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA main.foreign_key_check").fetchall() == []
        tables = [row[0] for row in connection.execute("SELECT name FROM previous.sqlite_master WHERE type='table'")]
        report = {"integrity": "ok", "foreign_key_errors": 0, "original_records_preserved": True, "tables": {}}
        for table in tables:
            columns = [row[1] for row in connection.execute(f"PRAGMA previous.table_info({quote(table)})")]
            fields = ",".join(map(quote, columns))
            missing = connection.execute(f"SELECT {fields} FROM previous.{quote(table)} EXCEPT SELECT {fields} FROM main.{quote(table)} LIMIT 1").fetchone()
            assert missing is None, f"Hay registros ausentes o modificados en {table}"
            counts = [connection.execute(f"SELECT COUNT(*) FROM {schema}.{quote(table)}").fetchone()[0] for schema in ("previous", "main")]
            digests = []
            for schema in ("previous", "main"):
                digest = sha256()
                for row in connection.execute(f"SELECT {fields} FROM {schema}.{quote(table)} ORDER BY rowid"):
                    digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8") + b"\n")
                digests.append(digest.hexdigest())
            report["tables"][table] = {"before": counts[0], "after": counts[1], "identical": digests[0] == digests[1], "sha256": digests[1]}
        report["new_tables"] = [row[0] for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'") if row[0] not in tables]
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.before, args.after)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        with args.report.open("x", encoding="utf-8") as output:
            output.write(text)
    print(text)
