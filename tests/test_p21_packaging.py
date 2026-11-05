from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "p21_bundle.py"
SPEC = importlib.util.spec_from_file_location("p21_bundle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
p21_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p21_bundle)


class P21PackagingTests(unittest.TestCase):
    def _bundles(self, root: Path) -> tuple[Path, Path]:
        viewer = root / "SASDataViewer"
        (viewer / "_internal").mkdir(parents=True)
        (viewer / "SASDataViewer.exe").write_bytes(b"viewer")
        sidecar = root / "P21IssueExplorer"
        (sidecar / "_internal").mkdir(parents=True)
        (sidecar / "P21IssueExplorer.exe").write_bytes(b"sidecar-v1")
        (sidecar / "_internal" / "runtime.dll").write_bytes(b"runtime")
        return viewer, sidecar

    def test_base_bundle_has_no_sidecar_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            viewer, _sidecar = self._bundles(Path(temporary))
            p21_bundle.verify_bundle(viewer, "base")

    def test_stage_creates_complete_hash_verified_p21_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            viewer, sidecar = self._bundles(Path(temporary))
            manifest = p21_bundle.stage_p21_sidecar(viewer, sidecar)
            p21_bundle.verify_bundle(viewer, "p21")
            value = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(value["protocol_version"], 1)
            self.assertEqual(value["executable"], "p21-sidecar/P21IssueExplorer.exe")
            self.assertEqual(len(value["sha256"]), 64)
            self.assertTrue(
                (viewer / "_internal" / "p21-sidecar" / "_internal").is_dir()
            )

    def test_modified_sidecar_fails_bundle_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            viewer, sidecar = self._bundles(Path(temporary))
            p21_bundle.stage_p21_sidecar(viewer, sidecar)
            packaged = viewer / "_internal" / "p21-sidecar" / "P21IssueExplorer.exe"
            packaged.write_bytes(b"damaged")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                p21_bundle.verify_bundle(viewer, "p21")


if __name__ == "__main__":
    unittest.main()
