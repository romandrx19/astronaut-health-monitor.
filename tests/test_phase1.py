"""Pruebas de migración y API aisladas de los datos de trabajo."""
import importlib
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import database
from measurements import FIELDS
from simulator import generate_sample


class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / "test.sqlite3"
        database.init_db()
        # Importar app usa exclusivamente la base temporal, nunca health.sqlite3.
        self.module = importlib.import_module("app")
        database.init_db()
        self.client = self.module.app.test_client()
        sample = generate_sample(1, occasional_alert=False)
        self.payload = {field: sample[field] for field in FIELDS}

    def tearDown(self):
        database.DB_PATH = self.previous
        self.temp.cleanup()

    def test_manual_persistence_and_filter(self):
        response = self.client.post("/api/astronauts/1/measurements", json=self.payload)
        self.assertEqual(response.status_code, 201)
        saved = response.json["measurement"]
        self.assertEqual(saved["source"], "manual")
        self.assertIn("+00:00", saved["captured_at"])
        database.init_db()
        self.assertEqual(database.get_latest(1, "manual"), saved)
        history = self.client.get("/api/astronauts/1/history?source=manual").json
        self.assertEqual(history, [saved])
        crew = self.client.get("/api/astronauts?source=manual").json
        self.assertEqual(crew[0]["latest"], saved)
        self.assertIsNone(crew[1]["latest"])

    def test_invalid_payloads_do_not_write(self):
        bad = [None, [], {}, {**self.payload, "source": "simulated"}]
        for field in FIELDS:
            for value in (None, True, "72", -1, 99999, float("nan"), float("inf")):
                bad.append({**self.payload, field: value})
        bad.extend([{**self.payload, "heart_rate": 70.5},
                    {**self.payload, "diastolic": self.payload["systolic"]}])
        before = database.get_history(1, 200)
        for payload in bad:
            with self.subTest(payload=payload):
                response = (self.client.post("/api/astronauts/1/measurements", data="null", content_type="application/json")
                            if payload is None else self.client.post("/api/astronauts/1/measurements", json=payload))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(database.get_history(1, 200), before)

    def test_protocol_errors_and_regressions(self):
        self.assertEqual(self.client.post("/api/astronauts/99/measurements", json=self.payload).status_code, 404)
        self.assertEqual(self.client.post("/api/astronauts/1/measurements", data="x").status_code, 415)
        self.assertEqual(self.client.post("/api/astronauts/1/measurements", data="{", content_type="application/json").status_code, 400)
        for path in ("/api/astronauts?source=invalid", "/api/astronauts/1/history?source=invalid", "/api/astronauts/1/history?limit=abc", "/api/astronauts/1/history?limit=201"):
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        result = self.client.post("/api/simulate")
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json["created"], 3)
        self.assertTrue(all(s["source"] == "simulated" for s in result.json["samples"]))
        self.assertTrue(all(s["source"] == "simulated" for s in self.client.get("/api/astronauts/1/history?source=simulated").json))

    def test_legacy_migration_is_lossless_and_repeatable(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, astronaut_id INTEGER NOT NULL, captured_at TEXT NOT NULL, heart_rate INTEGER NOT NULL, spo2 INTEGER NOT NULL, systolic INTEGER NOT NULL, diastolic INTEGER NOT NULL, glucose INTEGER NOT NULL, temperature REAL NOT NULL, sleep_hours REAL NOT NULL)")
            connection.execute("INSERT INTO measurements VALUES (42,1,'2026-01-01T00:00:00.000+00:00',72,98,116,75,94,36.7,7.4)")
            before = connection.execute("SELECT * FROM measurements").fetchone()
        database.DB_PATH = legacy
        database.init_db()
        database.init_db()
        with database.connect() as connection:
            after = tuple(connection.execute("SELECT * FROM measurements").fetchone())
            self.assertEqual(after[:-1], before)
            self.assertEqual(after[-1], "simulated")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
