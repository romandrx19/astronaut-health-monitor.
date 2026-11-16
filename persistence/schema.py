"""Migraciones SQLite existentes, agrupadas sin modificar sus tablas ni datos."""
from measurements import FIELDS

ASTRONAUTS = [
    (1, "Valentina Cruz", "Comandante", "Misión Ares-01"),
    (2, "Mateo Rivera", "Ingeniero de vuelo", "Misión Ares-01"),
    (3, "Sofía Nakamura", "Especialista científica", "Misión Ares-01"),
]

def init_core(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS astronauts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            mission TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            astronaut_id INTEGER NOT NULL REFERENCES astronauts(id),
            captured_at TEXT NOT NULL,
            heart_rate INTEGER NOT NULL,
            spo2 INTEGER NOT NULL,
            systolic INTEGER NOT NULL,
            diastolic INTEGER NOT NULL,
            glucose INTEGER NOT NULL,
            temperature REAL NOT NULL,
            sleep_hours REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_measurements_astronaut_time
            ON measurements(astronaut_id, captured_at DESC, id DESC);
    """)

    connection.executemany(
        "INSERT OR IGNORE INTO astronauts (id, name, role, mission) VALUES (?, ?, ?, ?)",
        ASTRONAUTS,
    )
    # Migración aditiva e idempotente: conserva IDs, fechas y valores previos.
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(measurements)")}
    if "source" not in columns:
        connection.execute("ALTER TABLE measurements ADD COLUMN source TEXT NOT NULL DEFAULT 'simulated' CHECK(source IN ('simulated', 'manual'))")
    connection.execute("PRAGMA user_version = 1")


def init_acquisition(connection):
    # Coordinación persistente entre pestañas, procesos web y escritorio.
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS simulation_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            running INTEGER NOT NULL DEFAULT 0 CHECK(running IN (0,1)),
            last_generated_at TEXT,
            revision INTEGER NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO simulation_state(id,running,revision) VALUES (1,0,0);
    """)
    metrics = ", ".join(repr(key) for key in FIELDS)
    legacy_fields = ", ".join(FIELDS)
    partial_fields = ", ".join(f"MAX(CASE WHEN v.metric='{key}' THEN v.value END) AS {key}" for key in FIELDS)
    connection.executescript(f"""
        CREATE TABLE IF NOT EXISTS reading_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            astronaut_id INTEGER NOT NULL REFERENCES astronauts(id),
            acquired_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('simulation', 'manual', 'smartwatch')),
            device_id TEXT,
            provenance TEXT NOT NULL CHECK(provenance IN ('manual', 'simulation', 'adapter_test', 'imported', 'verified_device')),
            observations TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(astronaut_id, source, request_id),
            CHECK(source != 'smartwatch' OR (provenance='verified_device' AND device_id IS NOT NULL)),
            CHECK(provenance NOT IN ('simulation', 'adapter_test') OR source='simulation'),
            CHECK(provenance != 'imported' OR source='manual')
        );
        CREATE INDEX IF NOT EXISTS idx_reading_batches_astronaut_time
            ON reading_batches(astronaut_id, acquired_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS reading_values (
            batch_id INTEGER NOT NULL REFERENCES reading_batches(id),
            metric TEXT NOT NULL CHECK(metric IN ({metrics})),
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            quality TEXT NOT NULL CHECK(quality IN ('unknown', 'good', 'poor', 'invalid')),
            PRIMARY KEY(batch_id, metric)
        );
        CREATE VIEW IF NOT EXISTS telemetry_measurements AS
            SELECT 'legacy' AS kind, id AS raw_id, id, astronaut_id, captured_at,
                {legacy_fields}, source, NULL AS received_at, NULL AS device_id,
                NULL AS provenance, NULL AS observations, NULL AS qualities,
                NULL AS units, NULL AS request_id
            FROM measurements
            UNION ALL
            SELECT 'reading', b.id, 'reading:' || b.id, b.astronaut_id, b.acquired_at,
                {partial_fields}, b.source, b.received_at, b.device_id,
                b.provenance, b.observations, json_group_object(v.metric, v.quality),
                json_group_object(v.metric, v.unit), b.request_id
            FROM reading_batches b JOIN reading_values v ON v.batch_id=b.id GROUP BY b.id;
    """)


