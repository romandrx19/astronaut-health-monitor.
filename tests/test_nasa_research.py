"""Datos recibidos del equipo: fusión y API histórica con bases temporales."""
from contextlib import closing
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

import database
from integrate_nasa import MergeConflict, TABLES, merge_science, table_digest

SOURCE = Path(__file__).resolve().parents[1] / "data/nasa/health_ares_nasa_preservada.sqlite3"
if not SOURCE.exists():
    SOURCE = SOURCE.with_name("bootstrap.sqlite3")


@unittest.skipUnless(SOURCE.exists(), "Requiere la base preservada del ZIP proporcionado")
class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.db_path = cls.root / "local.sqlite3"
        previous = database.DB_PATH
        database.DB_PATH = cls.db_path
        try:
            database.init_db()
            cls.module = importlib.import_module("app")
            cls.app = cls.module.create_app({"TESTING": True, "ARES_DB_PATH": cls.db_path, "ARES_SEED_DEMO": False})
        finally:
            database.DB_PATH = previous
        with closing(sqlite3.connect(cls.db_path)) as connection:
            cls.local = {name: table_digest(connection, name) for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cls.report = merge_science(SOURCE, cls.db_path, cls.root / "backups")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.client = self.app.test_client()

    def get(self, route, **params):
        response = self.client.get("/api/research/" + route, query_string=params)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_01_merge_preserves_every_local_record_and_has_verified_backup(self):
        self.assertEqual(self.report["local_before"], self.report["local_after"])
        self.assertTrue(Path(self.report["backup"]).is_file())
        with closing(sqlite3.connect(self.report["backup"])) as connection:
            self.assertEqual(self.local, {name: table_digest(connection, name) for name in self.local})
        self.assertEqual(self.report["integrity"], "ok")
        self.assertEqual(self.report["foreign_key_errors"], 0)

    def test_02_source_and_destination_scientific_rows_are_identical(self):
        with closing(sqlite3.connect(self.db_path)) as target, closing(sqlite3.connect(SOURCE)) as source:
            for table in TABLES:
                self.assertEqual(table_digest(target, table), table_digest(source, table), table)
            self.assertEqual(target.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(target.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_repeated_merge_is_idempotent(self):
        report = merge_science(SOURCE, self.db_path, self.root / "backups")
        self.assertEqual(sum(report["inserted"].values()), 0)

    def test_merge_conflict_rolls_back_and_retains_local_scientific_changes(self):
        target = self.root / "conflict.sqlite3"
        with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(target)) as copy:
            source.backup(copy)
            copy.execute("UPDATE subjects SET campaign='LOCAL' WHERE subject_id='C1:C1G0001'")
            copy.commit()
        with self.assertRaises(MergeConflict):
            merge_science(SOURCE, target, self.root / "conflict-backups")
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(connection.execute("SELECT campaign FROM subjects WHERE subject_id=?", ("C1:C1G0001",)).fetchone()[0], "LOCAL")

    def test_three_astronauts_and_52_research_subjects_are_separate(self):
        self.assertEqual([item["id"] for item in self.client.get("/api/astronauts").json], [1, 2, 3])
        subjects = self.get("subjects", page_size=200)
        self.assertEqual(subjects["total"], 52)
        self.assertTrue(all(isinstance(item["subject_id"], str) and ":" in item["subject_id"] for item in subjects["items"]))
        self.assertEqual(self.client.get("/api/astronauts/101/history").status_code, 404)

    def test_datasets_and_provenance_keep_original_hashes_and_notes(self):
        result = self.get("datasets")
        self.assertEqual(result["total"], 4)
        self.assertEqual({item["source_code"] for item in result["items"]}, {"CRF", "BRSMAct", "MR010G", "MR016G"})
        source = self.get("sources", dataset="CRF")["items"][0]
        self.assertEqual(len(source["sha256"]), 64)
        self.assertIn("SpO2", source["note"])
        self.assertEqual(source["entities"], ["vital_signs", "hydration"])

    def test_dataset_filter_does_not_confuse_campaign_with_dataset(self):
        result = self.get("subjects", dataset="BRSMAct", page_size=200)
        self.assertEqual(result["total"], 3)
        self.assertTrue(all(item["campaign"] == "C1" for item in result["items"]))
        self.assertEqual(self.get("vital-signs", dataset="MR016G")["total"], 0)

    def test_paginated_raw_counts_and_stable_order(self):
        expected = {"vital-signs": 2940, "sleep": 207, "nutrition": 2125, "hydration": 2689, "lab-results": 984, "quality-flags": 2800}
        for route, count in expected.items():
            first = self.get(route, page_size=2)
            second = self.get(route, page_size=2, page=2)
            self.assertEqual(first["total"], count)
            self.assertEqual(len(first["items"]), 2)
            self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])

    def test_subject_date_and_indicator_filters_preserve_source_values(self):
        result = self.get("vital-signs", subject_id="C1:C1G0001", **{"from": "2004-09-01", "to": "2004-09-05"}, metric="heart_rate_bpm")
        self.assertGreater(result["total"], 0)
        self.assertTrue(all(item["subject_id"] == "C1:C1G0001" and "2004-09-01" <= item["date"] <= "2004-09-05" for item in result["items"]))
        self.assertEqual(result["units"]["heart_rate_bpm"], "bpm")
        lab = self.get("lab-results", subject_id="C1:C1G0001", metric="glucose_mg_dl")
        self.assertTrue(all(item["analyte"] == "glucose_mg_dl" for item in lab["items"]))

    def test_nulls_and_undated_nutrition_are_not_coerced_to_zero_or_technical_dates(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute("SELECT subject_id FROM nutrition WHERE date IS NULL LIMIT 1").fetchone()
        result = self.get("nutrition", subject_id=row[0], dataset="MR016G", page_size=200)
        self.assertTrue(any(item["date"] is None for item in result["items"]))
        self.assertTrue(any(item["protein_g"] is None for item in result["items"]))
        limited = self.get("nutrition", subject_id=row[0], **{"from": "2000-01-01"})
        self.assertFalse(any(item["date"] is None for item in limited["items"]))
        self.assertEqual(self.get("summary")["unavailable"].keys(), {"spo2"})

    def test_series_use_provider_views_and_keep_conflicts_as_null(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            subject, metric = connection.execute("SELECT subject_id,field_name FROM quality_flags WHERE source_file='nutrition' AND flag='conflicting_values' AND field_name IN ('sodium_mg','calcium_mg') LIMIT 1").fetchone()
            expected = connection.execute(f'SELECT date,"{metric}" FROM v_nutrition_daily WHERE subject_id=? ORDER BY date', (subject,)).fetchall()
        result = self.get("series", entity="nutrition", metric=metric, subject_id=subject, page_size=200)
        self.assertEqual([(item["date"], item["value"]) for item in result["items"]], expected)
        self.assertTrue(any(item["value"] is None for item in result["items"]))
        self.assertEqual(result["time_basis"], "study_date_without_timezone")

    def test_quality_flags_include_aggregate_conflicts_and_annotate_original_rows(self):
        flags = self.get("quality-flags", dataset="MR016G", flag="conflicting_values")
        self.assertGreater(flags["total"], 0)
        self.assertTrue(all(item["dataset"] == "MR016G" for item in flags["items"]))
        row = self.get("nutrition", page_size=200)["items"]
        self.assertTrue(any(item["quality_flags"] for item in row))

    def test_outliers_and_suspect_dates_remain_visible_with_flags(self):
        result = self.get("quality-flags", flag="suspect_year")
        self.assertEqual(result["total"], 3)
        series = self.get("series", subject_id="C1:C1G0001", entity="vital_signs", metric="heart_rate_bpm", page_size=200)
        self.assertTrue(any(item["date"] == "2001-10-29" and "suspect_year" in item["quality_flags"] for item in series["items"]))

    def test_invalid_filters_and_injection_attempts_are_rejected_or_parameterized(self):
        for params in ({"page_size": 0}, {"page": -1}, {"page_size": 201}, {"page": "bad"},
                       {"from": "2026-1-1"}, {"from": "2026-02-01", "to": "2026-01-01"}, {"dataset": "CRF' OR 1=1--"},
                       {"metric": 'heart_rate_bpm"; DROP TABLE subjects;--'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/research/vital-signs", query_string=params).status_code, 400)
        self.assertEqual(self.client.get("/api/research/vital-signs", query_string={"subject_id": "' OR 1=1--"}).status_code, 404)
        self.assertEqual(self.get("subjects", subject_id="' OR 1=1--")["total"], 0)
        self.assertEqual(self.get("subjects")["total"], 52)
        self.assertEqual(self.client.post("/api/research/subjects", json={}).status_code, 405)

    def test_simulation_manual_medical_alerts_and_oct_remain_operational(self):
        self.assertEqual(self.client.post("/api/simulate").json["created"], 3)
        manual = self.client.post("/api/astronauts/1/measurements/manual", json={"heart_rate": 112})
        self.assertEqual(manual.status_code, 201)
        self.assertTrue(manual.json["alerts"])
        medical = self.client.post("/api/astronauts/1/medical", json={"notes": "Prueba NASA aislada", "references": {"heart_rate": {"min_value": 60, "max_value": 90}}})
        self.assertEqual(medical.status_code, 200)
        self.assertEqual(self.client.get("/api/astronauts/1/medical").json["notes"], "Prueba NASA aislada")
        self.assertEqual(self.client.get("/api/astronauts/1/assessment").status_code, 200)
        self.assertEqual(self.client.get("/api/astronauts/1/events").status_code, 200)
        self.assertEqual(self.client.get("/api/devices/status").json["verified_telemetry_available"], False)
        self.assertTrue(all(rule["validation_status"] == "demonstration" for rule in self.client.get("/api/rules").json["rules"]))
        self.client.get("/")
        self.assertEqual(self.client.get("/api/astronauts/1/oct/exams").json["exams"], [])
        self.assertEqual(self.get("subjects")["total"], 52)


if __name__ == "__main__":
    unittest.main()
