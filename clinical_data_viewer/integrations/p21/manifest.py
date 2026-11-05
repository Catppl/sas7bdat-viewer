from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import PROTOCOL_VERSION


class ManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class P21SidecarManifest:
    path: Path
    executable: Path
    arguments: tuple[str, ...]
    protocol_version: int
    sha256: str

    @classmethod
    def read(cls, path: Path | str) -> P21SidecarManifest:
        manifest_path = Path(path).resolve()
        try:
            value: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ManifestError(
                f"Cannot read P21 side-car manifest: {error}"
            ) from error
        if not isinstance(value, dict):
            raise ManifestError("P21 side-car manifest root must be an object.")
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise ManifestError("P21 side-car protocol version is incompatible.")
        raw_executable = value.get("executable")
        raw_hash = value.get("sha256")
        raw_arguments = value.get("arguments", [])
        if not isinstance(raw_executable, str) or not raw_executable.strip():
            raise ManifestError("P21 side-car executable is missing.")
        if not isinstance(raw_hash, str) or len(raw_hash) != 64:
            raise ManifestError("P21 side-car SHA-256 is invalid.")
        if not isinstance(raw_arguments, list) or not all(
            isinstance(item, str) for item in raw_arguments
        ):
            raise ManifestError("P21 side-car arguments must be an array of strings.")
        executable = (manifest_path.parent / raw_executable).resolve()
        try:
            executable.relative_to(manifest_path.parent)
        except ValueError as error:
            raise ManifestError(
                "P21 side-car executable must stay beside the manifest."
            ) from error
        if not executable.is_file():
            raise ManifestError("P21 side-car executable does not exist.")
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        if digest.casefold() != raw_hash.casefold():
            raise ManifestError("P21 side-car executable failed integrity validation.")
        return cls(
            manifest_path,
            executable,
            tuple(raw_arguments),
            PROTOCOL_VERSION,
            digest,
        )


def discover_sidecar_manifest(root: Path) -> P21SidecarManifest | None:
    path = root / "p21-sidecar-manifest.json"
    try:
        return P21SidecarManifest.read(path)
    except ManifestError:
        return None
