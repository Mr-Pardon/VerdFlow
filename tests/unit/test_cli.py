from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "cli" / "src"),
]

from verdflow.cli import main  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class CliTests(unittest.TestCase):
    def test_project_and_source_commands_emit_stable_json(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            project = root / "cli.verdflow"
            source = root / "scene.fbx"
            source.write_bytes(b"fixture")

            created = self._run("project", "create", str(project), "--name", "CLI")
            added = self._run("source", "add", str(project), str(source))
            info = self._run("project", "info", str(project))

            self.assertTrue(created["ok"])
            self.assertTrue(added["ok"])
            self.assertEqual(added["result"]["kind"], "fbx")
            self.assertEqual(info["result"]["name"], "CLI")

    def test_missing_source_is_a_structured_error(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            project = root / "cli.verdflow"
            self._run("project", "create", str(project), "--name", "CLI")

            result = self._run(
                "source",
                "add",
                str(project),
                str(root / "missing.fbx"),
                expected_exit=1,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["type"], "FileNotFoundError")

    def test_plant_run_accepts_profile_id_as_complete_identity(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            result = self._run(
                "plant",
                "run",
                str(root / "project.verdflow"),
                str(root / "output.xml"),
                "--library",
                str(root / "library"),
                "--scene-revision",
                "scene_1",
                "--region-set",
                "regions_1",
                "--region",
                "region_1",
                "--strategy-package-hash",
                "sha256:strategy",
                "--seed",
                "7",
                "--target",
                "dts",
                "--catalog-id",
                "catalog_1",
                "--profile-id",
                "profile_1",
                expected_exit=1,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "ProjectNotFoundError")

    def _run(self, *arguments: str, expected_exit: int = 0) -> dict[str, object]:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(arguments)
        self.assertEqual(exit_code, expected_exit)
        return json.loads(output.getvalue())


if __name__ == "__main__":
    unittest.main()
