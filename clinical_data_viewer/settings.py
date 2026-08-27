from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_FOLDER = "ClinicalDataViewer"

PROC_MEANS_STATISTICS = (
    ("subjects", "n (Subjects)"),
    ("n", "N (Values)"),
    ("nmiss", "NMISS"),
    ("mean", "Mean"),
    ("std", "Standard Deviation"),
    ("stderr", "Standard Error"),
    ("median", "Median"),
    ("q1", "Q1"),
    ("q3", "Q3"),
    ("min", "Minimum"),
    ("max", "Maximum"),
    ("lclm", "CI Lower"),
    ("uclm", "CI Upper"),
)
DEFAULT_PROC_MEANS_STATISTICS = [key for key, _label in PROC_MEANS_STATISTICS]
PROC_MEANS_COUNT_STATISTICS = {"subjects", "n", "nmiss"}
PROC_MEANS_DECIMAL_STATISTICS = tuple(
    key
    for key, _label in PROC_MEANS_STATISTICS
    if key not in PROC_MEANS_COUNT_STATISTICS
)
DEFAULT_PROC_MEANS_DECIMAL_PLACES = {key: 2 for key in PROC_MEANS_DECIMAL_STATISTICS}
DEFAULT_PROC_MEANS_DECIMAL_OFFSETS = {
    "mean": 1,
    "std": 2,
    "stderr": 2,
    "median": 1,
    "q1": 1,
    "q3": 1,
    "min": 0,
    "max": 0,
    "lclm": 1,
    "uclm": 1,
}


def application_data_directory() -> Path:
    """Return a per-user writable directory without importing Qt."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys_platform() == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / APP_FOLDER


def temporary_root_directory() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / APP_FOLDER / "temp"
    return Path(tempfile.gettempdir()) / APP_FOLDER / "temp"


def sys_platform() -> str:
    import sys

    return sys.platform


@dataclass(slots=True)
class AppSettings:
    page_size: int = 500
    history_limit: int = 500
    temp_max_age_hours: int = 24
    last_open_directory: str = ""
    last_export_directory: str = ""
    apply_sas_date_time_formats: bool = False
    proc_means_decimals: int = 2
    proc_means_decimal_places: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_PROC_MEANS_DECIMAL_PLACES)
    )
    proc_means_decimal_offsets: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_PROC_MEANS_DECIMAL_OFFSETS)
    )
    proc_means_confidence: float = 0.95
    proc_means_statistics: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROC_MEANS_STATISTICS)
    )

    @classmethod
    def load(cls, path: Path | None = None) -> AppSettings:
        settings_path = path or application_data_directory() / "settings.json"
        try:
            raw: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return cls()
        known = {key: raw[key] for key in asdict(cls()) if key in raw}
        value = cls(**known)
        value.page_size = min(5_000, max(50, int(value.page_size)))
        value.history_limit = min(10_000, max(10, int(value.history_limit)))
        value.temp_max_age_hours = min(720, max(1, int(value.temp_max_age_hours)))
        value.apply_sas_date_time_formats = bool(value.apply_sas_date_time_formats)
        value.proc_means_decimals = min(10, max(0, int(value.proc_means_decimals)))
        raw_decimal_places = raw.get("proc_means_decimal_places", {})
        if not isinstance(raw_decimal_places, dict):
            raw_decimal_places = {}
        decimal_places: dict[str, int] = {}
        for key in PROC_MEANS_DECIMAL_STATISTICS:
            try:
                decimals = int(raw_decimal_places.get(key, value.proc_means_decimals))
            except (TypeError, ValueError):
                decimals = value.proc_means_decimals
            decimal_places[key] = min(10, max(0, decimals))
        value.proc_means_decimal_places = decimal_places
        raw_decimal_offsets = raw.get("proc_means_decimal_offsets", {})
        if not isinstance(raw_decimal_offsets, dict):
            raw_decimal_offsets = {}
        decimal_offsets: dict[str, int] = {}
        for key in PROC_MEANS_DECIMAL_STATISTICS:
            try:
                offset = int(
                    raw_decimal_offsets.get(
                        key, DEFAULT_PROC_MEANS_DECIMAL_OFFSETS[key]
                    )
                )
            except (TypeError, ValueError):
                offset = DEFAULT_PROC_MEANS_DECIMAL_OFFSETS[key]
            decimal_offsets[key] = min(4, max(0, offset))
        value.proc_means_decimal_offsets = decimal_offsets
        value.proc_means_confidence = min(
            0.999, max(0.5, float(value.proc_means_confidence))
        )
        allowed_statistics = {key for key, _label in PROC_MEANS_STATISTICS}
        value.proc_means_statistics = [
            key for key in value.proc_means_statistics if key in allowed_statistics
        ] or list(DEFAULT_PROC_MEANS_STATISTICS)
        return value

    def save(self, path: Path | None = None) -> None:
        settings_path = path or application_data_directory() / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(settings_path)