def init_medical(connection):
    """Migración aditiva: no crea antecedentes ni referencias personales."""
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS medical_profiles (
            astronaut_id INTEGER PRIMARY KEY REFERENCES astronauts(id),
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS medical_references (
            astronaut_id INTEGER NOT NULL REFERENCES astronauts(id),
            metric TEXT NOT NULL CHECK(metric IN (
                'heart_rate', 'spo2', 'systolic', 'diastolic', 'glucose',
                'temperature', 'sleep_hours'
            )),
            min_value REAL NOT NULL,
            max_value REAL NOT NULL,
            PRIMARY KEY (astronaut_id, metric),
            CHECK(min_value < max_value)
        );
    """)


def init_events(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            astronaut_id INTEGER NOT NULL REFERENCES astronauts(id),
            measurement_id INTEGER REFERENCES measurements(id),
            metric TEXT NOT NULL CHECK(metric IN ('heart_rate', 'spo2', 'systolic',
                'diastolic', 'glucose', 'temperature', 'sleep_hours')),
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('variation', 'review', 'priority', 'educational')),
            current_value REAL NOT NULL,
            reference_min REAL,
            reference_max REAL,
            description TEXT NOT NULL,
            guidance TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('individual_reference', 'trend_analysis',
                'simulation_rule', 'configured_protocol')),
            sources TEXT NOT NULL,
            measurement_source TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'unverified', 'resolved', 'superseded')),
            condition_key TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            resolved_at TEXT,
            CHECK((reference_min IS NULL AND reference_max IS NULL) OR
                (reference_min IS NOT NULL AND reference_max IS NOT NULL AND reference_min < reference_max))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_health_events_open_condition
            ON health_events(condition_key) WHERE status IN ('active', 'unverified');
        CREATE INDEX IF NOT EXISTS idx_health_events_astronaut_date
            ON health_events(astronaut_id, detected_at DESC, id DESC);
        CREATE TRIGGER IF NOT EXISTS health_event_measurement_owner
            BEFORE INSERT ON health_events
            WHEN NEW.measurement_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM measurements WHERE id=NEW.measurement_id AND astronaut_id=NEW.astronaut_id
            )
            BEGIN SELECT RAISE(ABORT, 'La medición no corresponde al astronauta'); END;
        CREATE TRIGGER IF NOT EXISTS health_event_measurement_owner_update
            BEFORE UPDATE OF measurement_id, astronaut_id ON health_events
            WHEN NEW.measurement_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM measurements WHERE id=NEW.measurement_id AND astronaut_id=NEW.astronaut_id
            )
            BEGIN SELECT RAISE(ABORT, 'La medición no corresponde al astronauta'); END;
    """)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(health_events)")}
    if "reading_id" not in columns:
        connection.execute("ALTER TABLE health_events ADD COLUMN reading_id INTEGER REFERENCES reading_batches(id)")
    if "reading_key" not in columns:
        connection.execute("ALTER TABLE health_events ADD COLUMN reading_key TEXT")
    connection.executescript("""
        CREATE TRIGGER IF NOT EXISTS health_event_reading_owner BEFORE INSERT ON health_events
        WHEN NEW.reading_id IS NOT NULL AND (NEW.measurement_id IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM reading_batches WHERE id=NEW.reading_id AND astronaut_id=NEW.astronaut_id))
        BEGIN SELECT RAISE(ABORT, 'La lectura no corresponde al astronauta'); END;
        CREATE TRIGGER IF NOT EXISTS health_event_reading_owner_update
        BEFORE UPDATE OF reading_id, measurement_id, astronaut_id ON health_events
        WHEN NEW.reading_id IS NOT NULL AND (NEW.measurement_id IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM reading_batches WHERE id=NEW.reading_id AND astronaut_id=NEW.astronaut_id))
        BEGIN SELECT RAISE(ABORT, 'La lectura no corresponde al astronauta'); END;
    """)


def init_oct(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS oct_exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            astronaut_id INTEGER NOT NULL REFERENCES astronauts(id),
            eye TEXT NOT NULL CHECK(eye IN ('OD', 'OS')),
            exam_date TEXT NOT NULL,
            mission_phase TEXT NOT NULL CHECK(mission_phase IN ('preflight', 'inflight', 'postflight')),
            device_model TEXT NOT NULL,
            scan_protocol TEXT NOT NULL,
            image_quality TEXT NOT NULL,
            is_baseline INTEGER NOT NULL DEFAULT 0 CHECK(is_baseline IN (0, 1)),
            review_status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            review_notes TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            provenance TEXT NOT NULL,
            source_document TEXT NOT NULL DEFAULT '',
            is_demo INTEGER NOT NULL CHECK(is_demo = 1),
            request_key TEXT UNIQUE,
            request_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_oct_astronaut_eye_date
            ON oct_exams(astronaut_id, eye, exam_date, id);
        CREATE TABLE IF NOT EXISTS oct_measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL REFERENCES oct_exams(id) ON DELETE CASCADE,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            anatomical_region TEXT NOT NULL,
            measurement_method TEXT NOT NULL,
            UNIQUE(exam_id, metric, unit, anatomical_region, measurement_method)
        );
        CREATE TABLE IF NOT EXISTS oct_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL REFERENCES oct_exams(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL UNIQUE,
            preview_path TEXT NOT NULL UNIQUE,
            file_type TEXT NOT NULL,
            original_name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            UNIQUE(exam_id, checksum)
        );
    """)

