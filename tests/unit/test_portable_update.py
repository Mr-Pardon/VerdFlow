from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def load_module(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


updater = load_module("portable_update", ROOT / "apps/studio-electron-probe/src/prepare-update.py")
builder = load_module("portable_builder", ROOT / "tools/build_portable_release.py")


class PortableUpdateTests(unittest.TestCase):
    def test_default_build_has_the_release_source_without_waiting_for_a_release(self):
        self.assertEqual(builder.build_parser().parse_args([]).update_repository, "Mr-Pardon/VerdFlow")
        self.assertIsNone(builder.build_parser().parse_args(["--no-updates"]).update_repository)
        self.assertEqual(builder.build_parser().parse_args(["--update-repository", "example/fork"]).update_repository, "example/fork")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="verdflow-update-test-")
        self.root = Path(self.temporary.name).resolve()
        self.install = self.root / "old-app"
        self.install.mkdir()
        (self.install / "original.txt").write_text("original")
        self.stage = self.root / (".verdflow-update-" + "a" * 32)
        self.bundle = self.root / "VerdFlow-Studio-v0.4.0-win-x64"
        for name in ("VerdFlow Studio.exe", "resources/python/python.exe", "resources/studio/index.html", "resources/app/package.json"):
            target = self.bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"test-program-file")
        self.archive = self.root / (self.bundle.name + ".zip")

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self, *, extra=None, damaged=False):
        builder._write_update_manifest(self.bundle, "0.4.0")
        if damaged:
            (self.bundle / "VerdFlow Studio.exe").write_bytes(b"damaged-file")
        builder._write_zip(self.bundle, self.archive)
        if extra:
            with zipfile.ZipFile(self.archive, "a") as archive:
                archive.writestr(extra, b"unexpected")
        return {"id": "a" * 32, "installRoot": str(self.install), "stageRoot": str(self.stage),
                "archive": str(self.archive), "sha256": builder._sha256(self.archive),
                "rootName": self.bundle.name, "targetVersion": "0.4.0"}

    def test_manifest_stages_complete_application_without_touching_original(self):
        plan = self.plan()
        updater.stage_update(plan)
        self.assertEqual((self.install / "original.txt").read_text(), "original")
        self.assertEqual((self.stage / "VerdFlow Studio.exe").read_bytes(), b"test-program-file")
        self.assertEqual(json.loads((self.stage / ".verdflow-update-ready.json").read_text())["version"], "0.4.0")
        builder._write_update_descriptor(self.archive, "0.4.0")
        descriptor = json.loads((self.root / "VerdFlow-update-win-x64.json").read_text())
        self.assertEqual(descriptor["archive_sha256"], plan["sha256"])
        self.assertEqual(descriptor["archive_bytes"], self.archive.stat().st_size)

    def test_bad_archive_checksum_is_rejected(self):
        plan = self.plan()
        plan["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            updater.stage_update(plan)
        self.assertFalse(self.stage.exists())

    def test_damaged_file_removes_only_new_staging_directory(self):
        with self.assertRaisesRegex(ValueError, "size|checksum"):
            updater.stage_update(self.plan(damaged=True))
        self.assertFalse(self.stage.exists())
        self.assertTrue((self.install / "original.txt").exists())

    def test_extra_file_and_path_traversal_are_rejected(self):
        for extra in ("../outside", self.bundle.name + "/unexpected", self.bundle.name + "/RESOURCES/STUDIO/INDEX.HTML"):
            with self.subTest(extra=extra):
                self.archive.unlink(missing_ok=True)
                with self.assertRaises(ValueError):
                    updater.stage_update(self.plan(extra=extra))
                self.assertFalse(self.stage.exists())
        self.assertFalse((self.root / "outside").exists())

    def test_preexisting_or_unrelated_stage_is_never_removed(self):
        plan = self.plan()
        self.stage.mkdir()
        (self.stage / "keep").write_text("keep")
        with self.assertRaises(ValueError):
            updater.stage_update(plan)
        self.assertEqual((self.stage / "keep").read_text(), "keep")
        plan["stageRoot"] = str(self.install)
        with self.assertRaises(ValueError):
            updater.stage_update(plan)
        self.assertTrue((self.install / "original.txt").exists())

    def test_windows_unsafe_paths_are_rejected(self):
        for name in (".", "..", "/absolute", "a/../b", "a\\b", "C:/file", "file:stream", "CON.txt", "a. ", "a//b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                updater.safe_relative(name)


if __name__ == "__main__":
    unittest.main()
