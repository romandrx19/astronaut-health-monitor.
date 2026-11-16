"""Recorre A–E con HTTP Flask y archivos ficticios en un directorio temporal."""
from contextlib import closing
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from create_demo import create_demo


def verify():
    with tempfile.TemporaryDirectory(prefix="ares-e2e-") as temporary:
        demo = create_demo(temporary)
        os.environ["ARES_DB_PATH"] = demo["database"]
        import database
        database.DB_PATH = Path(demo["database"])
        from app import create_app
        app = create_app({"TESTING": True, "ARES_DB_PATH": demo["database"], "ARES_SEED_DEMO": False})
        client = app.test_client()
        results = {}
        page = client.get("/")
        assert page.status_code == 200
        assert [item["id"] for item in client.get("/api/astronauts").json] == [1, 2, 3]
        assert client.post("/api/simulation", json={"running": True}).status_code == 200
        first = client.post("/api/simulate", json={"automatic": True})
        second = client.post("/api/simulate", json={"automatic": True})
        assert first.json["created"] == 3 and second.json["created"] == 0
        client.post("/api/simulation", json={"running": False})
        assert client.get("/api/astronauts/1/history").status_code == 200
        results["A_telemetry"] = "passed"
        profile = {"notes": "EDICIÓN FICTICIA E2E", "references": {"heart_rate": {"min_value": 60, "max_value": 90}}}
        assert client.post("/api/astronauts/1/medical", json=profile).status_code == 200
        assert client.get("/api/astronauts/2/medical").json["notes"] != profile["notes"]
        restarted = create_app({"TESTING": True, "ARES_DB_PATH": demo["database"], "ARES_SEED_DEMO": False}).test_client()
        assert restarted.get("/api/astronauts/1/medical").json["notes"] == profile["notes"]
        results["B_medical"] = "passed"
        measurement = client.post("/api/astronauts/1/measurements/manual", json={"heart_rate": 113}, headers={"Idempotency-Key": "e2e-anomaly"})
        assert measurement.status_code == 201
        assessment = measurement.json["assessment"]
        assert any(event["source"] == "individual_reference" and event["guidance"] for event in assessment["events"])
        before = client.get("/api/astronauts/1/events").json
        client.get("/api/astronauts/1/assessment?source=manual")
        after = client.get("/api/astronauts/1/events").json
        assert [event["id"] for event in before] == [event["id"] for event in after]
        results["C_events"] = "passed"
        assert client.get("/api/research/subjects?page_size=200").json["total"] == 52
        assert client.get("/api/research/sources").json["total"] == 4
        series = client.get("/api/research/series?dataset=CRF&subject_id=C1:C1G0001&entity=vital_signs&metric=heart_rate_bpm").json
        assert series["items"] and series["data_class"] == "historical_research"
        results["D_research"] = "passed"
        headers = {"X-ARES-OCT-Token": app.config["ARES_OCT_SESSION_TOKEN"]}
        exams = client.get("/api/astronauts/1/oct/exams").json["exams"]
        assert len(exams) == 2
        content = (ROOT / "docs/examples/oct-demo-pattern.png").read_bytes()
        upload = client.post(f"/api/oct/exams/{exams[0]['id']}/files?astronaut_id=1", data={"files": (BytesIO(content), "demo.png")}, headers=headers)
        upload.request.environ["wsgi.input"].close()
        assert upload.status_code == 201
        preview = client.get(upload.json["files"][0]["content_url"])
        assert preview.status_code == 200
        preview.close()
        comparison = client.get("/api/astronauts/1/oct/comparison").json
        assert comparison["comparisons"] and comparison["baseline_id"]
        results["E_oct"] = "passed"
        with closing(sqlite3.connect(demo["database"])) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        results["sqlite"] = "passed"
        results["F_native_desktop"] = "verified separately"
        return results


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
