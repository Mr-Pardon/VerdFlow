from __future__ import annotations

from pathlib import Path
import copy
import json
import runpy
import re
import subprocess
import shutil
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path)
    for parent in (ROOT / "packages", ROOT / "adapters")
    for path in parent.glob("*/src")
]

from verdflow.authoring_kits import (  # noqa: E402
    available_authoring_kits,
    write_authoring_kit,
)
from verdflow.asset_package import inspect_asset_catalog_package  # noqa: E402
from verdflow.planting import load_catalog_yaml  # noqa: E402
from verdflow.strategy_worker import (  # noqa: E402
    inspect_strategy_package,
    open_strategy_package_archive,
)


class AuthoringKitTests(unittest.TestCase):
    def test_compatibility_check_flags_breaking_minor_updates(self) -> None:
        source = ROOT / "packages/authoring-kits/src/verdflow/authoring_kits/kits/vfs-author/assets"
        checker = runpy.run_path(str(source / "check_compatibility.py"))
        baseline = inspect_strategy_package(source / "strategy-template").strategy
        compatible = copy.deepcopy(baseline)
        compatible["display_name"] = "Updated title"
        compatible["generation_parameters"][0]["max"] += 10
        self.assertEqual(checker["compatible_update_issues"](baseline, compatible), [])
        for change in (
            lambda value: value["roles"].clear(),
            lambda value: value["generation_parameters"].clear(),
            lambda value: value["generation_parameters"][0].update(min=20),
            lambda value: value["roles"][0].update(assignment_policy="single"),
        ):
            updated = copy.deepcopy(baseline)
            change(updated)
            self.assertTrue(checker["compatible_update_issues"](baseline, updated))

    def test_exported_builder_checks_previous_without_running_generator(self) -> None:
        import yaml
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_authoring_kit("vfs-author", root / "kit.zip")
            with zipfile.ZipFile(root / "kit.zip") as archive:
                archive.extractall(root / "kit")
            assets = root / "kit/vfs-author/assets"
            previous = assets / "strategy-template"
            current = root / "current"
            shutil.copytree(previous, current)
            manifest_path = current / "strategy-package.json"
            strategy_path = current / "strategy.yaml"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            strategy = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
            strategy["generation_parameters"] = []
            (current / "generator.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
            for version, succeeds in (("1.1.0", False), ("2.0.0", True)):
                manifest["package_version"] = strategy["strategy_version"] = version
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                strategy_path.write_text(yaml.safe_dump(strategy), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(assets / "build_vfs.py"), str(current),
                     str(root / f"{version}.vfs"), "--previous", str(previous)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                if not succeeds:
                    self.assertIn("major version", result.stderr)

    def test_exports_both_skills_as_deterministic_readable_zip_archives(self) -> None:
        self.assertEqual(available_authoring_kits(), ("vfa-author", "vfs-author"))
        with tempfile.TemporaryDirectory() as temporary:
            for skill_id in available_authoring_kits():
                first = Path(temporary) / f"{skill_id}-first.zip"
                second = Path(temporary) / f"{skill_id}-second.zip"
                first_info = write_authoring_kit(skill_id, first)
                second_info = write_authoring_kit(skill_id, second)

                self.assertEqual(first.read_bytes(), second.read_bytes())
                self.assertEqual(first_info.archive_hash, second_info.archive_hash)
                with zipfile.ZipFile(first, mode="r") as archive:
                    names = archive.namelist()
                    self.assertFalse(any("__pycache__" in name for name in names))
                    self.assertFalse(any(name.endswith(".pyc") for name in names))
                    skill_path = f"{skill_id}/SKILL.md"
                    self.assertIn(skill_path, names)
                    skill = archive.read(skill_path).decode("utf-8")
                    self.assertRegex(
                        skill,
                        rf"(?m)^name: {re.escape(skill_id)}$",
                    )
                    self.assertRegex(skill, r"(?m)^description: .+$")
                    if skill_id == "vfs-author":
                        template = archive.read("vfs-author/assets/strategy-template/strategy.yaml").decode("utf-8")
                        self.assertIn("visible_for_roles: [primary_structure]", template)
                        contract = archive.read("vfs-author/references/strategy-contract.md").decode("utf-8")
                        self.assertIn("visible_for_roles", contract)
                        self.assertIn("excluded_role_keys", contract)
                        self.assertIn("filter_equivalent", contract)
                        self.assertIn("vfs-author/references/role-presentation.md", names)
                        self.assertIn("references/role-presentation.md", skill)
                        reference = archive.read("vfs-author/references/role-presentation.md").decode("utf-8")
                        self.assertIn("extensions", reference)
                        self.assertIn("role_presentation", reference)
                        self.assertIn("references/parameter-preview.md", skill)
                        self.assertIn("vfs-author/references/parameter-preview.md", names)
                        self.assertIn("parameter_preview", template)
                        preview_guide = archive.read("vfs-author/references/parameter-preview.md").decode("utf-8")
                        self.assertIn("参考范围不属于策略", preview_guide)
                        self.assertIn("不会重跑策略", preview_guide)
                        self.assertIn("不要求策略增加新输出字段", preview_guide)

    def test_vfa_author_is_explicitly_dts_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "vfa-author.zip"
            write_authoring_kit("vfa-author", path)
            with zipfile.ZipFile(path, mode="r") as archive:
                catalog = archive.read(
                    "vfa-author/assets/catalog-template/catalog.json"
                ).decode("utf-8")
                document = load_catalog_yaml(catalog)
                self.assertEqual(document["target_type"], "dts")
                self.assertEqual(document["provider_id"], "dts.static-mesh")
                self.assertEqual(document["assets"][0]["reference_height_m"], 3.0)

    def test_vfa_builder_rejects_invalid_reference_dimensions(self) -> None:
        template = ROOT / "packages/authoring-kits/src/verdflow/authoring_kits/kits/vfa-author/assets/catalog-template"
        builder = runpy.run_path(str(template / "build_vfa.py"))
        document = json.loads((template / "catalog.json").read_text(encoding="utf-8"))
        for field in ("reference_radius_m", "reference_height_m"):
            for value in (0, -1, float("inf"), float("nan"), True, None, "3"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    invalid = copy.deepcopy(document)
                    invalid["assets"][0][field] = value
                    builder["_validate_dts_catalog"](invalid)

    def test_vfa_author_builder_produces_a_package_accepted_by_core_inspector(
        self,
    ) -> None:
        template = (
            ROOT
            / "packages"
            / "authoring-kits"
            / "src"
            / "verdflow"
            / "authoring_kits"
            / "kits"
            / "vfa-author"
            / "assets"
            / "catalog-template"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "catalog.vfa"
            subprocess.run(
                [
                    sys.executable,
                    str(template / "build_vfa.py"),
                    str(template / "catalog.json"),
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            inspection = inspect_asset_catalog_package(output)
            self.assertEqual(inspection.catalog.target_type, "dts")
            self.assertEqual(inspection.catalog.provider_id, "dts.static-mesh")

    def test_vfs_author_template_matches_current_strategy_contract(self) -> None:
        template = (
            ROOT
            / "packages"
            / "authoring-kits"
            / "src"
            / "verdflow"
            / "authoring_kits"
            / "kits"
            / "vfs-author"
            / "assets"
            / "strategy-template"
        )
        package = inspect_strategy_package(template)
        self.assertEqual(package.package_id, "example_strategy")
        self.assertEqual(package.package_version, "1.0.0")
        presentation = package.strategy["extensions"]["role_presentation"]
        self.assertEqual(presentation["version"], "1")
        self.assertEqual(set(presentation["roles"]), {role["role_key"] for role in package.strategy["roles"]})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "example.vfs"
            kit = Path(temporary) / "kit.zip"
            write_authoring_kit("vfs-author", kit)
            with zipfile.ZipFile(kit) as archive:
                archive.extractall(Path(temporary) / "kit")
            builder = Path(temporary) / "kit/vfs-author/assets/build_vfs.py"
            subprocess.run(
                [sys.executable, str(builder), str(template), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            with open_strategy_package_archive(output) as opened:
                self.assertEqual(opened.package.package_hash, package.package_hash)
                self.assertEqual(opened.package.strategy["extensions"]["role_presentation"], presentation)

    def test_rejects_unknown_skill_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "Unknown"):
                write_authoring_kit(
                    "../../../outside",
                    Path(temporary) / "outside.zip",
                )


if __name__ == "__main__":
    unittest.main()
