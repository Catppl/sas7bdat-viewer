from __future__ import annotations

from importlib.resources import files

_CHANGE_FROM_BASELINE_PREVIEW = "change_from_baseline_horizontal.png"
_BASELINE_CHARACTERISTICS_PREVIEW = "baseline_characteristics.png"


def load_change_from_baseline_shell_preview() -> bytes:
    """Return the packaged PNG used by the Change from Baseline preview."""

    resource = files("clinical_data_viewer.assets.shells").joinpath(
        _CHANGE_FROM_BASELINE_PREVIEW
    )
    return resource.read_bytes()


def load_baseline_characteristics_shell_preview() -> bytes:
    """Return the packaged PNG used by the Baseline Characteristics preview."""

    resource = files("clinical_data_viewer.assets.shells").joinpath(
        _BASELINE_CHARACTERISTICS_PREVIEW
    )
    payload = resource.read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The packaged Baseline Characteristics preview is not a PNG.")
    return payload


__all__ = [
    "load_baseline_characteristics_shell_preview",
    "load_change_from_baseline_shell_preview",
]
