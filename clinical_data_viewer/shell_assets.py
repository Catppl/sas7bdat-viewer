from __future__ import annotations

from importlib.resources import files

_CHANGE_FROM_BASELINE_PREVIEW = "change_from_baseline_horizontal.png"


def load_change_from_baseline_shell_preview() -> bytes:
    """Return the packaged PNG used by the Change from Baseline preview."""

    resource = files("clinical_data_viewer.assets.shells").joinpath(
        _CHANGE_FROM_BASELINE_PREVIEW
    )
    return resource.read_bytes()


__all__ = ["load_change_from_baseline_shell_preview"]
