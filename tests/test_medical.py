"""Expedientes ficticios en SQLite temporal; nunca se usa health.sqlite3."""
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

import database
from medical import METRICS, init_medical_db, medical_profile, save_medical_profile


class MedicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / "medical.sqlite3"
        database.init_db()
        init_medical_db()
        self.module = importlib.import_module("app")
        self.client = self.module.app.test_client()
        self.payload = {
            "notes": "Texto de demostración; sin datos de pacientes.",
            "references": {metric: {"min_value": 1.25, "max_value": 2.75} for metric in METRICS},
        }

    def tearDown(self):
        database.DB_PATH = self.previous
        self.temp.cleanup()

    def save(self, astronaut_id=1, payload=None):
        return self.client.post(f"/api/astronauts/{astronaut_id}/medical",
                                json=self.payload if payload is None else payload)

    def test_initial_profiles_are_empty_without_invented_records(self):
        for astronaut_id in (1, 2, 3):
            response = self.client.get(f"/api/astronauts/{astronaut_id}/medical")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {
                "astronaut_id": astronaut_id, "notes": "", "updated_at": None, "references": {},
            })
        with database.connect() as connection:
            for table in ("medical_profiles", "medical_references"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_each_astronaut_has_an_independent_profile(self):
        saved = []
        for astronaut_id in (1, 2, 3):
            payload = {"notes": f"Demostración {astronaut_id}", "references": {
                "temperature": {"min_value": astronaut_id + 0.1, "max_value": astronaut_id + 0.9},
            }}
            response = self.save(astronaut_id, payload)
            self.assertEqual(response.status_code, 200)
            self.assertIn("+00:00", response.json["updated_at"])
            saved.append(response.json)
        self.save(1)
        for astronaut_id in (2, 3):
            self.assertEqual(medical_profile(astronaut_id), saved[astronaut_id - 1])

    def test_all_references_and_repeated_saves_do_not_duplicate(self):
        self.assertEqual(self.save().json["references"], self.payload["references"])
        self.assertEqual(self.save().status_code, 200)
        before = medical_profile(1)
        database.init_db()
        init_medical_db()
        init_medical_db()
        self.assertEqual(medical_profile(1), before)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM medical_profiles").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM medical_references").fetchone()[0], 7)

    def test_empty_references_clear_configuration_without_adding_defaults(self):
        self.save()
        response = self.save(payload={"notes": "", "references": {
            "heart_rate": {}, "spo2": None,
            "systolic": {"min_value": None, "max_value": ""},
        }})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["references"], {})
        self.assertEqual(response.json["notes"], "")
        self.assertEqual(self.save(payload={}).json["references"], {})

    def test_invalid_payloads_do_not_change_any_saved_data(self):
        self.save()
        before = medical_profile(1)
        invalid = [None, [], "texto", {"notes": None}, {"notes": 123},
                   {"notes": "x" * 20001}, {"references": []}, {"references": None},
                   {"astronaut_id": 2}, {"references": {"unknown": {}}}]
        invalid_bounds = [[], "rango", {"min": 1, "max": 2},
                          {"min_value": 1}, {"max_value": 2},
                          {"min_value": "", "max_value": 2},
                          {"min_value": 2, "max_value": 2},
                          {"min_value": 3, "max_value": 2}]
        for value in (True, False, "1", "abc", [], {}, float("nan"),
                      float("inf"), float("-inf"), 10 ** 400):
            invalid_bounds.extend([{"min_value": value, "max_value": 2},
                                   {"min_value": 1, "max_value": value}])
        for metric in METRICS:
            invalid.extend({"notes": "No debe guardarse", "references": {metric: bounds}}
                           for bounds in invalid_bounds)
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post("/api/astronauts/1/medical", data=json.dumps(payload),
                                            content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertIsInstance(response.json["error"], str)
                self.assertEqual(medical_profile(1), before)

    def test_protocol_errors_and_missing_astronauts(self):
        for method in (self.client.get, self.client.post):
            response = method("/api/astronauts/99/medical")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json["error"], "Astronauta no encontrado")
        self.assertEqual(self.client.post("/api/astronauts/1/medical", data="x").status_code, 415)
        self.assertEqual(self.client.post("/api/astronauts/1/medical", data="{",
                                         content_type="application/json").status_code, 400)
        self.assertEqual(self.client.delete("/api/astronauts/1/medical").status_code, 405)

    def test_astronaut_existence_uses_database_identifiers(self):
        with database.connect() as connection:
            connection.execute("INSERT INTO astronauts VALUES (7, 'Perfil de prueba', 'Demo', 'Demo')")
        self.assertEqual(self.save(7).status_code, 200)
        self.assertEqual(self.client.get("/api/astronauts/7/medical").json["astronaut_id"], 7)

    def test_database_constraints_and_foreign_keys(self):
        self.save()
        statements = [
            ("INSERT INTO medical_profiles VALUES (99, '', 'demo')", ()),
            ("INSERT INTO medical_references VALUES (99, 'heart_rate', 1, 2)", ()),
            ("INSERT INTO medical_references VALUES (1, 'heart_rate', 1, 2)", ()),
            ("INSERT INTO medical_references VALUES (2, 'unknown', 1, 2)", ()),
            ("INSERT INTO medical_references VALUES (2, 'heart_rate', 2, 1)", ()),
        ]
        for sql, parameters in statements:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with database.connect() as connection:
                    self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                    connection.execute(sql, parameters)

    def test_storage_failure_rolls_back_notes_and_every_reference(self):
        self.save()
        before = medical_profile(1)
        with database.connect() as connection:
            connection.execute("""CREATE TRIGGER reject_reference BEFORE INSERT ON medical_references
                WHEN NEW.metric = 'spo2' BEGIN SELECT RAISE(ABORT, 'fallo de prueba'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            save_medical_profile(1, {**self.payload, "notes": "No debe persistir"})
        self.assertEqual(medical_profile(1), before)

    def test_sql_text_is_stored_as_text(self):
        notes = "Demo: '); DROP TABLE astronauts; -- <script>demo</script>"
        self.assertEqual(self.save(payload={"notes": notes}).json["notes"], notes)
        self.assertEqual(len(database.get_astronauts()), 3)

    def test_tables_and_measurements_survive_medical_initialization(self):
        self.client.post("/api/simulate")
        with database.connect() as connection:
            before = [tuple(row) for row in connection.execute("SELECT * FROM measurements ORDER BY id")]
            crew = [tuple(row) for row in connection.execute("SELECT * FROM astronauts ORDER BY id")]
        init_medical_db()
        self.save()
        init_medical_db()
        with database.connect() as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM measurements ORDER BY id")], before)
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM astronauts ORDER BY id")], crew)

    def test_simulation_history_and_original_alerts_remain_independent(self):
        self.save()
        before = medical_profile(1)
        response = self.client.post("/api/simulate")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["created"], 3)
        crew = self.client.get("/api/astronauts").json
        self.assertEqual([a["id"] for a in crew], [1, 2, 3])
        self.assertTrue(all(a["latest"] for a in crew))
        self.assertEqual(len(self.client.get("/api/astronauts/1/history").json) > 0, True)
        # Valores sintéticos solo para ejercitar las reglas existentes, sin criterios nuevos.
        sample = {"heart_rate": 115, "spo2": 92, "systolic": 150, "diastolic": 75,
                  "glucose": 151, "temperature": 38.2, "sleep_hours": 4.5}
        alerts = self.client.post("/api/astronauts/1/measurements", json=sample)
        self.assertEqual(alerts.status_code, 201)
        self.assertEqual(len(alerts.json["alerts"]), 6)
        self.assertEqual(medical_profile(1), before)

    def test_profile_persists_after_a_new_application_process(self):
        expected = self.save().json
        env = {**os.environ, "ARES_DB_PATH": str(database.DB_PATH), "PYTHONIOENCODING": "utf-8"}
        script = "from app import app; import json; print(json.dumps(app.test_client().get('/api/astronauts/1/medical').json))"
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=True)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_page_has_unique_ids_and_keeps_original_sections(self):
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []

            def handle_starttag(self, tag, attrs):
                self.ids.extend(value for name, value in attrs if name == "id")

        html = self.client.get("/").get_data(as_text=True)
        elements = Elements()
        elements.feed(html)
        self.assertEqual(len(elements.ids), len(set(elements.ids)))
        for element_id in ("medical-panel", "medical-form", "medical-name", "medical-notes",
                           "medical-references", "medical-save", "medical-message", "reading-source"):
            self.assertIn(element_id, elements.ids)
        sections = [html.index(label) for label in ("01 / TRIPULACIÓN", "02 / TELEMETRÍA", "03 / EVENTOS",
                                                   "04 / ANÁLISIS", "05 / HISTORIAL MÉDICO", "<footer")]
        self.assertEqual(sections, sorted(sections))
        for module in ("control", "history", "medical", "manual", "devices", "events"):
            self.assertIn(f"view-{module}", elements.ids)
            self.assertIn(f"nav-{module}", elements.ids)
        self.assertEqual(html.count('id="manual-form"'), 1)
        self.assertIn('id="priority-banner"', html)
        self.assertIn('id="module-menu"', html)


if __name__ == "__main__":
    unittest.main()
