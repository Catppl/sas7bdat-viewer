from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .configuration import write_ae_table_configuration
from .models import AeTableConfig
from .result_store import AeTableResultWriter

TOTAL_KEY = "__ae_total__"


def _missing(value, variable):
    return value is None or (variable.kind == "character" and (value == "" or value is None))


def _canon(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _key(value):
    return json.dumps(_canon(value), ensure_ascii=False, separators=(",", ":"), default=str)


def _display(value):
    return "(Missing)" if value is None else str(value)


def _missing_sql(variable: VariableMetadata) -> str:
    col = quote_identifier(variable.name)
    return f"({col} IS NULL OR {col} = '')" if variable.kind == "character" else f"{col} IS NULL"


def _and(*parts):
    active = [(sql, params) for sql, params in parts if sql]
    return (" AND ".join(f"({sql})" for sql, _ in active), tuple(v for _, p in active for v in p)) if active else ("", ())


class MissingTreatmentError(ValueError):
    pass


class AeTableEngine:
    def __init__(self, temp_manager: TempManager):
        self.temp_manager = temp_manager

    @staticmethod
    def _fields(metadata):
        return {v.name.casefold(): v for v in metadata.variables}

    @staticmethod
    def _connection(handle):
        return sqlite3.connect(handle.database_path.resolve().as_uri() + "?mode=ro", uri=True)

    def resolve_treatment_levels(self, source, config, population=None):
        config.validate(source.metadata, population.metadata if population else None)
        treatment = self._fields(source.metadata)[config.treatment_variable.casefold()]
        handles = [(source, config.dataset_filter.sql, config.dataset_filter.parameters, treatment)]
        if config.denominator.type == "population" and population is not None:
            pt = self._fields(population.metadata)[config.treatment_variable.casefold()]
            handles.append((population, config.denominator.population_filter.sql, config.denominator.population_filter.parameters, pt))
        values = []
        seen = set()
        for handle, sql, params, variable in handles:
            where = f" WHERE {sql}" if sql else ""
            with closing(self._connection(handle)) as conn:
                for (value,) in conn.execute(f"SELECT DISTINCT {quote_identifier(variable.name)} FROM dataset{where}", params):
                    value = _canon(value)
                    if _missing(value, variable):
                        raise MissingTreatmentError("Missing treatment values were found. Modify the Dataset Filter or Population WHERE before running.")
                    key = _key(value)
                    if key not in seen:
                        seen.add(key); values.append((key, value, _display(value)))
        values.sort(key=lambda item: str(item[2]).casefold())
        return values

    def _aggregate(self, handle, config_filter, soc, pt, treatment, subject):
        where = f" WHERE {config_filter.sql}" if config_filter.sql else ""
        sql = (f"SELECT {quote_identifier(treatment.name)}, {quote_identifier(soc.name)}, {quote_identifier(pt.name)}, {quote_identifier(subject.name)} "
               f"FROM dataset{where}")
        any_counts = defaultdict(set); soc_counts = defaultdict(set); pt_counts = defaultdict(set)
        with closing(self._connection(handle)) as conn:
            for treatment_value, soc_value, pt_value, subject_value in conn.execute(sql, config_filter.parameters):
                treatment_value, soc_value, pt_value = map(_canon, (treatment_value, soc_value, pt_value))
                if _missing(treatment_value, treatment):
                    raise MissingTreatmentError("Missing treatment values were found. Modify the Dataset Filter before running.")
                if _missing(subject_value, subject):
                    continue
                tkey = _key(treatment_value)
                any_counts[tkey].add(subject_value); any_counts[TOTAL_KEY].add(subject_value)
                if _missing(soc_value, soc):
                    continue
                skey = _key(soc_value)
                soc_counts[(skey, tkey)].add(subject_value); soc_counts[(skey, TOTAL_KEY)].add(subject_value)
                if _missing(pt_value, pt):
                    continue
                pt_counts[(skey, _key(pt_value), tkey)].add(subject_value); pt_counts[(skey, _key(pt_value), TOTAL_KEY)].add(subject_value)
        return any_counts, soc_counts, pt_counts

    def _denom(self, source, population, config, treatment, subject):
        if config.denominator.type == "population":
            handle, compiled = population, config.denominator.population_filter
            treatment = self._fields(population.metadata)[config.treatment_variable.casefold()]
            subject = self._fields(population.metadata)[config.subject_id_variable.casefold()]
        else:
            handle, compiled = source, config.dataset_filter
        where = f" WHERE {compiled.sql}" if compiled.sql else ""
        counts = defaultdict(set)
        with closing(self._connection(handle)) as conn:
            for value, sid in conn.execute(f"SELECT {quote_identifier(treatment.name)}, {quote_identifier(subject.name)} FROM dataset{where}", compiled.parameters):
                value = _canon(value)
                if _missing(value, treatment):
                    raise MissingTreatmentError("Missing treatment values were found in denominator data.")
                if _missing(sid, subject):
                    continue
                counts[_key(value)].add(sid); counts[TOTAL_KEY].add(sid)
        return {k: len(v) for k, v in counts.items()}

    def run(self, source: DatasetHandle, config: AeTableConfig, population=None, progress=None):
        if not source.cache_complete or (population is not None and not population.cache_complete):
            raise ValueError("All AE source datasets must finish loading first.")
        config.validate(source.metadata, population.metadata if population else None)
        notify = progress or (lambda _msg: None)
        fields = self._fields(source.metadata)
        soc, pt = fields[config.soc_variable.casefold()], fields[config.pt_variable.casefold()]
        treatment, subject = fields[config.treatment_variable.casefold()], fields[config.subject_id_variable.casefold()]
        notify("Resolving treatment levels…")
        levels = self.resolve_treatment_levels(source, config, population)
        any_counts, soc_counts, pt_counts = self._aggregate(source, config.dataset_filter, soc, pt, treatment, subject)
        denom = self._denom(source, population, config, treatment, subject)
        # Derive labels directly from observed values (JSON keys preserve type).
        soc_labels = {skey: _display(json.loads(skey)) for skey, _t in soc_counts}
        pt_labels = {(skey, pkey): _display(json.loads(pkey)) for skey, pkey, _t in pt_counts}
        soc_order = sorted(soc_labels, key=lambda s: (-len(soc_counts.get((s, TOTAL_KEY), ())), soc_labels[s].casefold()))
        pt_order = {s: sorted([p for ss, p in pt_labels if ss == s], key=lambda p: (-len(pt_counts.get((s, p, TOTAL_KEY), ())), pt_labels[(s, p)].casefold())) for s in soc_order}
        rows = []
        if config.include_any_ae: rows.append({"row_type": "any", "soc": None, "pt": None, "item": config.any_ae_label, "indent": 0})
        for s in soc_order:
            rows.append({"row_type": "soc", "soc": json.loads(s), "pt": None, "item": soc_labels[s], "indent": 0})
            for p in pt_order[s]: rows.append({"row_type": "pt", "soc": json.loads(s), "pt": json.loads(p), "item": pt_labels[(s, p)], "indent": 1})
        directory = self.temp_manager.create_dataset_directory()
        writer = AeTableResultWriter(directory / "dataset.sqlite", levels, config)
        try:
            for row in rows:
                if row["row_type"] == "any": counts = any_counts
                elif row["row_type"] == "soc": counts = {(t): vals for (s, t), vals in soc_counts.items() if s == _key(row["soc"])}
                else: counts = {(t): vals for (s, p, t), vals in pt_counts.items() if s == _key(row["soc"]) and p == _key(row["pt"])}
                writer.add_row(row, counts, denom, levels, config.dataset_filter_text, config.denominator.type)
            path = directory / "ae_table_config.json"
            write_ae_table_configuration(path, source, config, population, levels, rows)
            return replace(writer.finish(directory, source), configuration_path=path)
        except BaseException:
            writer.abort(self.temp_manager, directory); raise
