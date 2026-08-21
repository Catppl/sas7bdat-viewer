from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

APP_FOLDER = "ClinicalDataViewer"


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
        return value

    def save(self, path: Path | None = None) -> None:
        settings_path = path or application_data_directory() / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(settings_path)
