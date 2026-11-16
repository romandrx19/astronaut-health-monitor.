"""Generador de datos ficticios; nunca representa sensores ni pacientes reales."""
import random
from datetime import datetime, timedelta, timezone
from threading import Lock

from persistence.provider import get_repository

simulation_lock = Lock()
SIMULATION_INTERVAL_SECONDS = 5
# Desplazamientos: cada astronauta tiene un perfil sintético ligeramente distinto.
BASELINES = {
    1: dict(heart_rate=72, spo2=98, systolic=116, diastolic=75,
            glucose=94, temperature=36.7, sleep_hours=7.4),
    2: dict(heart_rate=83, spo2=97, systolic=124, diastolic=80,
            glucose=106, temperature=36.9, sleep_hours=6.7),
    3: dict(heart_rate=66, spo2=99, systolic=110, diastolic=72,
            glucose=89, temperature=36.5, sleep_hours=8.0),
}


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def generate_sample(astronaut_id, at_time=None, occasional_alert=True):
    base = BASELINES[astronaut_id]
    rng = random
    reading = {
        "astronaut_id": astronaut_id,
        "source": "simulated",
        "captured_at": (at_time or datetime.now(timezone.utc)).isoformat(timespec="milliseconds"),
        "heart_rate": clamp(base["heart_rate"] + rng.randint(-9, 9), 45, 135),
        "spo2": clamp(base["spo2"] + rng.randint(-2, 1), 89, 100),
        "systolic": clamp(base["systolic"] + rng.randint(-9, 9), 90, 165),
        "diastolic": clamp(base["diastolic"] + rng.randint(-6, 6), 55, 105),
        "glucose": clamp(base["glucose"] + rng.randint(-13, 13), 65, 170),
        "temperature": round(clamp(base["temperature"] + rng.uniform(-0.3, 0.3), 35, 39), 1),
        "sleep_hours": round(clamp(base["sleep_hours"] + rng.uniform(-0.8, 0.8), 3, 10), 1),
    }
    # Anomalías ficticias poco frecuentes para poder probar las alertas.
    if occasional_alert and rng.random() < 0.22:
        key = rng.choice(["heart_rate", "spo2", "glucose", "temperature", "sleep_hours", "systolic"])
        reading[key] = {
            "heart_rate": 115,
            "spo2": 92,
            "glucose": 151,
            "temperature": 38.2,
            "sleep_hours": 4.5,
            "systolic": 150,
        }[key]
    return reading


def seed_if_empty(repository=None):
    with simulation_lock, get_repository(repository).transaction(write=True) as session:
        if session.has_measurements():
            return
        now = datetime.now(timezone.utc)
        items = []
        for index in range(24):
            moment = now - timedelta(minutes=(23 - index) * 5)
            for astronaut_id in BASELINES:
                items.append(generate_sample(astronaut_id, moment, occasional_alert=False))
        session.add_measurements(items)


def simulate_step(repository=None):
    with simulation_lock:
        items = [generate_sample(astronaut_id) for astronaut_id in BASELINES]
        get_repository(repository).add_measurements(items)
        return items


def simulation_status(repository=None):
    state = get_repository(repository).get_simulation_state()
    return {**state, "interval_seconds": SIMULATION_INTERVAL_SECONDS}


def set_simulation_running(running, repository=None):
    if not isinstance(running, bool):
        raise ValueError("running debe ser verdadero o falso")
    with get_repository(repository).transaction(write=True) as session:
        state = session.get_simulation_state()
        state.update(running=running, revision=state["revision"] + 1)
        session.save_simulation_state(state)
        return {**state, "interval_seconds": SIMULATION_INTERVAL_SECONDS}


def simulate_due(*, automatic=False, repository=None, now=None):
    """Una transacción decide el ritmo global antes de insertar el lote completo."""
    now = now or datetime.now(timezone.utc)
    with simulation_lock, get_repository(repository).transaction(write=True) as session:
        state = session.get_simulation_state()
        previous = datetime.fromisoformat(state["last_generated_at"]) if state["last_generated_at"] else None
        due = previous is None or (now - previous).total_seconds() >= SIMULATION_INTERVAL_SECONDS
        samples = []
        if due and (state["running"] or not automatic):
            samples = [generate_sample(astronaut_id, now) for astronaut_id in BASELINES]
            session.add_measurements(samples)
            state.update(last_generated_at=now.isoformat(timespec="microseconds"), revision=state["revision"] + 1)
            session.save_simulation_state(state)
        return {"created": len(samples), "samples": samples,
                "simulation": {**state, "interval_seconds": SIMULATION_INTERVAL_SECONDS}}
