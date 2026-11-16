"""Adaptador SQLite del contrato de persistencia. El SQL queda dentro de este paquete."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from measurements import FIELDS
from persistence.contracts import DuplicateRequest, RepositoryUnavailable, canonical_source
from persistence import schema
from scientific_rules import load_rule_definitions


@contextmanager
def open_connection(path):
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def decode_measurement(row):
    item = {key: row[key] for key in ("id", "astronaut_id", "captured_at", *FIELDS, "source")}
    if row["kind"] == "reading":
        item.update(reading_id=row["raw_id"], received_at=row["received_at"],
                    acquired_at=row["captured_at"], device_id=row["device_id"],
                    provenance=row["provenance"], observations=row["observations"],
                    quality=json.loads(row["qualities"]), units=json.loads(row["units"]), request_id=row["request_id"])
    return item


def read_history(connection, astronaut_id, limit=36, source=None):
    source = canonical_source(source) if source is not None else None
    rows = connection.execute("""
        SELECT * FROM telemetry_measurements WHERE astronaut_id=? AND
            (? IS NULL OR CASE WHEN source IN ('simulated', 'simulation') THEN 'simulation'
                WHEN source IN ('manual', 'smartwatch') THEN source ELSE 'unknown' END = ?)
        ORDER BY julianday(captured_at) DESC, kind DESC, raw_id DESC LIMIT ?
    """, (astronaut_id, source, source, limit)).fetchall()
    return [decode_measurement(row) for row in reversed(rows)]


def read_profile(connection, astronaut_id):
    rows = connection.execute("""
        SELECT a.id AS astronaut_id, p.notes, p.updated_at, r.metric, r.min_value, r.max_value
        FROM astronauts AS a
        LEFT JOIN medical_profiles AS p ON p.astronaut_id = a.id
        LEFT JOIN medical_references AS r ON r.astronaut_id = a.id
        WHERE a.id = ? ORDER BY r.metric
    """, (astronaut_id,)).fetchall()
    if not rows:
        raise LookupError("Astronauta no encontrado")
    return {"astronaut_id": rows[0]["astronaut_id"], "notes": rows[0]["notes"] or "", "updated_at": rows[0]["updated_at"],
            "references": {row["metric"]: {"min_value": row["min_value"], "max_value": row["max_value"]}
                           for row in rows if row["metric"] is not None}}


def decode_event(row):
    event = dict(row)
    event["guidance"] = json.loads(event["guidance"])
    event["sources"] = json.loads(event["sources"])
    return event


def read_oct_documents(connection, rows):
    exams = []
    for row in rows:
        exam = dict(row)
        exam["is_demo"] = bool(exam["is_demo"])
        exam["is_baseline"] = bool(exam["is_baseline"])
        exam["measurements"] = [dict(item) for item in connection.execute(
            "SELECT * FROM oct_measurements WHERE exam_id=? ORDER BY id", (exam["id"],))]
        exam["files"] = []
        for item in connection.execute("SELECT * FROM oct_files WHERE exam_id=? ORDER BY id", (exam["id"],)):
            attachment = dict(item)
            attachment["metadata"] = json.loads(attachment["metadata"])
            exam["files"].append(attachment)
        exams.append(exam)
    return exams


class SQLiteRepository:
    def __init__(self, path=None, *, rules_path=None, connection=None, writable=False):
        self._path = Path(path) if path is not None else None
        self._rules_path = rules_path
        self._connection = connection
        self._writable = writable

    @property
    def path(self):
        if self._path is not None:
            return self._path
        # Compatibilidad con ARES_DB_PATH y con los consumidores anteriores de database.DB_PATH.
        from database import DB_PATH
        return Path(DB_PATH)

    def initialize(self, scope="all"):
        scopes = {"telemetry": (schema.init_core, schema.init_acquisition), "medical": (schema.init_medical,),
                  "events": (schema.init_events,), "oct": (schema.init_oct,)}
        if self._connection is not None:
            raise RuntimeError("Inicializa las migraciones antes de abrir una transacción de negocio")
        selected = tuple(scopes) if scope == "all" else (scope,)
        if any(name not in scopes for name in selected):
            raise ValueError("Ámbito de inicialización desconocido")
        with open_connection(self.path) as connection:
            for name in selected:
                for initialize in scopes[name]:
                    initialize(connection)

    @contextmanager
    def transaction(self, *, write=False):
        if self._connection is not None:
            if write and not self._writable:
                raise RuntimeError("La sesión fue abierta para lectura")
            yield self
            return
        try:
            with open_connection(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield SQLiteRepository(self.path, rules_path=self._rules_path, connection=connection, writable=write)
        except sqlite3.OperationalError as error:
            raise RepositoryUnavailable("No se pudo completar el acceso al almacenamiento SQLite") from error

    @contextmanager
    def _session(self, *, write=False):
        with self.transaction(write=write) as session:
            yield session._connection

    def list_astronauts(self):
        with self._session() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM astronauts ORDER BY id")]

    def get_astronaut(self, astronaut_id):
        with self._session() as connection:
            row = connection.execute("SELECT * FROM astronauts WHERE id=?", (astronaut_id,)).fetchone()
            return dict(row) if row else None

    def _require_astronaut(self, connection, astronaut_id):
        if connection.execute("SELECT 1 FROM astronauts WHERE id=?", (astronaut_id,)).fetchone() is None:
            raise LookupError("Astronauta no encontrado")

    def get_history(self, astronaut_id, limit=36, source=None):
        with self._session() as connection:
            return read_history(connection, astronaut_id, limit, source)

    def has_measurements(self):
        with self._session() as connection:
            return connection.execute("SELECT EXISTS(SELECT 1 FROM measurements) OR EXISTS(SELECT 1 FROM reading_batches)").fetchone()[0] == 1

    def get_simulation_state(self):
        with self._session() as connection:
            row = dict(connection.execute("SELECT running,last_generated_at,revision FROM simulation_state WHERE id=1").fetchone())
            row["running"] = bool(row["running"])
            return row

    def save_simulation_state(self, state):
        with self._session(write=True) as connection:
            connection.execute("UPDATE simulation_state SET running=?,last_generated_at=?,revision=? WHERE id=1",
                               (state["running"], state["last_generated_at"], state["revision"]))

    def add_measurements(self, items):
        with self._session(write=True) as connection:
            connection.executemany("""INSERT INTO measurements
                (astronaut_id, captured_at, heart_rate, spo2, systolic, diastolic, glucose, temperature, sleep_hours, source)
                VALUES (:astronaut_id, :captured_at, :heart_rate, :spo2, :systolic, :diastolic, :glucose, :temperature, :sleep_hours, :source)""",
                [{**item, "source": item.get("source", "simulated")} for item in items])

    def add_manual_measurement(self, sample):
        with self._session(write=True) as connection:
            fields = ("astronaut_id", "captured_at", "heart_rate", "spo2", "systolic", "diastolic", "glucose", "temperature", "sleep_hours", "source")
            cursor = connection.execute(f"INSERT INTO measurements ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", [sample[field] for field in fields])
            return dict(connection.execute("SELECT * FROM measurements WHERE id=?", (cursor.lastrowid,)).fetchone())

    def store_reading(self, reading, request_id):
        with self._session(write=True) as connection:
            self._require_astronaut(connection, reading.astronaut_id)
            previous = connection.execute("""SELECT id, fingerprint FROM reading_batches
                WHERE astronaut_id=? AND source=? AND request_id=?""", (reading.astronaut_id, reading.source, request_id)).fetchone()
            if previous:
                if previous["fingerprint"] != reading.fingerprint:
                    raise DuplicateRequest("La clave de solicitud ya se utilizó para una medición diferente")
                row = connection.execute("SELECT * FROM telemetry_measurements WHERE kind='reading' AND raw_id=?", (previous["id"],)).fetchone()
                return decode_measurement(row), False
            cursor = connection.execute("""INSERT INTO reading_batches
                (astronaut_id, acquired_at, received_at, source, device_id, provenance, observations, request_id, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (reading.astronaut_id, reading.acquired_at, reading.received_at,
                    reading.source, reading.device_id, reading.provenance, reading.observations, request_id, reading.fingerprint))
            batch_id = cursor.lastrowid
            connection.executemany("INSERT INTO reading_values (batch_id, metric, value, unit, quality) VALUES (?, ?, ?, ?, ?)",
                [(batch_id, metric, value["value"], value["unit"], value["quality"]) for metric, value in reading.metrics.items()])
            row = connection.execute("SELECT * FROM telemetry_measurements WHERE kind='reading' AND raw_id=?", (batch_id,)).fetchone()
            return decode_measurement(row), True

    def get_medical_profile(self, astronaut_id):
        with self._session() as connection:
            return read_profile(connection, astronaut_id)

    def save_medical_profile(self, astronaut_id, notes, references, updated_at):
        with self._session(write=True) as connection:
            self._require_astronaut(connection, astronaut_id)
            connection.execute("""INSERT INTO medical_profiles (astronaut_id, notes, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(astronaut_id) DO UPDATE SET notes=excluded.notes, updated_at=excluded.updated_at""", (astronaut_id, notes, updated_at))
            connection.execute("DELETE FROM medical_references WHERE astronaut_id=?", (astronaut_id,))
            connection.executemany("INSERT INTO medical_references (astronaut_id, metric, min_value, max_value) VALUES (?, ?, ?, ?)",
                [(astronaut_id, metric, reference["min_value"], reference["max_value"]) for metric, reference in references.items()])
            return read_profile(connection, astronaut_id)

    def get_open_events(self, astronaut_id, source):
        with self._session() as connection:
            return [decode_event(row) for row in connection.execute("""SELECT * FROM health_events WHERE astronaut_id=?
                AND measurement_source=? AND status IN ('active', 'unverified')""", (astronaut_id, source))]

    def insert_health_event(self, event):
        fields = ("astronaut_id", "measurement_id", "metric", "event_type", "severity", "current_value", "reference_min", "reference_max",
                  "description", "guidance", "source", "sources", "measurement_source", "captured_at", "detected_at", "last_seen_at",
                  "condition_key", "status", "reading_id", "reading_key")
        record = {**event, "guidance": json.dumps(event["guidance"], ensure_ascii=False), "sources": json.dumps(event["sources"])}
        with self._session(write=True) as connection:
            cursor = connection.execute(f"INSERT INTO health_events ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", [record[field] for field in fields])
            return cursor.lastrowid

    def update_health_event(self, event_id, astronaut_id, changes):
        record = dict(changes)
        for field in ("guidance", "sources"):
            if field in record:
                record[field] = json.dumps(record[field], ensure_ascii=False)
        with self._session(write=True) as connection:
            cursor = connection.execute(f"UPDATE health_events SET {', '.join(field + '=?' for field in record)} WHERE id=? AND astronaut_id=?", [*record.values(), event_id, astronaut_id])
            if cursor.rowcount != 1:
                raise LookupError("Evento no encontrado para este astronauta")

    def list_health_events(self, astronaut_id, limit=50, metric=None, status=None):
        with self._session() as connection:
            self._require_astronaut(connection, astronaut_id)
            rows = connection.execute("""SELECT * FROM health_events WHERE astronaut_id=? AND (? IS NULL OR metric=?)
                AND (? IS NULL OR status=?) ORDER BY detected_at DESC, id DESC LIMIT ?""", (astronaut_id, metric, metric, status, status, limit)).fetchall()
            return [decode_event(row) for row in rows]

    def list_oct_exams(self, astronaut_id):
        with self._session() as connection:
            self._require_astronaut(connection, astronaut_id)
            rows = connection.execute("SELECT * FROM oct_exams WHERE astronaut_id=? ORDER BY exam_date DESC, id DESC", (astronaut_id,)).fetchall()
            return read_oct_documents(connection, rows)

    def get_oct_exam(self, exam_id, astronaut_id):
        with self._session() as connection:
            row = connection.execute("SELECT * FROM oct_exams WHERE id=? AND astronaut_id=?", (exam_id, astronaut_id)).fetchone()
            if row is None:
                raise LookupError("Examen OCT no encontrado para este astronauta")
            return read_oct_documents(connection, [row])[0]

    def find_oct_request(self, request_key):
        with self._session() as connection:
            row = connection.execute("SELECT * FROM oct_exams WHERE request_key=?", (request_key,)).fetchone()
            return read_oct_documents(connection, [row])[0] if row else None

    def insert_oct_exam(self, exam, measurements):
        fields = ("astronaut_id", "eye", "exam_date", "mission_phase", "device_model", "scan_protocol", "image_quality", "is_baseline",
                  "review_status", "reviewed_by", "reviewed_at", "review_notes", "notes", "provenance", "source_document", "is_demo",
                  "request_key", "request_hash", "created_at", "updated_at")
        with self._session(write=True) as connection:
            cursor = connection.execute(f"INSERT INTO oct_exams ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", [exam[field] for field in fields])
            exam_id = cursor.lastrowid
            connection.executemany("""INSERT INTO oct_measurements
                (exam_id, metric, value, unit, anatomical_region, measurement_method) VALUES (?, ?, ?, ?, ?, ?)""",
                [(exam_id, item["metric"], item["value"], item["unit"], item["anatomical_region"], item["measurement_method"]) for item in measurements])
            return exam_id

    def update_oct_exam(self, exam_id, astronaut_id, changes):
        with self._session(write=True) as connection:
            cursor = connection.execute(f"UPDATE oct_exams SET {', '.join(field + '=?' for field in changes)} WHERE id=? AND astronaut_id=?", [*changes.values(), exam_id, astronaut_id])
            if cursor.rowcount != 1:
                raise LookupError("Examen OCT no encontrado para este astronauta")
            row = connection.execute("SELECT * FROM oct_exams WHERE id=?", (exam_id,)).fetchone()
            return read_oct_documents(connection, [row])[0]

    def add_oct_files(self, exam_id, attachments):
        with self._session(write=True) as connection:
            connection.executemany("""INSERT INTO oct_files
                (exam_id, file_path, preview_path, file_type, original_name, checksum, size_bytes, metadata, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(exam_id, item["file_path"], item["preview_path"], item["file_type"], item["original_name"], item["checksum"],
                  item["size_bytes"], json.dumps(item["metadata"], ensure_ascii=False), item["uploaded_at"]) for item in attachments])

    def get_oct_file(self, file_id, astronaut_id):
        with self._session() as connection:
            row = connection.execute("""SELECT f.* FROM oct_files f JOIN oct_exams e ON e.id=f.exam_id
                WHERE f.id=? AND e.astronaut_id=?""", (file_id, astronaut_id)).fetchone()
            if row is None:
                raise LookupError("Archivo OCT no encontrado para este astronauta")
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            return item

    def get_rule_definitions(self):
        return load_rule_definitions(self._rules_path)

    def oct_storage_root(self):
        return self.path.parent / ".ares-oct-demo" / self.path.stem
