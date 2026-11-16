"""Prepara una base ficticia aislada y reproducible para demostrar todos los módulos."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acquisition import store_reading
from devices.normalizer import normalize_manual
from health_events import assess_astronaut
from integrate_nasa import merge_science
from medical import save_medical_profile
from oct_repository import create_exam
from oct_service import validate_exam
from persistence.provider import use_repository
from persistence.sqlite import SQLiteRepository
from simulator import seed_if_empty


def create_demo(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "health.demo.sqlite3"
    if target.exists():
        raise FileExistsError(f"La base de demostración ya existe: {target}")
    repository = SQLiteRepository(target)
    repository.initialize("all")
    source = ROOT / "data/nasa/health_ares_nasa_preservada.sqlite3"
    if not source.exists():
        source = ROOT / "data/nasa/bootstrap.sqlite3"
    if source.exists():
        merge_science(source, target, directory / "backups")
    with use_repository(repository):
        seed_if_empty()
        for astronaut_id in (1, 2, 3):
            save_medical_profile(astronaut_id, {"notes": "PERFIL FICTICIO PARA PRUEBAS. Referencias de ensayo sin validez clínica.",
                "references": {"heart_rate": {"min_value": 60, "max_value": 90}}})
        now = datetime.now(timezone.utc)
        for index, value in enumerate((70, 75, 80, 112)):
            reading = normalize_manual({"heart_rate": value, "observations": "MEDICIÓN FICTICIA DE DEMOSTRACIÓN",
                "acquired_at": (now - timedelta(seconds=(3 - index) * 15)).isoformat()}, 1)
            store_reading(reading, f"demo-manual-{index}")
        assessment = assess_astronaut(1, "manual", now=now)
        exams = []
        for name in ("oct-demo-baseline.json", "oct-demo-followup.json"):
            payload = json.loads((ROOT / "docs/examples" / name).read_text(encoding="utf-8"))
            exam, _ = create_exam(1, validate_exam(payload), [], name)
            exams.append(exam["id"])
    result = {"database": str(target), "astronauts": 3, "research_imported": source.exists(),
              "manual_readings": 4, "events": len(assessment["events"]), "oct_exams": exams,
              "main_database_modified": False}
    (directory / "demo-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(create_demo(arguments.directory), ensure_ascii=False, indent=2))
