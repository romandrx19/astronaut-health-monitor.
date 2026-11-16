"""Fase 3: datos ficticios, base temporal y ningún acceso a Bluetooth físico."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import database
from acquisition import DuplicateRequest, ingest_from_adapter, init_acquisition_db, reading_document, store_reading
from devices.base import IntegrationUnavailable
from devices.normalizer import normalize_manual, normalize_reading
from devices.roadtrip import RoadtripAdapter
from devices.testing import DemoAdapter
from health_analysis import evaluate_astronaut
from health_events import assess_astronaut, get_health_events, init_health_events_db
from medical import init_medical_db, medical_profile, save_medical_profile


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / "partial.sqlite3"
        database.init_db()
        init_medical_db()
        init_health_events_db()
        self.module = importlib.import_module("app")
        self.client = self.module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.previous
        self.temp.cleanup()

    def post(self, payload, key="demo-request", astronaut=1):
        return self.client.post(f"/api/astronauts/{astronaut}/measurements/manual", json=payload,
                                headers={"Idempotency-Key": key})

    def test_single_indicator_is_stored_without_filling_other_values(self):
        response = self.post({"heart_rate": 112, "observations": "Solo una lectura ficticia"})
        self.assertEqual(response.status_code, 201)
        saved = response.json["measurement"]
        self.assertEqual(set(saved["metrics"]), {"heart_rate"})
        self.assertEqual(saved["metrics"]["heart_rate"], {"value": 112, "unit": "bpm", "quality": "unknown"})
        self.assertIsNone(saved["spo2"])
        self.assertEqual(saved["source"], "manual")
        self.assertIn("+00:00", saved["acquired_at"])
        self.assertGreaterEqual(saved["received_at"], saved["acquired_at"])
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_values").fetchone()[0], 1)
        records = self.client.get("/api/astronauts/1/measurements?source=manual").json
        self.assertEqual(records, [saved])

    def test_multiple_indicators_zero_and_observations(self):
        response = self.post({"spo2": 0, "temperature": 36.75, "sleep_hours": 0,
                              "observations": "DEMO <script>texto</script>"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["measurement"]["spo2"], 0)
        self.assertEqual(response.json["measurement"]["temperature"], 36.75)
        self.assertEqual(len(response.json["measurement"]["metrics"]), 3)

    def test_invalid_inputs_do_not_insert_batches(self):
        invalid = [None, [], {}, {"observations": "Solo texto"}, {"unknown": 1},
                   {"heart_rate": 72, "source": "smartwatch"}, {"heart_rate": 72, "device_id": "fake"},
                   {"heart_rate": 72, "provenance": "verified_device"},
                   {"heart_rate": 72, "observations": 4}, {"heart_rate": 72, "observations": "x" * 4001},
                   {"systolic": 80, "diastolic": 90}, {"heart_rate": 70.5}]
        for value in (None, "", "72", True, float("nan"), float("inf"), 10 ** 400, -1, 9999):
            invalid.append({"heart_rate": value})
        for at in ("bad", "2026-01-01T00:00:00", 123, "", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
            invalid.append({"heart_rate": 72, "acquired_at": at})
        for payload in invalid:
            with self.subTest(payload=payload):
                result = self.client.post("/api/astronauts/1/measurements/manual", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(result.status_code, 400)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_batches").fetchone()[0], 0)

    def test_repeated_request_is_idempotent_and_conflicting_request_is_rejected(self):
        first = self.post({"heart_rate": 72})
        again = self.post({"heart_rate": 72})
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json["measurement"], again.json["measurement"])
        self.assertFalse(again.json["created"])
        self.assertEqual(self.post({"heart_rate": 73}).status_code, 409)
        self.assertEqual(len(database.get_history(1, source="manual")), 1)

    def test_concurrent_retries_insert_once(self):
        def save(_):
            return store_reading(normalize_manual({"heart_rate": 72}, 1), "concurrent-demo")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(save, range(8)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({record["id"] for record, _ in results}), 1)

    def test_astronauts_and_same_request_key_remain_independent(self):
        first = self.post({"heart_rate": 72}, astronaut=1).json["measurement"]
        second = self.post({"glucose": 96}, astronaut=2).json["measurement"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(database.get_latest(1, "manual")["heart_rate"], 72)
        self.assertIsNone(database.get_latest(2, "manual")["heart_rate"])
        self.assertEqual(self.client.get("/api/astronauts/3/measurements?source=manual").json, [])

    def test_medical_profiles_and_personal_alerts_are_preserved(self):
        save_medical_profile(1, {"notes": "DEMO sin datos reales", "references": {
            "heart_rate": {"min_value": 60, "max_value": 80}}})
        before = medical_profile(1)
        result = self.post({"heart_rate": 112})
        self.assertEqual(result.status_code, 201)
        item = result.json["assessment"]["metrics"]["heart_rate"]
        self.assertEqual((item["comparison"], item["difference"]), ("above", 32))
        self.assertEqual(result.json["assessment"]["metrics"]["glucose"]["state"], "insufficient")
        self.assertEqual(medical_profile(1), before)
        events = get_health_events(1)
        self.assertTrue(events)
        self.assertTrue(all(event["reading_id"] is not None and event["measurement_id"] is None for event in events))
        self.post({"heart_rate": 112})
        self.assertEqual(len(get_health_events(1)), len(events))

    def test_backdated_readings_do_not_replace_newer_readings(self):
        now = datetime.now(timezone.utc)
        recent = self.post({"heart_rate": 72, "acquired_at": (now - timedelta(seconds=1)).isoformat()}, "recent")
        older = self.post({"heart_rate": 112, "acquired_at": (now - timedelta(days=1)).isoformat()}, "older")
        self.assertEqual(older.status_code, 201)
        self.assertEqual(database.get_latest(1, "manual")["id"], recent.json["measurement"]["id"])
        self.assertEqual(older.json["assessment"]["measurement_id"], recent.json["measurement"]["id"])
        history = database.get_history(1, source="manual")
        self.assertEqual([item["heart_rate"] for item in history], [112, 72])

    def test_equivalent_time_zones_are_sorted_by_actual_time(self):
        newer = normalize_manual({"heart_rate": 72, "acquired_at": "2026-01-01T10:00:00-05:00"}, 1)
        older = normalize_manual({"heart_rate": 73, "acquired_at": "2026-01-01T14:00:00+00:00"}, 1)
        recent, _ = store_reading(newer)
        store_reading(older)
        self.assertEqual(database.get_latest(1, "manual")["id"], recent["id"])

    def test_low_quality_is_saved_but_not_used_for_evaluation(self):
        save_medical_profile(1, {"references": {"heart_rate": {"min_value": 60, "max_value": 80}}})
        reading = normalize_reading({"metrics": {"heart_rate": {"value": 112, "quality": "poor"}}},
                                    1, source="simulation", provenance="adapter_test", device_id="test:low-quality")
        saved, _ = store_reading(reading)
        self.assertEqual(saved["heart_rate"], 112)
        result = assess_astronaut(1, "simulation")
        self.assertEqual(result["metrics"]["heart_rate"]["state"], "insufficient")
        self.assertEqual(result["metrics"]["heart_rate"]["quality"], "poor")
        self.assertFalse(any(event["metric"] == "heart_rate" for event in result["events"]))

    def test_roadtrip_is_explicitly_unavailable_and_cannot_create_readings(self):
        adapter = RoadtripAdapter()
        self.assertFalse(adapter.status().connected)
        self.assertFalse(adapter.status().telemetry_confirmed)
        with self.assertRaises(IntegrationUnavailable):
            ingest_from_adapter(adapter, 1)
        status = self.client.get("/api/devices/status")
        self.assertEqual(status.status_code, 200)
        self.assertIn(status.json["devices"][0]["state"], ("ble_unavailable", "disconnected"))
        self.assertFalse(status.json["devices"][0]["telemetry_confirmed"])
        self.assertTrue(status.json["devices"][0]["message"])
        self.assertFalse(status.json["verified_telemetry_available"])

    def test_demo_adapter_never_claims_smartwatch_origin(self):
        adapter = DemoAdapter({"metrics": {"glucose": 96}})
        saved, created = ingest_from_adapter(adapter, 1, "test-adapter")
        self.assertTrue(created)
        self.assertEqual(saved["source"], "simulation")
        self.assertEqual(saved["provenance"], "adapter_test")
        self.assertEqual(saved["device_id"], "test:demo-adapter")
        self.assertFalse(adapter.status().verified_physical)
        self.assertEqual(self.client.get("/api/astronauts/1/measurements?source=smartwatch").json, [])
        self.assertEqual(len(self.client.get("/api/astronauts/1/measurements?source=simulation").json) > 0, True)

    def test_units_provenance_and_device_claims_are_checked(self):
        for payload in ({"metrics": {"heart_rate": {"value": 72, "unit": "kg"}}},
                        {"metrics": {"heart_rate": {"value": 72, "quality": "verified"}}}):
            with self.assertRaises(ValueError):
                normalize_reading(payload, 1)
        for source, provenance in (("smartwatch", "adapter_test"), ("smartwatch", "manual"), ("simulation", "verified_device")):
            with self.assertRaises(ValueError):
                normalize_reading({"metrics": {"heart_rate": 72}}, 1, source=source, provenance=provenance)
        imported = normalize_reading({"metrics": {"heart_rate": 72}}, 1, source="manual", provenance="imported")
        self.assertEqual(store_reading(imported)[0]["provenance"], "imported")

    def test_unverified_adapter_cannot_claim_a_real_device(self):
        class UnverifiedAdapter(DemoAdapter):
            def read(self, astronaut_id):
                return normalize_reading({"metrics": {"heart_rate": 72}}, astronaut_id,
                                         source="smartwatch", device_id="fake", provenance="verified_device")
        with self.assertRaises(ValueError):
            ingest_from_adapter(UnverifiedAdapter({}), 1)

    def test_protocol_errors_and_no_public_device_ingestion(self):
        self.assertEqual(self.post({"heart_rate": 72}, astronaut=99).status_code, 404)
        self.assertEqual(self.client.post("/api/astronauts/1/measurements/manual", data="x").status_code, 415)
        self.assertEqual(self.client.post("/api/astronauts/1/measurements/manual", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.post({"heart_rate": 72}, key="").status_code, 400)
        self.assertEqual(self.post({"heart_rate": 72}, key="x" * 129).status_code, 400)
        for query in ("limit=x", "limit=0", "limit=201", "source=invalid"):
            self.assertEqual(self.client.get("/api/astronauts/1/measurements?" + query).status_code, 400)
        self.assertEqual(self.client.get("/api/astronauts/99/measurements").status_code, 404)
        self.assertEqual(self.client.post("/api/devices/measurements", json={"source": "smartwatch"}).status_code, 404)

    def test_atomic_storage_and_foreign_keys(self):
        with database.connect() as connection:
            connection.execute("""CREATE TRIGGER reject_spo2 BEFORE INSERT ON reading_values
                WHEN NEW.metric='spo2' BEGIN SELECT RAISE(ABORT, 'prueba'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            store_reading(normalize_manual({"heart_rate": 72, "spo2": 98}, 1))
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_batches").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_values").fetchone()[0], 0)
        with self.assertRaises(LookupError):
            store_reading(normalize_manual({"heart_rate": 72}, 99))

    def test_existing_measurements_and_simulation_remain_unchanged(self):
        self.client.post("/api/simulate")
        with database.connect() as connection:
            before = [tuple(row) for row in connection.execute("SELECT * FROM measurements ORDER BY id")]
        self.post({"heart_rate": 112})
        init_acquisition_db()
        database.init_db()
        init_health_events_db()
        with database.connect() as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM measurements ORDER BY id")], before)
        records = self.client.get("/api/astronauts/1/measurements?source=simulation").json
        self.assertTrue(all(row["source"] == "simulation" and row["original_source"] == "simulated" for row in records))
        result = self.client.post("/api/simulate")
        # Una segunda solicitud dentro del mismo ciclo comparte el lote existente.
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["created"], 0)

    def test_persistence_in_a_new_application_process(self):
        saved = self.post({"heart_rate": 72}).json["measurement"]
        env = {**os.environ, "ARES_DB_PATH": str(database.DB_PATH), "PYTHONIOENCODING": "utf-8"}
        code = "from app import app; import json; print(json.dumps(app.test_client().get('/api/astronauts/1/measurements?source=manual').json))"
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=True)
        self.assertEqual(json.loads(result.stdout), [saved])

    def test_new_reading_event_cannot_be_assigned_to_another_astronaut(self):
        save_medical_profile(1, {"references": {"heart_rate": {"min_value": 60, "max_value": 80}}})
        self.post({"heart_rate": 112})
        with self.assertRaises(sqlite3.IntegrityError):
            with database.connect() as connection:
                connection.execute("UPDATE health_events SET astronaut_id=2 WHERE reading_id IS NOT NULL")

    def test_phase_two_event_schema_migration_keeps_old_rows(self):
        database.DB_PATH = Path(self.temp.name) / "phase-two.sqlite3"
        database.init_db()
        init_medical_db()
        sample = {"astronaut_id": 1, "captured_at": "2026-01-01T12:00:00+00:00", "source": "manual",
                  "heart_rate": 112, "spo2": 98, "systolic": 116, "diastolic": 75,
                  "glucose": 94, "temperature": 36.7, "sleep_hours": 7.4}
        saved = database.add_manual_measurement(sample)
        with database.connect() as connection:
            # Formato anterior a los vínculos reading_id/reading_key de la Fase 3.
            connection.execute("""CREATE TABLE health_events (
                id INTEGER PRIMARY KEY, astronaut_id INTEGER NOT NULL REFERENCES astronauts(id),
                measurement_id INTEGER REFERENCES measurements(id), metric TEXT NOT NULL,
                event_type TEXT NOT NULL, severity TEXT NOT NULL, current_value REAL NOT NULL,
                reference_min REAL, reference_max REAL, description TEXT NOT NULL,
                guidance TEXT NOT NULL, source TEXT NOT NULL, sources TEXT NOT NULL,
                measurement_source TEXT NOT NULL, captured_at TEXT NOT NULL,
                detected_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, status TEXT NOT NULL,
                condition_key TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1, resolved_at TEXT)""")
            connection.execute("""INSERT INTO health_events VALUES
                (42, 1, ?, 'heart_rate', 'above_reference', 'variation', 112, 60, 80,
                 'Ejemplo ficticio de Fase 2', '[]', 'individual_reference',
                 '["individual_reference"]', 'manual', ?, ?, ?, 'active', 'legacy-fixture', 1, NULL)""",
                (saved["id"], sample["captured_at"], sample["captured_at"], sample["captured_at"]))
            before = tuple(connection.execute("SELECT * FROM health_events").fetchone())
        init_health_events_db()
        init_health_events_db()
        with database.connect() as connection:
            after = tuple(connection.execute("SELECT * FROM health_events").fetchone())
            self.assertEqual(after[:len(before)], before)
            self.assertEqual(after[len(before):], (None, None))
        self.assertEqual(database.get_latest(1, "manual"), saved)
        self.assertEqual(get_health_events(1)[0]["id"], 42)


if __name__ == "__main__":
    unittest.main()
