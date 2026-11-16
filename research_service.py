"""Consultas históricas de investigación, separadas de telemetría y reglas clínicas."""
from contextlib import contextmanager
from datetime import date
import sqlite3

from persistence.contracts import RepositoryUnavailable
from persistence.provider import get_repository
from persistence.sqlite import open_connection

# Prefijos verificados en dataset_sources y source_file del archivo proporcionado.
DATASETS = {"CRF": ("CRF_", "vital_signs", "hydration"), "BRSMAct": ("BRSMACT_", "sleep"),
            "MR010G": ("MR010G_", "lab_results"), "MR016G": ("MR016G_", "nutrition")}
METRICS = {
    "vital_signs": {"heart_rate_bpm": ("Frecuencia cardíaca", "bpm"), "systolic_bp_mmhg": ("Presión sistólica", "mmHg"),
                    "diastolic_bp_mmhg": ("Presión diastólica", "mmHg"), "respiratory_rate_rpm": ("Frecuencia respiratoria", "rpm"),
                    "body_temperature_c": ("Temperatura", "°C"), "weight_kg": ("Peso", "kg")},
    "sleep": {"sleep_duration_hours": ("Duración del sueño", "h"), "sleep_latency_hours": ("Latencia del sueño", "h"),
              "sleep_efficiency_percent": ("Eficiencia del sueño", "%")},
    "nutrition": {key: (label, unit) for key, label, unit in (
        ("energy_kcal", "Energía", "kcal"), ("protein_g", "Proteínas", "g"), ("carbohydrates_g", "Carbohidratos", "g"),
        ("fat_g", "Grasas", "g"), ("saturated_fat_g", "Grasas saturadas", "g"), ("fiber_g", "Fibra", "g"),
        ("total_sugars_g", "Azúcares", "g"), ("sodium_mg", "Sodio", "mg"), ("potassium_mg", "Potasio", "mg"),
        ("calcium_mg", "Calcio", "mg"), ("magnesium_mg", "Magnesio", "mg"), ("phosphorus_mg", "Fósforo", "mg"),
        ("iron_mg", "Hierro", "mg"), ("vitamin_d_mcg", "Vitamina D", "µg"), ("vitamin_c_mg", "Vitamina C", "mg"),
        ("vitamin_b12_mcg", "Vitamina B12", "µg"), ("vitamin_a_mcg", "Vitamina A", "µg"),
        ("water_from_diet_g", "Agua de la dieta", "g"), ("omega3_g", "Omega 3", "g"), ("cholesterol_mg", "Colesterol", "mg"))},
    "hydration": {"fluid_intake_g": ("Ingesta de líquidos", "g"), "goal_g": ("Objetivo registrado en el estudio", "g"),
                  "difference_g": ("Diferencia registrada", "g")},
}
ENTITIES = (*METRICS, "lab_results")
VIEWS = {"vital_signs": "v_vital_daily", "sleep": "v_sleep_daily", "nutrition": "v_nutrition_daily",
         "hydration": "v_hydration_daily", "lab_results": "v_lab_daily"}
DATA_NOTICE = "Investigación histórica de estudios análogos; no es telemetría actual, diagnóstico ni datos del Roadtrip."


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def source_code_sql(alias="r"):
    return f"""CASE WHEN upper({alias}.source_file) LIKE 'CRF!_%' ESCAPE '!' OR {alias}.source_file IN ('vital_signs','hydration') THEN 'CRF'
        WHEN upper({alias}.source_file) LIKE 'BRSMACT!_%' ESCAPE '!' OR {alias}.source_file='sleep' THEN 'BRSMAct'
        WHEN upper({alias}.source_file) LIKE 'MR010G!_%' ESCAPE '!' OR {alias}.source_file='lab_results' THEN 'MR010G'
        WHEN upper({alias}.source_file) LIKE 'MR016G!_%' ESCAPE '!' OR {alias}.source_file='nutrition' THEN 'MR016G' END"""


def parse_filters(args):
    allowed = {"dataset", "subject_id", "from", "to", "metric", "page", "page_size", "entity", "flag", "campaign"}
    if set(args) - allowed:
        raise ValueError("Filtros desconocidos")
    result = {key: args.get(key) or None for key in allowed - {"page", "page_size"}}
    if result["dataset"] is not None and result["dataset"] not in DATASETS:
        raise ValueError("Conjunto de investigación desconocido")
    for key in ("from", "to"):
        if result[key] is not None:
            try:
                parsed = date.fromisoformat(result[key])
                if parsed.isoformat() != result[key]:
                    raise ValueError()
            except ValueError:
                raise ValueError("Las fechas de estudio deben usar YYYY-MM-DD") from None
    if result["from"] and result["to"] and result["from"] > result["to"]:
        raise ValueError("La fecha inicial debe ser anterior o igual a la final")
    for key, default, maximum in (("page", 1, 1_000_000), ("page_size", 50, 200)):
        try:
            value = int(args.get(key, default))
        except (ValueError, TypeError):
            raise ValueError(f"{key} debe ser entero") from None
        if not 1 <= value <= maximum:
            raise ValueError(f"{key} debe estar entre 1 y {maximum}")
        result[key] = value
    if result["entity"] and result["entity"] not in ENTITIES:
        raise ValueError("Familia de indicadores desconocida")
    return result


