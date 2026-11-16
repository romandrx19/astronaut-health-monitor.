"""Reglas sintéticas y eventos aislados de la base de trabajo; sin datos clínicos reales."""
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
from health_analysis import AnalysisRules, METRICS, analyze_trends, evaluate_astronaut, evaluate_metric
from health_events import assess_astronaut, get_health_events, init_health_events_db
from medical import init_medical_db, medical_profile, save_medical_profile

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
REFERENCE = {"heart_rate": {"min_value": 60, "max_value": 80}}


def samples(values, astronaut_id=1, source="manual", now=NOW, gap=5):
    return [{"id": index + 1, "astronaut_id": astronaut_id, "source": source,
             "captured_at": (now - timedelta(seconds=(len(values) - index - 1) * gap)).isoformat(),
             "heart_rate": value, "spo2": 98, "systolic": 116, "diastolic": 75,
             "glucose": 94, "temperature": 36.7, "sleep_hours": 7.4}
            for index, value in enumerate(values)]


class AnalysisTests(unittest.TestCase):
    def test_all_available_references_inside_have_the_requested_message(self):
        history = samples([72])
        references = {metric: {"min_value": history[-1][metric] - 1, "max_value": history[-1][metric] + 1}
                      for metric in METRICS}
        result = evaluate_astronaut(1, history[-1], history, references, now=NOW)
        self.assertEqual(result["state"], "normal")
        self.assertEqual(result["message"], "No se detectaron variaciones respecto a las referencias individuales disponibles.")
        self.assertEqual(result["missing_references"], [])

    def test_inside_and_boundaries(self):
        for value in (60, 72, 80):
            item = evaluate_metric("heart_rate", value, REFERENCE["heart_rate"], NOW.isoformat(), now=NOW)
            self.assertEqual(item["state"], "normal")
            self.assertEqual(item["difference"], 0)

    def test_above_and_below(self):
        for value, side, difference in ((112, "above", 32), (55, "below", 5)):
            item = evaluate_metric("heart_rate", value, REFERENCE["heart_rate"], NOW.isoformat(), now=NOW)
            self.assertEqual((item["state"], item["comparison"], item["difference"]), ("variation", side, difference))
            self.assertIn("no confirma", item["description"])

    def test_missing_and_partial_references_do_not_mean_normal(self):
        history = samples([72])
        empty = evaluate_astronaut(1, history[-1], history, {}, now=NOW)
        self.assertEqual(empty["state"], "no_reference")
        self.assertEqual(len(empty["missing_references"]), 7)
        partial = evaluate_astronaut(1, history[-1], history, REFERENCE, now=NOW)
        self.assertEqual(partial["metrics"]["heart_rate"]["state"], "normal")
        self.assertEqual(partial["state"], "no_reference")
        self.assertEqual(len(partial["missing_references"]), 6)

    def test_invalid_values_dates_and_references(self):
        for value in (None, True, "72", float("nan"), float("inf"), 10 ** 400):
            item = evaluate_metric("heart_rate", value, REFERENCE["heart_rate"], NOW.isoformat(), now=NOW)
            self.assertEqual(item["state"], "insufficient")
            self.assertIsNone(item["current_value"])
        for at in (None, "bad-date", "2026-11-14T12:00:00", (NOW + timedelta(seconds=1)).isoformat()):
            self.assertEqual(evaluate_metric("heart_rate", 72, REFERENCE["heart_rate"], at, now=NOW)["state"], "insufficient")
        for reference in ({}, {"min_value": 60}, {"min_value": 80, "max_value": 60}, {"min_value": True, "max_value": 80}):
            self.assertEqual(evaluate_metric("heart_rate", 72, reference, NOW.isoformat(), now=NOW)["state"], "no_reference")

    def test_few_old_or_gapped_samples_are_not_a_current_trend(self):
        for history in (samples([71]), samples([70, 71, 72, 73], gap=1000), samples([70, 71, 72, 73], gap=0.1)):
            self.assertEqual(analyze_trends(history, REFERENCE, 1, now=NOW)["heart_rate"]["state"], "insufficient")
        old = samples([70, 71, 72, 73], now=NOW - timedelta(hours=1))
        result = evaluate_astronaut(1, old[-1], old, REFERENCE, now=NOW)
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["metrics"]["heart_rate"]["trend"]["state"], "stale")

    def test_chronological_rising_and_falling_patterns(self):
        for values, kind in (([70, 71, 72, 73], "increasing"), ([73, 72, 71, 70], "decreasing")):
            history = samples(values)
            result = analyze_trends(list(reversed(history)), REFERENCE, 1, now=NOW)["heart_rate"]
            self.assertEqual(result["sample_count"], 4)
            self.assertEqual(result["span_seconds"], 15)
            self.assertEqual(result["findings"][0]["type"], kind)
            self.assertIn("no confirma", result["context_note"])

    def test_sudden_change_is_demonstration_not_priority(self):
        history = samples([70, 71, 70, 112])
        result = evaluate_astronaut(1, history[-1], history, REFERENCE, now=NOW)
        metric = result["metrics"]["heart_rate"]
        self.assertEqual(metric["state"], "variation")
        self.assertIn("sudden_change", [f["type"] for f in metric["trend"]["findings"]])
        self.assertEqual(len([e for e in result["events"] if e["source"] != "simulation_rule"]), 1)

    def test_repeated_variation_requires_review_without_emergency(self):
        history = samples([90, 91, 92, 93])
        result = evaluate_astronaut(1, history[-1], history, REFERENCE, now=NOW)
        self.assertEqual(result["state"], "review")
        self.assertEqual(result["events"][0]["sources"], ["individual_reference", "trend_analysis"])
        self.assertNotEqual(result["state"], "priority")

    def test_trends_do_not_mix_people_or_measurement_sources(self):
        own = samples([72])
        others = samples([60, 65, 70], astronaut_id=2)
        simulated = samples([60, 65, 70], source="simulated")
        result = analyze_trends(others + simulated + own, REFERENCE, 1, latest=own[-1], now=NOW)["heart_rate"]
        self.assertEqual(result["sample_count"], 1)
        with self.assertRaises(ValueError):
            evaluate_astronaut(2, own[-1], own, REFERENCE, now=NOW)

    def test_invalid_sample_breaks_trend_instead_of_bridging(self):
        history = samples([70, None, 72, 73])
        result = analyze_trends(history, REFERENCE, 1, now=NOW)["heart_rate"]
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["state"], "insufficient")

    def test_priority_only_from_explicit_declared_validated_protocol(self):
        history = samples([112])
        # Fixture de prueba de configuración; NO representa un protocolo clínico validado.
        rule = {"id": "test-only", "title": "Fixture sintético", "metric": "heart_rate",
                "operator": "above", "threshold": 100, "enabled": True, "validated": True,
                "approved_by": "Responsable ficticio de prueba", "validation_reference": "fixture-no-clinico"}
        for protocol in ({**rule, "validated": False}, {**rule, "enabled": False}, {**rule, "validation_reference": ""}):
            self.assertEqual(evaluate_astronaut(1, history[-1], history, REFERENCE, now=NOW, protocols=[protocol])["state"], "variation")
        result = evaluate_astronaut(1, history[-1], history, REFERENCE, now=NOW, protocols=[rule])
        self.assertEqual(result["state"], "priority")
        self.assertEqual(result["events"][0]["source"], "configured_protocol")

    def test_educational_rules_stay_independent(self):
        history = samples([115], source="simulated")
        refs = {"heart_rate": {"min_value": 110, "max_value": 120}}
        result = evaluate_astronaut(1, history[-1], history, refs, now=NOW)
        self.assertEqual(result["metrics"]["heart_rate"]["state"], "normal")
        event = result["events"][0]
        self.assertEqual(event["source"], "simulation_rule")
        self.assertIsNone(event["reference"])

    def test_rule_configuration_and_finite_json(self):
        for kwargs in ({"trend_samples": 1}, {"max_age_seconds": -1}, {"max_age_seconds": float("nan")},
                       {"min_interval_seconds": 1000}, {"history_limit": 3}):
            with self.assertRaises(ValueError):
                AnalysisRules(**kwargs)
        item = evaluate_metric("heart_rate", 1.7e308, {"min_value": -1.7e308, "max_value": -1.6e308}, NOW.isoformat(), now=NOW)
        json.dumps(item, allow_nan=False)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / "events.sqlite3"
        database.init_db()
        init_medical_db()
        init_health_events_db()
        self.module = importlib.import_module("app")
        self.client = self.module.app.test_client()
        self.previous_rules = self.module.app.config["ARES_ANALYSIS_RULES"]
        self.module.app.config["ARES_ANALYSIS_RULES"] = {"max_age_seconds": 10 ** 9}
        save_medical_profile(1, {"notes": "Demostración", "references": REFERENCE})

    def tearDown(self):
        self.module.app.config["ARES_ANALYSIS_RULES"] = self.previous_rules
        database.DB_PATH = self.previous
        self.temp.cleanup()

    def add(self, value, seconds=0, astronaut_id=1, source="manual"):
        sample = samples([value], astronaut_id, source, now=NOW + timedelta(seconds=seconds))[0]
        del sample["id"]
        database.add_measurements([sample])

    def assess(self, seconds=0):
        return assess_astronaut(1, "manual", now=NOW + timedelta(seconds=seconds))

    def test_repeated_gets_and_new_samples_share_one_episode(self):
        self.add(112)
        first = self.assess()["events"][0]["id"]
        for _ in range(3):
            self.assertEqual(self.assess()["events"][0]["id"], first)
        self.add(114, 5)
        self.assertEqual(self.assess(5)["events"][0]["id"], first)
        events = get_health_events(1)
        self.assertEqual(len(events), 2)  # Un episodio personal y otro educativo independiente.
        self.assertTrue(all(event["occurrences"] == 2 for event in events))
        self.assertTrue(all(event["current_value"] == 114 for event in events))
        self.assertEqual({event["source"] for event in events}, {"individual_reference", "simulation_rule"})

    def test_episode_resolves_and_new_condition_creates_a_new_episode(self):
        self.add(112)
        first = self.assess()["events"][0]["id"]
        self.add(72, 5)
        self.assess(5)
        self.assertEqual(get_health_events(1)[0]["status"], "resolved")
        self.add(112, 10)
        current = self.assess(10)
        event = next(e for e in current["events"] if e["source"] == "individual_reference")
        self.assertNotEqual(event["id"], first)

    def test_stale_data_does_not_claim_condition_resolved(self):
        self.add(112)
        first = self.assess()["events"][0]["id"]
        self.assess(1000)
        self.assertEqual(get_health_events(1)[0]["status"], "unverified")
        self.add(113, 1005)
        self.assertEqual(self.assess(1005)["events"][0]["id"], first)

    def test_reference_edits_preserve_old_events_and_medical_profile(self):
        self.add(112)
        self.assess()
        save_medical_profile(1, {"notes": "Demo editada", "references": {"heart_rate": {"min_value": 100, "max_value": 120}}})
        profile_before = medical_profile(1)
        self.assess()
        event = next(event for event in get_health_events(1) if event["source"] == "individual_reference")
        self.assertEqual(event["status"], "superseded")
        self.assertEqual(medical_profile(1), profile_before)

    def test_concurrent_assessments_deduplicate(self):
        self.add(112)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.assess(), range(8)))
        self.assertEqual(len({result["events"][0]["id"] for result in results}), 1)
        self.assertEqual(len(get_health_events(1)), 2)

    def test_filtering_manual_does_not_close_simulated_episode(self):
        self.add(112, source="simulated")
        assess_astronaut(1, "simulated", now=NOW)
        self.add(72, 5, source="manual")
        self.assess(5)
        simulated = [e for e in get_health_events(1) if e["measurement_source"] == "simulated"]
        self.assertTrue(all(e["status"] == "active" for e in simulated))

    def test_event_foreign_keys_and_measurement_ownership(self):
        self.add(112)
        self.assess()
        with self.assertRaises(sqlite3.IntegrityError):
            with database.connect() as connection:
                connection.execute("UPDATE health_events SET astronaut_id=2")
        with self.assertRaises(sqlite3.IntegrityError):
            with database.connect() as connection:
                connection.execute("UPDATE health_events SET measurement_id=999999")
        with self.assertRaises(sqlite3.IntegrityError):
            with database.connect() as connection:
                connection.execute("UPDATE health_events SET reference_max=NULL")

    def test_no_measurements_for_a_source_are_insufficient_and_do_not_create_events(self):
        result = self.client.get("/api/astronauts/2/assessment?source=manual")
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json["measurement_id"])
        self.assertEqual(result.json["state"], "insufficient")
        self.assertEqual(result.json["events"], [])
        self.assertEqual(get_health_events(2), [])

    def test_educational_episode_does_not_depend_on_personal_reference_changes(self):
        self.add(115, source="simulated")
        before = assess_astronaut(1, "simulated", now=NOW)
        educational = next(event for event in before["events"] if event["source"] == "simulation_rule")
        save_medical_profile(1, {"references": {"heart_rate": {"min_value": 110, "max_value": 120}}})
        after = assess_astronaut(1, "simulated", now=NOW)
        self.assertEqual(next(event for event in after["events"] if event["source"] == "simulation_rule")["id"], educational["id"])

    def test_api_filters_errors_and_no_cross_astronaut_events(self):
        self.add(112)
        result = self.client.get("/api/astronauts/1/assessment?source=manual")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["astronaut_id"], 1)
        self.assertEqual(self.client.get("/api/astronauts/2/events").json, [])
        events = self.client.get("/api/astronauts/1/events?metric=heart_rate&status=active&limit=1")
        self.assertEqual(events.status_code, 200)
        self.assertEqual(len(events.json), 1)
        self.assertIsInstance(events.json[0]["guidance"], list)
        for path in ("/api/astronauts/99/assessment", "/api/astronauts/99/events"):
            self.assertEqual(self.client.get(path).status_code, 404)
        for query in ("metric=unknown", "status=unknown", "limit=x", "limit=0", "limit=201"):
            self.assertEqual(self.client.get("/api/astronauts/1/events?" + query).status_code, 400)
        self.assertEqual(self.client.get("/api/astronauts/1/assessment?source=bad").status_code, 400)

    def test_new_process_and_idempotent_migration_preserve_events(self):
        self.add(112)
        self.assess()
        before = get_health_events(1)
        init_health_events_db()
        self.assertEqual(get_health_events(1), before)
        env = {**os.environ, "ARES_DB_PATH": str(database.DB_PATH), "PYTHONIOENCODING": "utf-8"}
        code = "from app import app; import json; print(json.dumps(app.test_client().get('/api/astronauts/1/events').json))"
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=True)
        self.assertEqual(json.loads(result.stdout), before)

    def test_simulation_and_medical_routes_still_work(self):
        self.assertEqual(self.client.post("/api/simulate").status_code, 201)
        self.assertEqual(len(self.client.get("/api/astronauts").json), 3)
        before = self.client.get("/api/astronauts/1/medical").json
        self.assertEqual(self.client.get("/api/astronauts/1/assessment").status_code, 200)
        self.assertEqual(self.client.get("/api/astronauts/1/medical").json, before)
        self.assertEqual(self.client.get("/api/astronauts/1/history").status_code, 200)


if __name__ == "__main__":
    unittest.main()
