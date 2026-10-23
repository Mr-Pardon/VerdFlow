from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.portable_licenses import PROJECT_FILES, stage_licenses, verify_licenses


class PortableLicenseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "source"
        self.bundle = self.root / "bundle"
        for name in PROJECT_FILES:
            self.write(self.workspace / name, "project " + name)
        self.write(self.workspace / "third-party/runtime-ui.txt", "curated UI notices")
        self.write(self.bundle / "LICENSE", "original Electron license")
        self.write(self.bundle / "LICENSES.chromium.html", "original Chromium notices")
        self.write(self.bundle / "version", "40.0.0")
        self.write(self.bundle / "resources/python/LICENSE.txt", "original Python license")
        for app in ("studio", "dts-live-webui"):
            self.write(self.workspace / "apps" / app / "package.json", json.dumps({"dependencies": {"fixture-ui": "1.0.0"}}))
        self.js_package = self.workspace / "node_modules/fixture-ui"
        self.write(self.js_package / "package.json", '{"name":"fixture-ui","version":"1.0.0","license":"MIT"}')
        self.write(self.js_package / "LICENSE", "original UI license")
        self.site = self.bundle / "resources/python/Lib/site-packages"
        self.distribution = self.site / "fixture-1.0.dist-info"
        self.write(self.distribution / "METADATA", "Name: fixture\nVersion: 1.0\nLicense-Expression: BSD-3-Clause\n")
        self.write(self.distribution / "licenses/LICENSE", "original wheel license")
        self.write(self.site / "fixture/native/NOTICE.txt", "original native library notice")
        self.write_record(["fixture-1.0.dist-info/licenses/LICENSE", "fixture/native/NOTICE.txt"])

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_record(self, names):
        with (self.distribution / "RECORD").open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows((name, "", "") for name in names)

    def stage(self):
        return stage_licenses(self.workspace, self.bundle, python_version="3.13.3")

    def test_preserves_all_upstream_material_and_separates_project_license(self):
        manifest = self.stage()
        verify_licenses(self.bundle)
        self.assertEqual((self.bundle / "LICENSE").read_text(), "project LICENSE")
        self.assertEqual((self.bundle / "third-party/electron-LICENSE.txt").read_text(), "original Electron license")
        self.assertEqual(len(manifest["components"]), 4)
        self.assertIn("resources/python/Lib/site-packages/fixture/native/NOTICE.txt", manifest["files"])
        self.assertIn("third-party/javascript/fixture-ui-1.0.0/LICENSE", manifest["files"])
        self.assertNotIn(str(self.root), json.dumps(manifest))

    def test_local_publication_overlay_and_standalone_root_both_work(self):
        overlay = self.workspace / "publishing/public"
        for name in (*PROJECT_FILES, "third-party/runtime-ui.txt"):
            destination = overlay / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            (self.workspace / name).rename(destination)
        self.stage()
        verify_licenses(self.bundle)

    def test_changed_notice_fails_verification(self):
        self.stage()
        (self.bundle / "third-party/electron-LICENSE.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            verify_licenses(self.bundle)

    def test_missing_js_license_fails_build(self):
        (self.js_package / "LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "No license material for JavaScript"):
            self.stage()

    def test_missing_upstream_license_uses_only_exact_reviewed_version_and_hash(self):
        (self.js_package / "LICENSE").unlink()
        notice = self.workspace / "third-party/reviewed.txt"
        self.write(notice, "reviewed upstream license")
        overrides = {"fixture-ui@1.0.0": {
            "path": "third-party/reviewed.txt", "sha256": hashlib.sha256(notice.read_bytes()).hexdigest(),
            "source": "https://example.org/upstream/LICENSE",
        }}
        self.write(self.workspace / "third-party/license-overrides.json", json.dumps(overrides))
        manifest = self.stage()
        verify_licenses(self.bundle)
        entry = next(item for item in manifest["components"] if item["name"] == "fixture-ui")
        self.assertEqual(entry["license_source"], overrides["fixture-ui@1.0.0"]["source"])

    def test_missing_wheel_native_notice_fails_build(self):
        (self.site / "fixture/native/NOTICE.txt").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or unsafe"):
            self.stage()

    def test_record_cannot_include_files_outside_runtime(self):
        self.write(self.root / "LICENSE", "outside")
        self.write_record([str(self.root / "LICENSE")])
        with self.assertRaisesRegex(ValueError, "Missing or unsafe"):
            self.stage()

    def test_second_staging_cannot_replace_electron_notice_with_project_license(self):
        self.stage()
        with self.assertRaisesRegex(ValueError, "fresh Electron"):
            self.stage()
