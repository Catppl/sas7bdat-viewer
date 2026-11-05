from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

PROTOCOL_VERSION = 1
VIEWER_EXECUTABLE = "SASDataViewer.exe"
SIDECAR_EXECUTABLE = "P21IssueExplorer.exe"
MANIFEST_NAME = "p21-sidecar-manifest.json"
SIDECAR_DIRECTORY = "p21-sidecar"


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _viewer_internal(bundle: Path) -> Path:
    root = bundle.resolve()
    executable = root / VIEWER_EXECUTABLE
    internal = root / "_internal"
    if not executable.is_file() or not internal.is_dir():
        raise ValueError(
            "Viewer bundle must contain SASDataViewer.exe and an _internal directory."
        )
    return internal


def stage_p21_sidecar(viewer_bundle: Path, sidecar_bundle: Path) -> Path:
    internal = _viewer_internal(viewer_bundle)
    source = sidecar_bundle.resolve()
    source_executable = source / SIDECAR_EXECUTABLE
    source_internal = source / "_internal"
    if not source_executable.is_file() or not source_internal.is_dir():
        raise ValueError(
            "Side-car bundle must contain P21IssueExplorer.exe and an _internal "
            "directory."
        )

    target = internal / SIDECAR_DIRECTORY
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    executable = target / SIDECAR_EXECUTABLE
    manifest = internal / MANIFEST_NAME
    manifest.write_text(
        json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "executable": f"{SIDECAR_DIRECTORY}/{SIDECAR_EXECUTABLE}",
                "arguments": [],
                "sha256": _digest(executable),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_bundle(viewer_bundle: Path, edition: str) -> None:
    internal = _viewer_internal(viewer_bundle)
    manifest = internal / MANIFEST_NAME
    sidecar = internal / SIDECAR_DIRECTORY
    if edition == "base":
        if manifest.exists() or sidecar.exists():
            raise ValueError("Base bundle unexpectedly contains the P21 side-car.")
        return

    if not manifest.is_file():
        raise ValueError("P21 bundle is missing its side-car manifest.")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"P21 side-car manifest is invalid: {error}") from error
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("P21 side-car protocol version is incompatible.")
    if value.get("arguments") != []:
        raise ValueError("P21 side-car manifest arguments must be an empty array.")
    relative = value.get("executable")
    if relative != f"{SIDECAR_DIRECTORY}/{SIDECAR_EXECUTABLE}":
        raise ValueError("P21 side-car executable path is invalid.")
    executable = (internal / relative).resolve()
    try:
        executable.relative_to(internal.resolve())
    except ValueError as error:
        raise ValueError("P21 side-car executable escapes the bundle.") from error
    if not executable.is_file() or not (sidecar / "_internal").is_dir():
        raise ValueError("P21 side-car onedir bundle is incomplete.")
    if value.get("sha256") != _digest(executable):
        raise ValueError("P21 side-car executable failed SHA-256 verification.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage and verify P21 Windows bundles")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage")
    stage.add_argument("--viewer-bundle", required=True, type=Path)
    stage.add_argument("--sidecar-bundle", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--viewer-bundle", required=True, type=Path)
    verify.add_argument("--edition", choices=("base", "p21"), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "stage":
        manifest = stage_p21_sidecar(arguments.viewer_bundle, arguments.sidecar_bundle)
        print(f"P21 manifest: {manifest}")
    else:
        verify_bundle(arguments.viewer_bundle, arguments.edition)
        print(f"Verified {arguments.edition} bundle: {arguments.viewer_bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
