"""OCT con imágenes sintéticas y una base temporal por prueba."""
from io import BytesIO
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import database
from health_events import init_health_events_db
from medical import init_medical_db
from oct_analysis import compare_exams, validate_review_rules
from oct_files import MAX_FILE_BYTES, resolve_private, storage_root
from oct_repository import init_oct_db


def image_bytes(format="PNG", color=25):
    output = BytesIO()
    Image.new("L", (64, 32), color=color).save(output, format=format)
    return output.getvalue()


def exam_payload(**changes):
    return {"is_demo": True, "eye": "OD", "exam_date": "2026-01-01T12:00:00Z", "mission_phase": "preflight",
            "device_model": "Equipo OCT sintético", "scan_protocol": "Protocolo de prueba A", "image_quality": "good",
            "is_baseline": False, "notes": "Datos ficticios de prueba", "provenance": "manual_entry",
            "measurements": [{"metric": "rnfl", "value": 90, "unit": "µm", "anatomical_region": "Anillo sintético",
                              "measurement_method": "Informe sintético v1"}], **changes}


class OctTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_db = database.DB_PATH
        database.DB_PATH = Path(self.temp.name) / "oct.sqlite3"
        database.init_db()
        init_medical_db()
        init_health_events_db()
        init_oct_db()
        self.module = importlib.import_module("app")
        self.app = self.module.app
        self.previous_config = {key: self.app.config.get(key) for key in ("ARES_OCT_STORAGE", "ARES_OCT_REVIEW_RULES", "TESTING")}
        self.root = Path(self.temp.name) / "private"
        self.app.config.update(ARES_OCT_STORAGE=str(self.root), ARES_OCT_REVIEW_RULES=[], TESTING=True)
        self.client = self.app.test_client()
        self.client.get("/")
        self.headers = {"X-ARES-OCT-Token": self.app.config["ARES_OCT_SESSION_TOKEN"]}

    def tearDown(self):
        self.app.config.update(self.previous_config)
        database.DB_PATH = self.previous_db
        self.temp.cleanup()

    def create(self, payload=None, astronaut_id=1, files=None, key=None):
        headers = {**self.headers}
        if key:
            headers["Idempotency-Key"] = key
        path = f"/api/astronauts/{astronaut_id}/oct/exams"
        if files is None:
            return self.client.post(path, json=payload or exam_payload(), headers=headers)
        data = {"exam": json.dumps(payload or exam_payload()), "files": [(BytesIO(content), name) for content, name in files]}
        response = self.client.post(path, data=data, headers=headers, content_type="multipart/form-data")
        response.request.environ["wsgi.input"].close()
        return response

    def current(self, **changes):
        return self.create(exam_payload(exam_date="2026-02-01T12:00:00Z", mission_phase="inflight", **changes)).json["exam"]

    def compare(self, current_id=None, **params):
        if current_id:
            params["current_id"] = current_id
        return self.client.get("/api/astronauts/1/oct/comparison", query_string=params)

    def count(self, table="oct_exams"):
        with database.connect() as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_create_each_eye_and_astronaut_and_retrieve_quantitative_values(self):
        for astronaut_id, eye in ((1, "OD"), (1, "OS"), (2, "OD")):
            result = self.create(exam_payload(eye=eye), astronaut_id)
            self.assertEqual(result.status_code, 201)
            saved = result.json["exam"]
            self.assertEqual((saved["astronaut_id"], saved["eye"]), (astronaut_id, eye))
            self.assertTrue(saved["is_demo"])
            detail = self.client.get(f'/api/oct/exams/{saved["id"]}?astronaut_id={astronaut_id}')
            self.assertEqual(detail.json, saved)
            measurements = self.client.get(f'/api/oct/exams/{saved["id"]}/measurements?astronaut_id={astronaut_id}')
            self.assertEqual(measurements.json[0]["value"], 90)
        self.assertEqual(len(self.client.get("/api/astronauts/1/oct/exams").json["exams"]), 2)
        self.assertEqual(len(self.client.get("/api/astronauts/2/oct/exams").json["exams"]), 1)

    def test_bad_or_real_mode_payloads_are_rejected_without_records(self):
        invalid = [None, [], {"is_demo": False}, exam_payload(eye="OU"), exam_payload(mission_phase="landing"),
                   exam_payload(device_model=[]), exam_payload(scan_protocol={}), exam_payload(is_baseline=1),
                   exam_payload(exam_date="2026-01-01"), exam_payload(exam_date="2999-01-01T00:00:00Z"),
                   exam_payload(review_status=[]), exam_payload(measurements="90"), exam_payload(unexpected="x")]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = self.client.post("/api/astronauts/1/oct/exams", data=json.dumps(payload), content_type="application/json", headers=self.headers)
                self.assertEqual(result.status_code, 400)
        self.assertEqual(self.count(), 0)

    def test_optional_measurements_do_not_invent_missing_values(self):
        saved = self.create(exam_payload(measurements=[])).json["exam"]
        self.assertEqual(saved["measurements"], [])
        payload = exam_payload()
        payload["measurements"][0]["value"] = 0
        payload["measurements"][0]["unit"] = "um"
        saved = self.create(payload).json["exam"]
        self.assertEqual(saved["measurements"][0]["value"], 0)
        self.assertEqual(saved["measurements"][0]["unit"], "µm")
        self.assertEqual(len(saved["measurements"]), 1)

    def test_invalid_duplicate_and_nonfinite_measurements_are_rejected(self):
        for field, value in (("value", True), ("value", float("nan")), ("value", float("inf")),
                             ("value", "90"), ("anatomical_region", {}), ("measurement_method", []), ("unit", []), ("metric", "rnfl<script>")):
            payload = exam_payload()
            payload["measurements"][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.create(payload).status_code, 400)
        payload = exam_payload()
        payload["measurements"] *= 2
        self.assertEqual(self.create(payload).status_code, 400)

    def test_partial_measurements_preserve_values_and_missing_context_prevents_comparison(self):
        payload = exam_payload(is_baseline=True, device_model="", scan_protocol="", measurements=[{"metric": "rnfl", "value": 90}])
        baseline_response = self.create(payload)
        self.assertEqual(baseline_response.status_code, 201)
        baseline = baseline_response.json["exam"]
        item = baseline["measurements"][0]
        self.assertEqual(item["value"], 90)
        for field in ("unit", "anatomical_region", "measurement_method"):
            self.assertEqual(item[field], "")
        payload.update(is_baseline=False, exam_date="2026-02-01T12:00:00Z")
        current = self.create(payload).json["exam"]
        self.assertEqual(self.compare(current["id"]).json["comparisons"], [])
        explicit = self.compare(current["id"], baseline_id=baseline["id"]).json
        self.assertTrue(explicit["incompatible"])
        self.assertEqual(explicit["message"], "No existe un examen basal comparable")
        known = self.create(exam_payload(is_baseline=True)).json["exam"]
        payload = exam_payload(exam_date="2026-02-01T12:00:00Z")
        payload["measurements"][0]["anatomical_region"] = "No informada"
        current = self.create(payload).json["exam"]
        self.assertEqual(self.compare(current["id"], baseline_id=known["id"]).json["comparisons"], [])

    def test_exam_owner_required_for_details_mutations_and_files(self):
        saved = self.create(files=[(image_bytes(), "demo.png")]).json["exam"]
        path = f'/api/oct/exams/{saved["id"]}'
        self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.get(path + "?astronaut_id=2").status_code, 404)
        self.assertEqual(self.client.patch(path + "?astronaut_id=2", json={"is_baseline": True}, headers=self.headers).status_code, 404)
        file_url = saved["files"][0]["content_url"].replace("astronaut_id=1", "astronaut_id=2")
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.assertEqual(self.create(astronaut_id=999).status_code, 404)

    def test_png_upload_checks_content_uses_private_names_and_safe_preview(self):
        result = self.create(files=[(image_bytes(), "../../inventada.html")])
        self.assertEqual(result.status_code, 201)
        attachment = result.json["exam"]["files"][0]
        self.assertEqual(attachment["file_type"], "PNG")
        self.assertNotIn("file_path", attachment)
        self.assertEqual(len(attachment["checksum"]), 64)
        self.assertEqual(attachment["original_name"], "inventada.html")
        content = self.client.get(attachment["content_url"])
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.mimetype, "image/png")
        self.assertEqual(content.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", content.headers["Cache-Control"])
        with Image.open(BytesIO(content.data)) as preview:
            self.assertEqual(preview.size, (64, 32))
        content.close()
        self.assertEqual(len(list(self.root.glob("*/original.png"))), 1)

    def test_jpeg_and_single_page_tiff_are_decoded(self):
        for format, name in (("JPEG", "export.jpg"), ("TIFF", "export.tiff")):
            with self.subTest(format=format):
                result = self.create(files=[(image_bytes(format), name)])
                self.assertEqual(result.status_code, 201)
                self.assertEqual(result.json["exam"]["files"][0]["file_type"], format)

    def test_high_bit_depth_tiff_has_documented_preview_and_original(self):
        source = Image.new("I", (32, 16))
        source.putdata([i * 100 for i in range(512)])
        output = BytesIO()
        source.save(output, format="TIFF")
        result = self.create(files=[(output.getvalue(), "synthetic.tiff")])
        self.assertEqual(result.status_code, 201)
        self.assertIn("escala lineal", result.json["exam"]["files"][0]["metadata"]["preview_transform"])

    def test_nonimages_dicoms_and_multipage_images_have_clear_errors(self):
        multipage = BytesIO()
        Image.new("L", (8, 8)).save(multipage, format="TIFF", save_all=True, append_images=[Image.new("L", (8, 8))])
        files = [(b"<svg><script>demo</script></svg>", "test.png"), (b"%PDF-1.0", "test.jpg"),
                 (b"\x00" * 128 + b"DICM" + b"demo", "oct.dcm"), (image_bytes("GIF"), "test.png"),
                 (multipage.getvalue(), "multiple.tiff"), (b"\x89PNG\r\n\x1a\ntruncated", "broken.png")]
        for content, name in files:
            with self.subTest(name=name):
                result = self.create(files=[(content, name)])
                self.assertEqual(result.status_code, 400)
                self.assertTrue(result.json["error"])
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.root.exists())

    def test_file_size_count_and_pixel_limits(self):
        self.assertEqual(self.create(files=[(b"a" * (MAX_FILE_BYTES + 1), "too-big.png")]).status_code, 400)
        files = [(image_bytes(color=i), f"{i}.png") for i in range(6)]
        self.assertEqual(self.create(files=files).status_code, 400)
        output = BytesIO()
        Image.new("1", (8193, 1)).save(output, format="PNG")
        self.assertEqual(self.create(files=[(output.getvalue(), "wide.png")]).status_code, 400)
        self.assertEqual(self.count(), 0)

    def test_bad_second_file_rolls_back_whole_exam_and_private_files(self):
        result = self.create(files=[(image_bytes(), "valid.png"), (b"invalid", "bad.png")])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.count(), 0)
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_transaction_failure_restores_previous_baseline_and_removes_staged_files(self):
        baseline = self.create(exam_payload(is_baseline=True)).json["exam"]
        with patch("oct_repository._insert_files", side_effect=RuntimeError("Injected failure")):
            with self.assertRaises(RuntimeError):
                self.create(exam_payload(is_baseline=True), files=[(image_bytes(), "demo.png")])
        self.assertEqual(self.count(), 1)
        self.assertTrue(self.client.get(f'/api/oct/exams/{baseline["id"]}?astronaut_id=1').json["is_baseline"])
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_retries_are_idempotent_and_changed_payload_conflicts(self):
        first = self.create(files=[(image_bytes(), "demo.png")], key="oct-test-retry")
        repeated = self.create(files=[(image_bytes(), "demo.png")], key="oct-test-retry")
        self.assertEqual((first.status_code, repeated.status_code), (201, 200))
        self.assertEqual(first.json["exam"]["id"], repeated.json["exam"]["id"])
        self.assertFalse(repeated.json["created"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(len(list(self.root.glob("*/original.png"))), 1)
        conflict = self.create(exam_payload(notes="Otro contenido"), key="oct-test-retry")
        self.assertEqual(conflict.status_code, 409)

    def test_attach_rejects_duplicates_and_checks_owner_before_file_write(self):
        saved = self.create(files=[(image_bytes(), "first.png")]).json["exam"]
        path = f'/api/oct/exams/{saved["id"]}/files?astronaut_id=1'
        result = self.client.post(path, data={"files": (BytesIO(image_bytes(color=50)), "next.png")}, headers=self.headers)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(len(result.json["files"]), 2)
        result = self.client.post(path, data={"files": (BytesIO(image_bytes()), "copy.png")}, headers=self.headers)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(list(self.root.glob("*/original.png"))), 2)
        wrong = path.replace("astronaut_id=1", "astronaut_id=2")
        self.assertEqual(self.client.post(wrong, data={"files": (BytesIO(image_bytes(color=70)), "wrong.png")}, headers=self.headers).status_code, 404)

    def test_local_session_origin_and_csrf_controls_protect_oct(self):
        outsider = self.app.test_client()
        self.assertEqual(outsider.get("/api/astronauts/1/oct/exams").status_code, 403)
        self.assertEqual(self.client.get("/api/astronauts/1/oct/exams", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
        self.assertEqual(self.client.get("/api/astronauts/1/oct/exams", headers={"Host": "external.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/astronauts/1/oct/exams", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.post("/api/astronauts/1/oct/exams", json=exam_payload()).status_code, 403)
        self.assertEqual(self.client.post("/api/astronauts/1/oct/exams", json=exam_payload(), headers={**self.headers, "Origin": "https://external.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/astronauts/1/oct/exams", json=exam_payload(), headers={"X-ARES-OCT-Token": "inválido"}).status_code, 403)

    def test_storage_paths_cannot_escape_or_use_public_assets(self):
        with self.assertRaises(ValueError):
            resolve_private(self.root, "../outside.png")
        with self.assertRaises(ValueError):
            storage_root({"ARES_OCT_STORAGE": str(Path(__file__).resolve().parents[1] / "static" / "oct")})

    def test_compatible_comparison_and_descriptive_differences(self):
        baseline = self.create(exam_payload(is_baseline=True)).json["exam"]
        current = self.current()
        current["measurements"][0]["value"] = 99
        payload = exam_payload(exam_date="2026-03-01T12:00:00Z", measurements=[{key: value for key, value in current["measurements"][0].items() if key not in ("id", "exam_id")}])
        latest = self.create(payload).json["exam"]
        result = self.compare(latest["id"]).json
        item = result["comparisons"][0]
        self.assertEqual(result["baseline_id"], baseline["id"])
        self.assertEqual((item["baseline_value"], item["current_value"], item["difference"], item["absolute_difference"], item["percent_difference"]), (90, 99, 9, 9, 10))
        self.assertEqual(len(item["points"]), 3)
        self.assertFalse(any(notice["kind"] == "configured_review" for notice in result["notices"]))

    def test_incompatible_acquisitions_measurements_quality_and_dates_do_not_compare(self):
        baseline = self.create(exam_payload(is_baseline=True)).json["exam"]
        variants = [{"eye": "OS"}, {"device_model": "Equipo diferente"}, {"scan_protocol": "Otro protocolo"},
                    {"image_quality": "poor"}, {"exam_date": "2025-12-01T12:00:00Z"}]
        for changes in variants:
            current = self.create(exam_payload(exam_date="2026-02-01T12:00:00Z", **{key: value for key, value in changes.items() if key != "exam_date"}) if "exam_date" not in changes else exam_payload(**changes)).json["exam"]
            result = self.compare(current["id"], baseline_id=baseline["id"]).json
            self.assertEqual(result["comparisons"], [], changes)
            self.assertTrue(result["incompatible"])
        for field, value in (("unit", "mm"), ("anatomical_region", "Mácula sintética"), ("measurement_method", "Otro método"), ("metric", "trt")):
            payload = exam_payload(exam_date="2026-02-01T12:00:00Z")
            payload["measurements"][0][field] = value
            current = self.create(payload).json["exam"]
            self.assertEqual(self.compare(current["id"], baseline_id=baseline["id"]).json["comparisons"], [])

    def test_units_remain_case_sensitive_except_documented_micrometer_aliases(self):
        payload = exam_payload(is_baseline=True)
        payload["measurements"][0]["unit"] = "mm"
        baseline = self.create(payload).json["exam"]
        payload = exam_payload(exam_date="2026-02-01T12:00:00Z")
        payload["measurements"][0]["unit"] = "mM"
        current = self.create(payload).json["exam"]
        self.assertEqual(self.compare(current["id"], baseline_id=baseline["id"]).json["comparisons"], [])

    def test_no_baseline_is_selected_automatically(self):
        saved = self.create().json["exam"]
        result = self.compare(saved["id"]).json
        self.assertEqual(result["message"], "No existe un examen basal comparable")
        self.assertIsNone(result["baseline_id"])
        self.assertEqual(result["comparisons"], [])
        self.assertFalse(self.client.get("/api/astronauts/1/oct/exams").json["exams"][0]["is_baseline"])

    def test_baseline_is_explicit_and_replacement_preserves_other_eyes_protocols(self):
        first = self.create(exam_payload(is_baseline=True)).json["exam"]
        other_eye = self.create(exam_payload(is_baseline=True, eye="OS")).json["exam"]
        other_protocol = self.create(exam_payload(is_baseline=True, scan_protocol="Protocolo B")).json["exam"]
        current = self.current()
        self.assertEqual(self.compare(current["id"], baseline_id=current["id"]).status_code, 400)
        result = self.client.patch(f'/api/oct/exams/{current["id"]}?astronaut_id=1', json={"is_baseline": True}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        baselines = self.client.get("/api/astronauts/1/oct/summary").json["baselines"]
        self.assertEqual({exam["id"] for exam in baselines}, {current["id"], other_eye["id"], other_protocol["id"]})
        self.assertEqual(self.count(), 4)

    def test_period_and_eye_filters_keep_actual_dates_and_mission_phases(self):
        self.create(exam_payload(is_baseline=True))
        self.current()
        current = self.create(exam_payload(exam_date="2026-03-01T12:00:00Z", mission_phase="postflight")).json["exam"]
        result = self.compare(current["id"], eye="OD", **{"from": "2026-02-01", "to": "2026-03-01"}).json
        self.assertEqual([item["mission_phase"] for item in result["comparisons"][0]["points"]], ["inflight", "postflight"])
        self.assertEqual(self.compare(current["id"], **{"from": "2026-03-01", "to": "2026-02-01"}).status_code, 400)
        self.assertEqual(self.compare(current["id"], eye="OU").status_code, 400)

    def test_zero_baseline_has_difference_without_percentage(self):
        payload = exam_payload(is_baseline=True)
        payload["measurements"][0]["value"] = 0
        self.create(payload)
        result = self.compare(self.current()["id"]).json["comparisons"][0]
        self.assertEqual(result["difference"], 90)
        self.assertIsNone(result["percent_difference"])

    def test_quality_and_provenance_are_reported_even_when_not_supplied(self):
        self.create(exam_payload(is_baseline=True, image_quality="unknown"))
        current = self.current(provenance="exported_report", source_document="Informe sintético.json")
        item = self.compare(current["id"]).json["comparisons"][0]
        self.assertEqual(item["baseline_quality"], "unknown")
        self.assertEqual(item["current_provenance"], "exported_report")

    def test_configured_review_rule_requires_exact_context_and_has_no_default_threshold(self):
        self.create(exam_payload(is_baseline=True))
        payload = exam_payload(exam_date="2026-02-01T12:00:00Z")
        payload["measurements"][0]["value"] = 99
        current = self.create(payload).json["exam"]
        self.assertFalse(any(item["kind"] == "configured_review" for item in self.compare(current["id"]).json["notices"]))
        rule = {**payload["measurements"][0], "device_model": payload["device_model"], "scan_protocol": payload["scan_protocol"],
                "min_absolute_change": 5, "reason": "Regla sintética de prueba", "validation_reference": "Fixture, sin uso clínico"}
        rule.pop("value")
        self.app.config["ARES_OCT_REVIEW_RULES"] = [rule]
        notice = next(item for item in self.compare(current["id"]).json["notices"] if item["kind"] == "configured_review")
        self.assertEqual((notice["previous_value"], notice["current_value"], notice["difference"]), (90, 99, 9))
        self.assertIn("personal médico", notice["guidance"])
        self.app.config["ARES_OCT_REVIEW_RULES"][0]["anatomical_region"] = "Otra región"
        self.assertFalse(any(item["kind"] == "configured_review" for item in self.compare(current["id"]).json["notices"]))

    def test_malformed_review_rules_and_cross_astronaut_comparisons_are_rejected(self):
        for rules in ({}, [None], [{"metric": "rnfl"}]):
            with self.assertRaises(ValueError):
                validate_review_rules(rules)
        baseline = self.create(exam_payload(is_baseline=True), astronaut_id=2).json["exam"]
        current = self.current()
        self.assertEqual(self.compare(current["id"], baseline_id=baseline["id"]).status_code, 404)

    def test_review_updates_ocular_summary_and_preserves_medical_profile(self):
        payload = {"notes": "Antecedentes ficticios existentes", "references": {"heart_rate": {"min_value": 60, "max_value": 90}}}
        self.client.post("/api/astronauts/1/medical", json=payload)
        before = self.client.get("/api/astronauts/1/medical").json
        saved = self.create().json["exam"]
        changes = {"review_status": "reviewed", "reviewed_by": "Profesional ficticio", "reviewed_at": "2026-01-02T12:00:00Z", "review_notes": "Revisión sintética"}
        result = self.client.patch(f'/api/oct/exams/{saved["id"]}?astronaut_id=1', json=changes, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        summary = self.client.get("/api/astronauts/1/oct/summary").json
        self.assertEqual(summary["last_review"]["reviewed_by"], "Profesional ficticio")
        self.assertEqual(summary["exam_count"], 1)
        self.assertEqual(self.client.get("/api/astronauts/1/medical").json, before)
        self.assertEqual(self.client.patch(f'/api/oct/exams/{saved["id"]}?astronaut_id=1', json={"astronaut_id": 2}, headers=self.headers).status_code, 400)

    def test_restart_flask_preserves_exam_and_private_preview(self):
        saved = self.create(files=[(image_bytes(), "synthetic.png")]).json["exam"]
        environment = {**os.environ, "ARES_DB_PATH": str(database.DB_PATH), "PYTHONIOENCODING": "utf-8"}
        code = '''import sys
from app import app
app.config['ARES_OCT_STORAGE']=sys.argv[1]
c=app.test_client(); c.get('/')
r=c.get('/api/oct/exams/'+sys.argv[2]+'?astronaut_id=1')
assert r.status_code==200 and r.json['measurements'][0]['value']==90
image=c.get(r.json['files'][0]['content_url'])
assert image.status_code==200 and image.mimetype=='image/png'
image.close()
print('restart OK')'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root), str(saved["id"])], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restart OK", result.stdout)

    def test_oct_reads_and_writes_preserve_vitals_simulation_and_current_events(self):
        self.client.post("/api/simulate")
        before_history = self.client.get("/api/astronauts/1/history").json
        before_assessment = self.client.get("/api/astronauts/1/assessment").json
        before_events = self.client.get("/api/astronauts/1/events").json
        saved = self.create(exam_payload(is_baseline=True)).json["exam"]
        self.compare(saved["id"])
        self.assertEqual(self.client.get("/api/astronauts/1/history").json, before_history)
        after_assessment = self.client.get("/api/astronauts/1/assessment").json
        self.assertEqual(after_assessment["measurement_id"], before_assessment["measurement_id"])
        self.assertEqual(after_assessment["state"], before_assessment["state"])
        self.assertEqual({item["id"] for item in self.client.get("/api/astronauts/1/events").json}, {item["id"] for item in before_events})
        with patch("simulator.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(seconds=6)
            self.assertEqual(self.client.post("/api/simulate").status_code, 201)
        self.assertEqual(self.count(), 1)
        self.assertEqual(len(self.client.get("/api/astronauts/1/history").json), len(before_history) + 1)

    def test_json_validation_and_seventh_view_use_same_module_and_no_storage_side_effect(self):
        result = self.client.post("/api/oct/validate", json=exam_payload(), headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.count(), 0)
        html = self.client.get("/").get_data(as_text=True)
        for element in ("view-oct", "nav-oct", "oct-title", "oct-chart", "open-oct-medical", "oct-exam-form"):
            self.assertIn(f'id="{element}"', html)
        self.assertIn("Datos OCT de demostración", html)
        for path in ("/static/js/oct.js", "/static/css/oct.css"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
