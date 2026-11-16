"""Doble de persistencia para probar servicios sin conexiones ni SQL."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from measurements import FIELDS
from persistence.contracts import DuplicateRequest, canonical_source
from scientific_rules import load_rule_definitions


class FakeRepository:
    def __init__(self, root):
        self.root = Path(root)
        self.rules = load_rule_definitions()
        self.initialized = []
        self.data = {
            "astronauts": [{"id": i, "name": f"Tripulante ficticio {i}", "role": "Prueba", "mission": "DEMO"} for i in (1, 2, 3)],
            "history": [], "profiles": {}, "requests": {}, "events": {}, "exams": {}, "files": {},
            "simulation": {"running": False, "last_generated_at": None, "revision": 0},
            "ids": {key: 0 for key in ("legacy", "reading", "event", "exam", "measurement", "file")},
        }

    def initialize(self, scope="all"):
        self.initialized.append(scope)

    @contextmanager
    def transaction(self, *, write=False):
        snapshot = deepcopy(self.data)
        try:
            yield self
        except Exception:
            if write:
                self.data = snapshot
            raise

    def _id(self, key):
        self.data["ids"][key] += 1
        return self.data["ids"][key]

    def _require(self, astronaut_id):
        if self.get_astronaut(astronaut_id) is None:
            raise LookupError("Astronauta no encontrado")

    def list_astronauts(self):
        return deepcopy(self.data["astronauts"])

    def get_astronaut(self, astronaut_id):
        return next((item for item in self.list_astronauts() if item["id"] == astronaut_id), None)

    def get_history(self, astronaut_id, limit=36, source=None):
        items = [item for item in self.data["history"] if item["astronaut_id"] == astronaut_id
                 and (source is None or canonical_source(item["source"]) == canonical_source(source))]
        items.sort(key=lambda item: (datetime.fromisoformat(item["captured_at"].replace("Z", "+00:00")),
                                    "reading_id" in item, item.get("reading_id", item["id"])))
        return deepcopy(items[-limit:])

    def has_measurements(self):
        return bool(self.data["history"])

    def get_simulation_state(self):
        return deepcopy(self.data["simulation"])

    def save_simulation_state(self, state):
        self.data["simulation"] = deepcopy(state)

    def add_measurements(self, items):
        with self.transaction(write=True):
            for item in items:
                self.add_manual_measurement({**item, "source": item.get("source", "simulated")})

    def add_manual_measurement(self, sample):
        self._require(sample["astronaut_id"])
        result = {**deepcopy(sample), "id": self._id("legacy")}
        self.data["history"].append(result)
        return deepcopy(result)

    def store_reading(self, reading, request_id):
        self._require(reading.astronaut_id)
        key = (reading.astronaut_id, reading.source, request_id)
        previous = self.data["requests"].get(key)
        if previous:
            if previous[0] != reading.fingerprint:
                raise DuplicateRequest("La clave de solicitud ya se utilizó para una medición diferente")
            return deepcopy(previous[1]), False
        reading_id = self._id("reading")
        result = {field: reading.metrics.get(field, {}).get("value") for field in FIELDS}
        result.update(id=f"reading:{reading_id}", reading_id=reading_id, astronaut_id=reading.astronaut_id,
                      captured_at=reading.acquired_at, acquired_at=reading.acquired_at, received_at=reading.received_at,
                      source=reading.source, device_id=reading.device_id, provenance=reading.provenance,
                      observations=reading.observations, request_id=request_id,
                      quality={key: item["quality"] for key, item in reading.metrics.items()},
                      units={key: item["unit"] for key, item in reading.metrics.items()})
        self.data["history"].append(result)
        self.data["requests"][key] = (reading.fingerprint, deepcopy(result))
        return deepcopy(result), True

    def get_medical_profile(self, astronaut_id):
        self._require(astronaut_id)
        return deepcopy(self.data["profiles"].get(astronaut_id, {
            "astronaut_id": astronaut_id, "notes": "", "references": {}, "updated_at": None}))

    def save_medical_profile(self, astronaut_id, notes, references, updated_at):
        self._require(astronaut_id)
        self.data["profiles"][astronaut_id] = deepcopy({"astronaut_id": astronaut_id, "notes": notes,
                                                       "references": references, "updated_at": updated_at})
        return self.get_medical_profile(astronaut_id)

    def get_open_events(self, astronaut_id, source):
        return [event for event in self.list_health_events(astronaut_id, 10000)
                if event["measurement_source"] == source and event["status"] in ("active", "unverified")]

    def insert_health_event(self, event):
        event_id = self._id("event")
        self.data["events"][event_id] = deepcopy({**event, "id": event_id, "occurrences": 1, "resolved_at": None})
        return event_id

    def update_health_event(self, event_id, astronaut_id, changes):
        event = self.data["events"].get(event_id)
        if not event or event["astronaut_id"] != astronaut_id:
            raise LookupError("Evento no encontrado para este astronauta")
        event.update(deepcopy(changes))

    def list_health_events(self, astronaut_id, limit=50, metric=None, status=None):
        self._require(astronaut_id)
        events = [item for item in self.data["events"].values() if item["astronaut_id"] == astronaut_id
                  and (metric is None or item["metric"] == metric) and (status is None or item["status"] == status)]
        return deepcopy(sorted(events, key=lambda item: (item["detected_at"], item["id"]), reverse=True)[:limit])

    def list_oct_exams(self, astronaut_id):
        self._require(astronaut_id)
        exams = [self.get_oct_exam(item["id"], astronaut_id) for item in self.data["exams"].values()
                 if item["astronaut_id"] == astronaut_id]
        return sorted(exams, key=lambda item: (item["exam_date"], item["id"]), reverse=True)

    def get_oct_exam(self, exam_id, astronaut_id):
        exam = self.data["exams"].get(exam_id)
        if not exam or exam["astronaut_id"] != astronaut_id:
            raise LookupError("Examen OCT no encontrado para este astronauta")
        files = [item for item in self.data["files"].values() if item["exam_id"] == exam_id]
        return deepcopy({**exam, "files": files})

    def find_oct_request(self, request_key):
        for exam in self.data["exams"].values():
            if exam["request_key"] == request_key:
                return self.get_oct_exam(exam["id"], exam["astronaut_id"])
        return None

    def insert_oct_exam(self, exam, measurements):
        exam_id = self._id("exam")
        items = [{**deepcopy(item), "id": self._id("measurement"), "exam_id": exam_id} for item in measurements]
        self.data["exams"][exam_id] = deepcopy({**exam, "id": exam_id, "measurements": items})
        return exam_id

    def update_oct_exam(self, exam_id, astronaut_id, changes):
        self.get_oct_exam(exam_id, astronaut_id)
        self.data["exams"][exam_id].update(deepcopy(changes))
        return self.get_oct_exam(exam_id, astronaut_id)

    def add_oct_files(self, exam_id, attachments):
        for item in attachments:
            file_id = self._id("file")
            self.data["files"][file_id] = deepcopy({**item, "id": file_id, "exam_id": exam_id})

    def get_oct_file(self, file_id, astronaut_id):
        attachment = self.data["files"].get(file_id)
        if not attachment:
            raise LookupError("Archivo OCT no encontrado para este astronauta")
        self.get_oct_exam(attachment["exam_id"], astronaut_id)
        return deepcopy(attachment)

    def get_rule_definitions(self):
        return deepcopy(self.rules)

    def oct_storage_root(self):
        return self.root
