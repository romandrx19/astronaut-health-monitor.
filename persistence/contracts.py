"""Datos intercambiados entre servicios y adaptadores. Compatible con Python 3.10+."""
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, TypedDict, runtime_checkable

from devices.normalizer import NormalizedReading

MeasurementId = int | str
SchemaScope = Literal["all", "telemetry", "medical", "events", "oct"]
RuleScope = Literal["educational", "safety_protocol", "oct_review"]
ValidationStatus = Literal["demonstration", "unvalidated", "validated"]
Document = dict[str, Any]


class DuplicateRequest(ValueError):
    """Una clave de reintento se reutilizó para una lectura diferente."""


class OctConflict(ValueError):
    """Conflicto entre una solicitud OCT y los datos ya guardados."""


class RepositoryUnavailable(RuntimeError):
    """El adaptador no puede completar una operación de almacenamiento."""


class Astronaut(TypedDict):
    id: int
    name: str
    role: str
    mission: str


class ReferenceBounds(TypedDict):
    min_value: float
    max_value: float


class MedicalProfile(TypedDict):
    astronaut_id: int
    notes: str
    updated_at: str | None
    references: dict[str, ReferenceBounds]


class MeasurementBase(TypedDict):
    id: MeasurementId
    astronaut_id: int
    captured_at: str
    source: str
    heart_rate: int | float | None
    spo2: int | float | None
    systolic: int | float | None
    diastolic: int | float | None
    glucose: int | float | None
    temperature: int | float | None
    sleep_hours: int | float | None


class Measurement(MeasurementBase, total=False):
    reading_id: int
    acquired_at: str
    received_at: str | None
    device_id: str | None
    provenance: str
    observations: str
    quality: dict[str, str]
    units: dict[str, str]
    request_id: str


class ScientificSource(TypedDict):
    title: str
    reference: str
    url: str | None


class RuleBase(TypedDict):
    id: str
    scope: RuleScope
    metric: str
    unit: str
    condition: Document
    source: ScientificSource
    version: str
    validation_status: ValidationStatus
    message: str
    guidance: list[str]
    enabled: bool


class RuleDefinition(RuleBase, total=False):
    approved_by: str
    validation_reference: str
    context: dict[str, str]


def canonical_source(source):
    if source in ("simulated", "simulation"):
        return "simulation"
    return source if source in ("manual", "smartwatch") else "unknown"


class StoredEvent(TypedDict):
    id: int
    astronaut_id: int
    metric: str
    event_type: str
    severity: str
    current_value: float
    reference_min: float | None
    reference_max: float | None
    guidance: list[str]
    sources: list[str]
    source: str
    measurement_source: str
    measurement_id: int | None
    reading_id: int | None
    reading_key: str | None
    captured_at: str
    detected_at: str
    last_seen_at: str
    status: str
    condition_key: str
    occurrences: int
    resolved_at: str | None
    description: str


class OctMeasurement(TypedDict):
    id: int
    exam_id: int
    metric: str
    value: float
    unit: str
    anatomical_region: str
    measurement_method: str


class OctFile(TypedDict):
    id: int
    exam_id: int
    file_path: str
    preview_path: str
    file_type: str
    original_name: str
    checksum: str
    size_bytes: int
    metadata: Document
    uploaded_at: str


class OctExam(TypedDict):
    id: int
    astronaut_id: int
    eye: Literal["OD", "OS"]
    exam_date: str
    mission_phase: Literal["preflight", "inflight", "postflight"]
    device_model: str
    scan_protocol: str
    image_quality: str
    is_baseline: bool
    review_status: str
    reviewed_by: str
    reviewed_at: str | None
    review_notes: str
    notes: str
    provenance: str
    source_document: str
    is_demo: bool
    request_key: str | None
    request_hash: str | None
    created_at: str
    updated_at: str
    measurements: list[OctMeasurement]
    files: list[OctFile]


@runtime_checkable
class PersistenceRepository(Protocol):
    def initialize(self, scope: SchemaScope = "all") -> None:
        """Aplica las migraciones propias del adaptador, preservando registros e IDs."""
        ...

    def transaction(self, *, write: bool = False) -> AbstractContextManager["PersistenceRepository"]:
        """Entrega una sesión con instantánea coherente y commit/rollback atómicos."""
        ...

    def list_astronauts(self) -> list[Astronaut]: ...
    def get_astronaut(self, astronaut_id: int) -> Astronaut | None: ...
    def get_history(self, astronaut_id: int, limit: int = 36, source: str | None = None) -> list[Measurement]: ...
    def has_measurements(self) -> bool: ...
    def get_simulation_state(self) -> Document: ...
    def save_simulation_state(self, state: Document) -> None: ...
    def add_measurements(self, items: list[Document]) -> None: ...
    def add_manual_measurement(self, sample: Document) -> Measurement: ...
    def store_reading(self, reading: NormalizedReading, request_id: str) -> tuple[Measurement, bool]: ...
    def get_medical_profile(self, astronaut_id: int) -> MedicalProfile: ...
    def save_medical_profile(self, astronaut_id: int, notes: str, references: Mapping[str, ReferenceBounds], updated_at: str) -> MedicalProfile: ...
    def get_open_events(self, astronaut_id: int, source: str) -> list[StoredEvent]: ...
    def insert_health_event(self, event: Document) -> int: ...
    def update_health_event(self, event_id: int, astronaut_id: int, changes: Document) -> None: ...
    def list_health_events(self, astronaut_id: int, limit: int = 50, metric: str | None = None, status: str | None = None) -> list[StoredEvent]: ...
    def list_oct_exams(self, astronaut_id: int) -> list[OctExam]: ...
    def get_oct_exam(self, exam_id: int, astronaut_id: int) -> OctExam: ...
    def find_oct_request(self, request_key: str) -> OctExam | None: ...
    def insert_oct_exam(self, exam: Document, measurements: list[Document]) -> int: ...
    def update_oct_exam(self, exam_id: int, astronaut_id: int, changes: Document) -> OctExam: ...
    def add_oct_files(self, exam_id: int, attachments: list[Document]) -> None: ...
    def get_oct_file(self, file_id: int, astronaut_id: int) -> OctFile: ...
    def get_rule_definitions(self) -> list[RuleDefinition]: ...
    def oct_storage_root(self) -> Path: ...
