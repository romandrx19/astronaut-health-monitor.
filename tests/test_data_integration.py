"""Integración del contrato: servicios y HTTP con SQLite expresamente bloqueado."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

from PIL import Image

import database
from acquisition import DuplicateRequest, ingest_from_adapter, store_reading
from alerts import evaluate
from devices.normalizer import normalize_manual
from devices.testing import DemoAdapter
from fake_repository import FakeRepository
from health_events import assess_astronaut, get_health_events
from medical import medical_profile, save_medical_profile
from oct_repository import create_exam, get_exam, list_exams, update_exam
from oct_service import validate_exam
from persistence.contracts import PersistenceRepository, RepositoryUnavailable
from persistence.provider import get_repository, use_repository
from science_service import safety_protocols
from scientific_rules import load_rule_definitions, validate_rule_definitions
from simulator import seed_if_empty, simulate_step


def oct_payload(**changes):
    return {"is_demo": True, "eye": "OD", "exam_date": "2026-01-01T12:00:00Z",
            "mission_phase": "preflight", "device_model": "Equipo de prueba", "scan_protocol": "Protocolo ficticio",
            "image_quality": "good", "is_baseline": False, "provenance": "manual_entry",
            "measurements": [{"metric": "rnfl", "value": 90, "unit": "µm",
                              "anatomical_region": "Región ficticia", "measurement_method": "Informe de prueba"}], **changes}


def load_application():
    previous = database.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as root:
            database.DB_PATH = Path(root) / "startup.sqlite3"
            return importlib.import_module("app")
    finally:
        database.DB_PATH = previous


class RepositoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = FakeRepository(Path(self.temp.name) / "images")
        self.no_sqlite = patch("sqlite3.connect", side_effect=AssertionError("SQLite no debe usarse con el repositorio falso"))
        self.no_sqlite.start()
        self.addCleanup(self.no_sqlite.stop)
        self.app = self.module.create_app({"TESTING": True, "ARES_DATA_BACKEND": "custom",
                                          "ARES_REPOSITORY_FACTORY": lambda config: self.repository})
        self.client = self.app.test_client()
        self.now = datetime.now(timezone.utc)

    def reading(self, value=80, minutes=0, key="reading"):
        normalized = normalize_manual({"heart_rate": value, "acquired_at": (self.now + timedelta(minutes=minutes)).isoformat()}, 1,
                                      now=self.now + timedelta(minutes=max(minutes, 0)))
        return store_reading(normalized, key, repository=self.repository)[0]

    def test_custom_startup_has_no_sqlite_or_automatic_demo_data(self):
        self.assertIsInstance(self.repository, PersistenceRepository)
        self.assertIs(self.app.extensions["ares_repository"], self.repository)
        self.assertEqual(self.repository.initialized, ["all"])
        self.assertFalse(self.repository.has_measurements())
        result = self.client.get("/api/astronauts")
        self.assertEqual(result.status_code, 200)
        self.assertEqual([item["id"] for item in result.json], [1, 2, 3])
        self.assertIsNone(result.json[0]["latest"])

    def test_page_and_device_status_render_without_sqlite(self):
        result = self.client.get("/")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"ARES", result.data)
        self.assertFalse(self.client.get("/api/devices/status").json["verified_telemetry_available"])
        self.assertEqual(self.client.get("/api/health").json["status"], "ok")

    def test_simulation_and_idempotent_seed_use_injected_repository(self):
        seed_if_empty(self.repository)
        seed_if_empty(self.repository)
        self.assertEqual(len(self.repository.data["history"]), 72)
        self.assertEqual(len(simulate_step(self.repository)), 3)
        result = self.client.post("/api/simulate")
        self.assertEqual((result.status_code, result.json["created"]), (201, 3))
        self.assertEqual(len(self.repository.data["history"]), 78)
        self.assertTrue(all(item["source"] == "simulated" for item in self.repository.data["history"]))

    def test_partial_http_manual_retry_conflict_and_history_shapes(self):
        path = "/api/astronauts/1/measurements/manual"
        data = {"heart_rate": 112, "acquired_at": self.now.isoformat()}
        headers = {"Idempotency-Key": "manual-test"}
        first = self.client.post(path, json=data, headers=headers)
        repeated = self.client.post(path, json=data, headers=headers)
        self.assertEqual((first.status_code, repeated.status_code), (201, 200))
        saved = first.json["measurement"]
        self.assertEqual(saved, repeated.json["measurement"])
        self.assertIsNone(saved["spo2"])
        self.assertEqual(saved["metrics"], {"heart_rate": {"value": 112.0, "quality": "unknown", "unit": "bpm"}})
        self.assertEqual(self.client.get("/api/astronauts/1/measurements?source=manual").json, [saved])
        self.assertEqual(self.client.get("/api/astronauts/1/history").json[0]["id"], "reading:1")
        conflict = self.client.post(path, json={**data, "heart_rate": 113}, headers=headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(self.repository.data["history"]), 1)

    def test_full_manual_and_source_filters_preserve_legacy_ids(self):
        data = {"heart_rate": 80, "spo2": 98, "systolic": 120, "diastolic": 80,
                "glucose": 90, "temperature": 36.7, "sleep_hours": 7}
        result = self.client.post("/api/astronauts/1/measurements", json=data)
        self.assertEqual(result.status_code, 201)
        self.assertIsInstance(result.json["measurement"]["id"], int)
        self.assertEqual(result.json["measurement"]["source"], "manual")
        self.assertEqual(len(self.client.get("/api/astronauts/1/history?source=manual").json), 1)
        self.assertEqual(self.client.get("/api/astronauts/1/history?source=simulation").json, [])

    def test_adapter_ingestion_remains_marked_as_simulation(self):
        with use_repository(self.repository):
            saved, created = ingest_from_adapter(DemoAdapter({"metrics": {"heart_rate": {"value": 80, "unit": "bpm"}}}), 1, "adapter-test")
        self.assertTrue(created)
        self.assertEqual(saved["source"], "simulation")
        self.assertEqual(saved["provenance"], "adapter_test")

    def test_medical_validation_replacement_and_owner_isolation(self):
        self.assertEqual(medical_profile(1, repository=self.repository)["references"], {})
        path = "/api/astronauts/1/medical"
        data = {"notes": "Prueba", "references": {"heart_rate": {"min_value": 60, "max_value": 85}}}
        first = self.client.post(path, json=data)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.client.get(path).json, first.json)
        self.assertEqual(self.client.get("/api/astronauts/2/medical").json["notes"], "")
        bad = self.client.post(path, json={"notes": "no guardar", "references": {"heart_rate": {"min_value": 100, "max_value": 60}}})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(path).json, first.json)
        self.assertEqual(self.client.post(path, json={"notes": "Sustituido"}).json["references"], {})

    def test_events_deduplicate_increment_resolve_and_keep_reading_relation(self):
        save_medical_profile(1, {"references": {"heart_rate": {"min_value": 60, "max_value": 85}}}, repository=self.repository)
        self.reading(90)
        for _ in range(2):
            assess_astronaut(1, "manual", now=self.now, repository=self.repository)
        events = get_health_events(1, repository=self.repository)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["occurrences"], events[0]["reading_id"], events[0]["measurement_id"]), (1, 1, None))
        self.reading(92, minutes=1, key="second")
        assess_astronaut(1, "manual", now=self.now + timedelta(minutes=1), repository=self.repository)
        self.assertEqual(get_health_events(1, repository=self.repository)[0]["occurrences"], 2)
        self.reading(80, minutes=2, key="third")
        assess_astronaut(1, "manual", now=self.now + timedelta(minutes=2), repository=self.repository)
        result = self.client.get("/api/astronauts/1/events?metric=heart_rate&status=resolved")
        self.assertEqual(len(result.json), 1)
        self.assertEqual(result.json[0]["id"], events[0]["id"])

    def test_event_failure_rolls_back_whole_reconciliation(self):
        self.reading(120)
        before = deepcopy(self.repository.data)
        original = self.repository.insert_health_event
        def fail_after_insert(event):
            original(event)
            raise RuntimeError("fallo inducido")
        with patch.object(self.repository, "insert_health_event", side_effect=fail_after_insert):
            with self.assertRaisesRegex(RuntimeError, "inducido"):
                assess_astronaut(1, repository=self.repository)
        self.assertEqual(self.repository.data, before)

    def test_repository_rules_drive_alerts_and_assessment(self):
        self.repository.rules[0]["condition"] = {"operator": "above", "threshold": 70}
        self.repository.rules[0]["message"] = "Regla ficticia inyectada"
        self.reading(80)
        result = self.client.get("/api/astronauts")
        self.assertEqual(result.json[0]["alerts"][0]["message"], "Regla ficticia inyectada")
        assessment = self.client.get("/api/astronauts/1/assessment").json
        self.assertTrue(any("Regla ficticia inyectada" in item["description"] for item in assessment["events"]))
        catalog = self.client.get("/api/rules").json
        self.assertEqual(catalog["rules"][0]["version"], "1")
        self.assertEqual(catalog["rules"][0]["validation_status"], "demonstration")

    def test_empty_rule_catalog_does_not_fall_back_to_bundled_thresholds(self):
        self.repository.rules = []
        saved = self.reading(120)
        self.assertEqual(evaluate(saved, repository=self.repository), [])
        self.assertEqual(assess_astronaut(1, repository=self.repository)["events"], [])

    def test_configured_protocol_requires_declared_validation_and_preserves_version(self):
        rule = deepcopy(self.repository.rules[0])
        rule.update(id="test.protocol", scope="safety_protocol", validation_status="unvalidated",
                    condition={"operator": "above", "threshold": 70}, approved_by="Responsable de prueba",
                    validation_reference="Referencia ficticia para pruebas", version="2", guidance=["Orientación de prueba"])
        self.repository.rules = [rule]
        self.assertEqual(safety_protocols(self.repository), [])
        rule["validation_status"] = "validated"
        self.reading(80)
        with self.app.app_context():
            result = assess_astronaut(1, protocols=safety_protocols(), repository=self.repository)
        event = result["events"][0]
        self.assertEqual(result["state"], "priority")
        self.assertEqual(event["protocol"]["version"], "2")
        self.assertEqual(event["guidance"][0], "Orientación de prueba")

    def test_context_override_is_restored_even_after_error(self):
        with self.app.app_context():
            selected = get_repository()
            other = FakeRepository(Path(self.temp.name) / "other")
            with self.assertRaises(RuntimeError):
                with use_repository(other):
                    self.assertIs(get_repository(), other)
                    raise RuntimeError()
            self.assertIs(get_repository(), selected)

    def test_two_custom_apps_do_not_share_data_or_configuration(self):
        other = FakeRepository(Path(self.temp.name) / "other")
        app2 = self.module.create_app({"TESTING": True, "ARES_DATA_BACKEND": "custom", "ARES_REPOSITORY_FACTORY": lambda _: other})
        self.reading(80)
        self.assertEqual(app2.test_client().get("/api/astronauts/1/history").json, [])
        self.assertEqual(len(self.client.get("/api/astronauts/1/history").json), 1)
        self.assertNotEqual(self.app.config["ARES_OCT_SESSION_TOKEN"], app2.config["ARES_OCT_SESSION_TOKEN"])

    def test_incomplete_or_missing_custom_adapter_and_unknown_backend_fail_clearly(self):
        for config, exception in (({"ARES_DATA_BACKEND": "unknown"}, ValueError),
                                  ({"ARES_DATA_BACKEND": "custom"}, ValueError),
                                  ({"ARES_DATA_BACKEND": "custom", "ARES_REPOSITORY_FACTORY": "module_missing_ares:factory"}, ValueError),
                                  ({"ARES_DATA_BACKEND": "custom", "ARES_REPOSITORY_FACTORY": lambda _: object()}, TypeError)):
            with self.subTest(config=config), self.assertRaises(exception):
                self.module.create_app(config)

    def test_importable_factory_receives_configuration_and_returns_working_adapter(self):
        factory_module = ModuleType("ares_test_factory")
        received = []
        def factory(config):
            received.append(config["TEST_ADAPTER_OPTION"])
            return self.repository
        factory_module.create = factory
        with patch.dict("sys.modules", {factory_module.__name__: factory_module}):
            app = self.module.create_app({"ARES_DATA_BACKEND": "custom", "ARES_REPOSITORY_FACTORY": "ares_test_factory:create",
                                         "TEST_ADAPTER_OPTION": "configured"})
        self.assertEqual(received, ["configured"])
        self.assertEqual(app.test_client().get("/api/astronauts").status_code, 200)

    def test_oct_rule_from_provider_requires_validation_and_matching_context(self):
        self.client.get("/")
        headers = {"X-ARES-OCT-Token": self.app.config["ARES_OCT_SESSION_TOKEN"]}
        baseline = oct_payload(is_baseline=True)
        current = oct_payload(exam_date="2026-02-01T12:00:00Z")
        current["measurements"][0]["value"] = 95
        for data in (baseline, current):
            self.assertEqual(self.client.post("/api/astronauts/1/oct/exams", json=data, headers=headers).status_code, 201)
        rule = {"id": "test.oct", "scope": "oct_review", "metric": "rnfl", "unit": "µm",
                "condition": {"operator": "absolute_change_at_least", "threshold": 4},
                "source": {"title": "Fuente ficticia de prueba", "reference": "Solo pruebas", "url": None},
                "version": "1", "validation_status": "unvalidated", "message": "Regla OCT inyectada",
                "guidance": ["Texto de prueba"], "enabled": True, "approved_by": "Prueba",
                "validation_reference": "Referencia de prueba", "context": {
                    "anatomical_region": "Región ficticia", "measurement_method": "Informe de prueba",
                    "device_model": "Equipo de prueba", "scan_protocol": "Protocolo ficticio"}}
        self.repository.rules = [rule]
        path = "/api/astronauts/1/oct/comparison"
        self.assertFalse(any(item.get("reason") == rule["message"] for item in self.client.get(path).json["notices"]))
        rule["validation_status"] = "validated"
        self.assertTrue(any(item.get("reason") == rule["message"] for item in self.client.get(path).json["notices"]))
        rule["context"]["device_model"] = "Otro equipo"
        self.assertFalse(any(item.get("reason") == rule["message"] for item in self.client.get(path).json["notices"]))

    def test_unavailable_repository_returns_json_503(self):
        with patch.object(self.repository, "list_astronauts", side_effect=RepositoryUnavailable("Almacén no disponible")):
            result = self.client.get("/api/astronauts")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json, {"error": "Almacén no disponible"})

    def test_oct_create_retry_baseline_replace_and_private_fields(self):
        first, created = create_exam(1, validate_exam(oct_payload(is_baseline=True)), [], "oct-test", repository=self.repository)
        repeated, created_again = create_exam(1, validate_exam(oct_payload(is_baseline=True)), [], "oct-test", repository=self.repository)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, repeated)
        self.assertNotIn("request_hash", first)
        second, _ = create_exam(1, validate_exam(oct_payload(exam_date="2026-02-01T12:00:00Z")), [], repository=self.repository)
        update_exam(second["id"], 1, {"is_baseline": True}, repository=self.repository)
        self.assertFalse(get_exam(first["id"], 1, repository=self.repository)["is_baseline"])
        self.assertEqual(len(list_exams(1, repository=self.repository)), 2)
        with self.assertRaises(LookupError):
            get_exam(first["id"], 2, repository=self.repository)

    def test_oct_failure_restores_previous_baseline(self):
        first, _ = create_exam(1, validate_exam(oct_payload(is_baseline=True)), [], repository=self.repository)
        before = deepcopy(self.repository.data)
        with patch.object(self.repository, "add_oct_files", side_effect=RuntimeError("fallo de adjuntos")):
            with self.assertRaises(RuntimeError):
                create_exam(1, validate_exam(oct_payload(is_baseline=True)), [], repository=self.repository)
        self.assertEqual(before, self.repository.data)
        self.assertTrue(get_exam(first["id"], 1, repository=self.repository)["is_baseline"])

    def test_oct_http_images_comparison_review_and_summary_use_custom_storage(self):
        self.client.get("/")
        headers = {"X-ARES-OCT-Token": self.app.config["ARES_OCT_SESSION_TOKEN"]}
        output = BytesIO()
        Image.new("L", (12, 12), color=35).save(output, format="PNG")
        result = self.client.post("/api/astronauts/1/oct/exams", headers=headers,
                                  data={"exam": json.dumps(oct_payload(is_baseline=True)), "files": (BytesIO(output.getvalue()), "prueba.png")})
        result.request.environ["wsgi.input"].close()
        self.assertEqual(result.status_code, 201)
        exam = result.json["exam"]
        self.assertNotIn("preview_path", exam["files"][0])
        content = self.client.get(exam["files"][0]["content_url"])
        self.assertEqual((content.status_code, content.mimetype), (200, "image/png"))
        content.close()
        self.assertTrue(any(self.repository.root.rglob("*.png")))
        later = self.client.post("/api/astronauts/1/oct/exams", headers=headers,
                                 json=oct_payload(exam_date="2026-02-01T12:00:00Z", mission_phase="inflight"))
        self.assertEqual(later.status_code, 201)
        compared = self.client.get("/api/astronauts/1/oct/comparison").json
        self.assertEqual(compared["baseline_id"], exam["id"])
        self.assertEqual(self.client.get("/api/astronauts/1/oct/summary").json["exam_count"], 2)


class RuleContractTests(unittest.TestCase):
    def test_bundled_rules_explicitly_declare_demo_sources_and_versions(self):
        rules = load_rule_definitions()
        self.assertEqual(len(rules), 7)
        self.assertTrue(all(item["validation_status"] == "demonstration" and item["version"] == "1" for item in rules))
        self.assertTrue(all(item["source"]["url"] is None for item in rules))

    def test_invalid_conditions_units_sources_and_duplicate_ids_are_rejected(self):
        rule = load_rule_definitions()[0]
        for changes in ({"unit": "inventada"}, {"condition": {"operator": "eval", "threshold": 1}},
                        {"condition": {"operator": "above", "threshold": float("nan")}},
                        {"validation_status": "validated", "source": {"title": "", "reference": "", "url": None}},
                        {"version": ""}, {"guidance": "texto"}, {"approved_by": " "}, {"validation_reference": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_rule_definitions([{**rule, **changes}])
        with self.assertRaises(ValueError):
            validate_rule_definitions([rule, rule])


class SQLiteSelectionTests(unittest.TestCase):
    def test_explicit_sqlite_paths_and_rule_files_are_isolated(self):
        module = load_application()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            rules_path = root / "rules.json"
            rules_path.write_text("[]", encoding="utf-8")
            app1 = module.create_app({"TESTING": True, "ARES_DB_PATH": root / "one.sqlite3",
                                      "ARES_SEED_DEMO": False, "ARES_RULES_PATH": rules_path})
            app2 = module.create_app({"TESTING": True, "ARES_DB_PATH": root / "two.sqlite3", "ARES_SEED_DEMO": False})
            client = app1.test_client()
            self.assertEqual(client.post("/api/astronauts/1/measurements/manual", json={"heart_rate": 120}).status_code, 201)
            self.assertEqual(client.get("/api/rules").json["rules"], [])
            self.assertEqual(app2.test_client().get("/api/astronauts/1/history").json, [])
            restarted = module.create_app({"TESTING": True, "ARES_DB_PATH": root / "one.sqlite3", "ARES_SEED_DEMO": False})
            self.assertEqual(len(restarted.test_client().get("/api/astronauts/1/history").json), 1)


if __name__ == "__main__":
    unittest.main()