@contextmanager
def connection():
    path = getattr(get_repository(), "path", None)
    if path is None:
        raise RepositoryUnavailable("Investigación NASA requiere el adaptador SQLite integrado")
    try:
        with open_connection(path) as database:
            database.execute("PRAGMA query_only=ON")
            database.execute("BEGIN")
            names = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"subjects", "dataset_sources", "quality_flags", *ENTITIES}.issubset(names):
                raise RepositoryUnavailable("Datos de investigación aún no integrados en esta base SQLite")
            yield ResearchService(database)
    except sqlite3.OperationalError as error:
        raise RepositoryUnavailable("No se pudo consultar la información de investigación") from error


class ResearchService:
    def __init__(self, database):
        self.db = database

    def where(self, filters, *, alias="r", dated="date", dataset=True):
        clauses, params = ["1=1"], []
        for key, expression in (("subject_id", f"{alias}.subject_id"), ("from", f"{alias}.{dated}"), ("to", f"{alias}.{dated}")):
            if filters.get(key):
                op = ">=" if key == "from" else "<=" if key == "to" else "="
                clauses.append(f"{expression}{op}?")
                params.append(filters[key])
        if dataset and filters.get("dataset"):
            clauses.append(f"({source_code_sql(alias)})=?")
            params.append(filters["dataset"])
        return " AND ".join(clauses), params

    def page(self, select, count, params, filters):
        total = self.db.execute(count, params).fetchone()[0]
        rows = self.db.execute(select + " LIMIT ? OFFSET ?", [*params, filters["page_size"], (filters["page"] - 1) * filters["page_size"]]).fetchall()
        return {"items": [dict(row) for row in rows], "page": filters["page"], "page_size": filters["page_size"],
                "total": total, "pages": (total + filters["page_size"] - 1) // filters["page_size"],
                "data_class": "historical_research", "notice": DATA_NOTICE, "time_basis": "study_date_without_timezone"}

    def require_subject(self, filters):
        if filters.get("subject_id") and not self.db.execute("SELECT 1 FROM subjects WHERE subject_id=?", (filters["subject_id"],)).fetchone():
            raise LookupError("Participante de investigación no encontrado")

    def datasets(self, filters):
        where, params = "1=1", []
        if filters.get("dataset"):
            where, params = "source_code=?", [filters["dataset"]]
        result = self.page(f"SELECT * FROM dataset_sources WHERE {where} ORDER BY source_code",
                           f"SELECT COUNT(*) FROM dataset_sources WHERE {where}", params, filters)
        for item in result["items"]:
            item["entities"] = list(DATASETS.get(item["source_code"], (None,))[1:])
        return result

    def subjects(self, filters):
        union = " UNION ALL ".join(f"SELECT subject_id,date,source_file FROM {table}" for table in ENTITIES)
        where, params = self.where(filters, alias="r")
        clauses = ["1=1"]
        if any(filters.get(key) for key in ("dataset", "from", "to")):
            clauses.append(f"EXISTS(SELECT 1 FROM ({union}) r WHERE r.subject_id=s.subject_id AND {where})")
        else:
            params = []
            if filters.get("subject_id"):
                clauses.append("s.subject_id=?")
                params.append(filters["subject_id"])
        if filters.get("campaign"):
            clauses.append("s.campaign=?")
            params.append(filters["campaign"])
        condition = " AND ".join(clauses)
        return self.page(f"SELECT s.* FROM subjects s WHERE {condition} ORDER BY s.campaign,s.subject_id",
                         f"SELECT COUNT(*) FROM subjects s WHERE {condition}", params, filters)

    def metrics(self, entity):
        if entity == "lab_results":
            return {row["analyte"]: (row["analyte"], row["unit"]) for row in self.db.execute("SELECT DISTINCT analyte,unit FROM lab_results ORDER BY analyte")}
        return METRICS[entity]

    def records(self, entity, filters):
        self.require_subject(filters)
        where, params = self.where(filters)
        metrics = self.metrics(entity)
        metric = filters.get("metric")
        if metric and metric not in metrics:
            raise ValueError("Indicador desconocido para esta familia de datos")
        if metric and entity == "lab_results":
            where += " AND r.analyte=?"
            params.append(metric)
        result = self.page(f"SELECT r.*,({source_code_sql()}) AS dataset FROM {entity} r WHERE {where} ORDER BY r.date IS NULL,r.date,r.id",
                           f"SELECT COUNT(*) FROM {entity} r WHERE {where}", params, filters)
        # Los originales conservan NULL y valores señalados; nunca se rellenan con cero.
        for row in result["items"]:
            flags = self.db.execute("""SELECT id,flag,field_name,notes FROM quality_flags
                WHERE (source_file=? AND source_row=?) OR
                    (source_file=? AND subject_id=? AND source_date IS ?) ORDER BY id""",
                (row["source_file"], row["source_row"], entity, row["subject_id"], row["date"])).fetchall()
            row["quality_flags"] = [dict(flag) for flag in flags]
        result["metric"] = metric
        result["units"] = {key: value[1] for key, value in metrics.items()}
        return result

    def quality(self, filters):
        self.require_subject(filters)
        where, params = self.where(filters, dated="source_date")
        for key, column in (("metric", "field_name"), ("flag", "flag")):
            if filters.get(key):
                where += f" AND r.{column}=?"
                params.append(filters[key])
        return self.page(f"SELECT r.*,({source_code_sql()}) AS dataset FROM quality_flags r WHERE {where} ORDER BY r.source_date IS NULL,r.source_date,r.id",
                         f"SELECT COUNT(*) FROM quality_flags r WHERE {where}", params, filters)

    def summary(self, filters):
        self.require_subject(filters)
        output = {"metrics": [], "records": {}, "data_class": "historical_research", "notice": DATA_NOTICE,
                  "unavailable": {"spo2": "Estos conjuntos no contienen mediciones de SpO₂"}}
        for entity in ENTITIES:
            where, params = self.where(filters)
            if entity == "lab_results":
                rows = self.db.execute(f"SELECT analyte AS metric,unit,COUNT(value) AS available,COUNT(*) AS records,COUNT(date) AS dated_records FROM lab_results r WHERE {where} GROUP BY analyte,unit ORDER BY analyte", params).fetchall()
                output["records"][entity] = sum(row["records"] for row in rows)
                for row in rows:
                    output["metrics"].append({**dict(row), "label": row["metric"], "entity": entity})
            else:
                metrics = self.metrics(entity)
                counts = self.db.execute(f"SELECT COUNT(*) AS records,COUNT(date) AS dated_records,{', '.join('COUNT('+identifier(key)+') AS '+identifier(key) for key in metrics)} FROM {entity} r WHERE {where}", params).fetchone()
                output["records"][entity] = counts["records"]
                for key, (label, unit) in metrics.items():
                    output["metrics"].append({"entity": entity, "metric": key, "label": label, "unit": unit,
                                              "available": counts[key], "records": counts["records"], "dated_records": counts["dated_records"]})
        where, params = self.where(filters, dated="source_date")
        output["quality"] = [dict(row) for row in self.db.execute(f"SELECT flag,COUNT(*) AS count FROM quality_flags r WHERE {where} GROUP BY flag ORDER BY count DESC,flag", params)]
        output["quality_count"] = sum(row["count"] for row in output["quality"])
        return output

    def series(self, filters):
        self.require_subject(filters)
        entity, metric = filters.get("entity"), filters.get("metric")
        if not entity or not metric or metric not in self.metrics(entity):
            raise ValueError("Selecciona entity y metric válidos para la serie histórica")
        if not filters.get("subject_id"):
            raise ValueError("Selecciona un participante; no se combinan sujetos en una curva")
        source = next(key for key, value in DATASETS.items() if entity in value[1:])
        if filters.get("dataset") and filters["dataset"] != source:
            return {"items": [], "total": 0, "page": filters["page"], "page_size": filters["page_size"], "pages": 0,
                    "entity": entity, "metric": metric, "unit": self.metrics(entity)[metric][1], "data_class": "historical_research"}
        where, params = self.where(filters, dataset=False)
        view = VIEWS[entity]
        fields = {row["name"] for row in self.db.execute(f"PRAGMA table_info({view})")}
        if metric in fields:
            query = f"SELECT r.subject_id,r.date,r.{identifier(metric)} AS value FROM {view} r WHERE {where}"
        else:
            query = f"SELECT r.subject_id,r.date,CASE WHEN COUNT(DISTINCT value)>1 THEN NULL ELSE MAX(value) END AS value FROM lab_results r WHERE {where} AND analyte=? GROUP BY r.subject_id,r.date"
            params.append(metric)
        result = self.page(f"SELECT * FROM ({query}) ORDER BY date", f"SELECT COUNT(*) FROM ({query})", params, filters)
        # Asocia banderas de origen y las agregadas por día sin descartar los puntos señalados.
        for point in result["items"]:
            flags = self.db.execute(f"""SELECT DISTINCT q.flag FROM quality_flags q WHERE q.subject_id=? AND q.source_date=?
                AND ({source_code_sql('q')})=? AND (q.source_file=? OR EXISTS
                    (SELECT 1 FROM {entity} raw WHERE raw.subject_id=? AND raw.date=? AND raw.source_file=q.source_file AND raw.source_row=q.source_row))""",
                (point["subject_id"], point["date"], source, entity, point["subject_id"], point["date"])).fetchall()
            point["quality_flags"] = [row[0] for row in flags]
        result.update(entity=entity, metric=metric, unit=self.metrics(entity)[metric][1],
                      aggregation="Vista diaria del proveedor: conflictos conservados como NULL; consultar originales",
                      missing_date_note="Los registros sin fecha se consultan en tablas; no se colocan en la curva")
        return result
