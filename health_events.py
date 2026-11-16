"""Reconciliación de episodios; ninguna consulta depende del motor de datos."""
from hashlib import sha256
import json
from health_analysis import AnalysisRules, METRICS, SOURCES, evaluate_astronaut
from persistence.provider import get_repository
from science_service import educational_rules, individual_references

EVENT_STATUSES = ("active", "unverified", "resolved", "superseded")


def init_health_events_db(repository=None):
    get_repository(repository).initialize("events")


def _event_dict(row):
    event = dict(row)
    event["source_label"] = SOURCES[event["source"]]
    event["label"], event["unit"] = METRICS[event["metric"]]
    return event


def _condition_key(assessment, event):
    # La severidad y la lectura cambian dentro del mismo episodio; no crean otro evento.
    identity = [assessment["astronaut_id"], assessment["measurement_source"], event["metric"],
                event["event_type"], event["source"], event["reference"]]
    if event["source"] == "trend_analysis":
        identity.append(sorted(f["type"] for f in event["trend"]["findings"]))
        identity.append(assessment["rules"])
    if event["source"] == "configured_protocol":
        identity.append(event["protocol"])
    return sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def _persist_events(repository, assessment):
    astronaut_id, measurement_source = assessment["astronaut_id"], assessment["measurement_source"]
    if measurement_source is None:
        return
    opened = {row["condition_key"]: row for row in repository.get_open_events(astronaut_id, measurement_source)}
    present = set()
    for event in assessment["events"]:
        key = _condition_key(assessment, event)
        present.add(key)
        reference = event["reference"] or {}
        params = {
            "astronaut_id": astronaut_id, "measurement_id": assessment["legacy_measurement_id"],
            "reading_id": assessment["reading_id"], "reading_key": assessment["measurement_key"],
            "metric": event["metric"], "event_type": event["event_type"], "severity": event["severity"],
            "current_value": event["current_value"], "reference_min": reference.get("min_value"),
            "reference_max": reference.get("max_value"), "description": event["description"],
            "guidance": event["guidance"], "source": event["source"],
            "sources": event["sources"], "measurement_source": measurement_source,
            "captured_at": assessment["captured_at"], "detected_at": assessment["evaluated_at"],
            "last_seen_at": assessment["captured_at"], "condition_key": key,
        }
        previous = opened.get(key)
        if previous:
            # Consultar otra vez la misma lectura no incrementa el contador de muestras.
            previous_key = previous["reading_key"] or f"legacy:{previous['measurement_id']}"
            increment = int(previous_key != assessment["measurement_key"])
            updates = {field: params[field] for field in (
                "measurement_id", "reading_id", "reading_key", "severity", "current_value", "description",
                "guidance", "sources", "captured_at", "last_seen_at")}
            updates.update(occurrences=previous["occurrences"] + increment, status="active", resolved_at=None)
            repository.update_health_event(previous["id"], astronaut_id, updates)
            event["id"] = previous["id"]
        else:
            event["id"] = repository.insert_health_event({**params, "status": "active"})
        event["source_label"] = SOURCES[event["source"]]
    for key, previous in opened.items():
        if key in present:
            continue
        metric = assessment["metrics"][previous["metric"]]
        if metric["state"] in ("insufficient", "stale") or (previous["source"] == "trend_analysis" and metric["trend"]["state"] == "insufficient"):
            status = "unverified"
        elif previous["source"] == "individual_reference" and metric["reference"] != {
            "min_value": previous["reference_min"], "max_value": previous["reference_max"],
        }:
            status = "superseded"
        elif previous["source"] == "configured_protocol" and not assessment["active_protocol_count"]:
            status = "superseded"
        else:
            status = "resolved"
        repository.update_health_event(previous["id"], astronaut_id, {
            "status": status, "resolved_at": None if status == "unverified" else assessment["evaluated_at"]})


def assess_astronaut(astronaut_id, source=None, *, rules=None, protocols=(), now=None, repository=None):
    rules = rules or AnalysisRules()
    with get_repository(repository).transaction(write=True) as session:
        if session.get_astronaut(astronaut_id) is None:
            raise LookupError("Astronauta no encontrado")
        references = individual_references(astronaut_id, session)
        history = session.get_history(astronaut_id, rules.history_limit, source)
        assessment = evaluate_astronaut(astronaut_id, history[-1] if history else None,
                                        history, references, now=now, rules=rules, protocols=protocols,
                                        educational_rules=educational_rules(session))
        latest = history[-1] if history else {}
        assessment["reading_id"] = latest.get("reading_id")
        assessment["legacy_measurement_id"] = latest.get("id") if "reading_id" not in latest else None
        assessment["measurement_key"] = (str(latest["id"]) if "reading_id" in latest else f"legacy:{latest.get('id')}")
        _persist_events(session, assessment)
        return assessment


def get_health_events(astronaut_id, limit=50, metric=None, status=None, *, repository=None):
    rows = get_repository(repository).list_health_events(astronaut_id, limit, metric, status)
    return [_event_dict(row) for row in rows]
