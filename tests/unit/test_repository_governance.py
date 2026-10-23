from __future__ import annotations

import json
import os
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON_WORKSPACE_ROOTS = ("packages", "adapters", "bridges", "apps")
MODULE_ROOTS = ("packages", "adapters", "bridges", "apps")
REQUIRED_README_SECTIONS = (
    "## 职责",
    "## 公共入口",
    "## 依赖边界",
    "## 验证",
    "## 当前限制",
)


class RepositoryGovernanceTests(unittest.TestCase):
    def test_product_versions_match_without_bumping_strategy_contracts(self) -> None:
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        for app in ("studio", "dts-live-webui", "studio-electron-probe"):
            with self.subTest(app=app):
                manifest = ROOT / "apps" / app / "package.json"
                self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["version"], version)

    def test_tool_catalog_points_to_maintained_files(self) -> None:
        tools = ROOT / "tools"
        catalog = (tools / "README.md").read_text(encoding="utf-8")
        targets = re.findall(r"\]\(([^)]+)\)", catalog)
        self.assertGreater(len(targets), 10)
        for target in targets:
            with self.subTest(target=target):
                path = (tools / target).resolve()
                self.assertTrue(path.is_relative_to(tools.resolve()))
                self.assertTrue(path.is_file())

    def test_integration_helpers_have_an_active_tool_or_test_caller(self) -> None:
        callers = [*(ROOT / "tools").glob("*.py"), *(ROOT / "tests" / "unit").glob("test_*.py")]
        content = "\n".join(path.read_text(encoding="utf-8") for path in callers)
        for helper in (ROOT / "tests" / "integration" / "helpers").glob("*.py"):
            with self.subTest(helper=helper.name):
                self.assertIn(helper.name, content)

    def test_workspace_members_match_package_manifests(self) -> None:
        workspace = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        declared = {
            (ROOT / member).resolve()
            for member in workspace["tool"]["uv"]["workspace"]["members"]
        }
        actual = self.python_package_roots()
        self.assertEqual(declared, actual)

    def test_every_package_has_maintainer_readme(self) -> None:
        for package_root in self.module_roots():
            with self.subTest(package=package_root.name):
                readme = package_root / "README.md"
                self.assertTrue(readme.is_file(), f"Missing {readme}")
                content = readme.read_text(encoding="utf-8")
                self.assertIn("维护责任：", content)
                for section in REQUIRED_README_SECTIONS:
                    self.assertIn(section, content)

    def test_package_catalog_links_every_package(self) -> None:
        catalog = (ROOT / "packages" / "README.md").read_text(encoding="utf-8")
        for package_root in self.python_package_roots():
            link = Path(
                os.path.relpath(
                    package_root / "README.md",
                    ROOT / "packages",
                )
            ).as_posix()
            with self.subTest(package=package_root.name):
                self.assertIn(f"({link})", catalog)

    def test_internal_dependencies_follow_layer_direction(self) -> None:
        manifests = {
            manifest.parent.resolve(): tomllib.loads(
                manifest.read_text(encoding="utf-8")
            )
            for root_name in PYTHON_WORKSPACE_ROOTS
            if (ROOT / root_name).is_dir()
            for manifest in (ROOT / root_name).glob("*/pyproject.toml")
        }
        package_by_name = {
            manifest["project"]["name"]: path
            for path, manifest in manifests.items()
        }
        ranks = {
            path: self.layer_rank(path)
            for path in manifests
        }
        for path, manifest in manifests.items():
            dependencies = manifest["project"].get("dependencies", [])
            for dependency in dependencies:
                match = re.match(r"[A-Za-z0-9_.-]+", dependency)
                if match is None:
                    continue
                dependency_path = package_by_name.get(match.group(0))
                if dependency_path is None:
                    continue
                with self.subTest(
                    package=manifest["project"]["name"],
                    dependency=match.group(0),
                ):
                    self.assertLess(
                        ranks[dependency_path],
                        ranks[path],
                        "Internal dependency points upward or sideways",
                    )

    @staticmethod
    def python_package_roots() -> set[Path]:
        return {
            manifest.parent.resolve()
            for root_name in PYTHON_WORKSPACE_ROOTS
            if (ROOT / root_name).is_dir()
            for manifest in (ROOT / root_name).glob("*/pyproject.toml")
        }

    @staticmethod
    def module_roots() -> set[Path]:
        roots: set[Path] = set()
        for root_name in MODULE_ROOTS:
            root = ROOT / root_name
            if not root.is_dir():
                continue
            for manifest_name in ("pyproject.toml", "package.json"):
                roots.update(
                    manifest.parent.resolve()
                    for manifest in root.glob(f"*/{manifest_name}")
                )
        return roots

    @staticmethod
    def layer_rank(path: Path) -> int:
        relative = path.relative_to(ROOT)
        if relative.as_posix() == "packages/contracts":
            return 0
        if relative.as_posix() == "packages/core":
            return 2
        if relative.as_posix() == "apps/core-host":
            return 4
        if relative.as_posix() == "apps/system-host":
            return 5
        if relative.as_posix() == "apps/studio-service":
            return 5
        if relative.as_posix() == "apps/local-api":
            # The desktop loopback API is the outer process composition root.
            # It may control optional app transports without moving their
            # protocol or business logic into the generic API layer.
            return 7
        if relative.parts[0] == "apps":
            return 6
        if relative.parts[0] == "packages":
            return 1
        if relative.parts[0] == "adapters":
            return 3
        return 4


if __name__ == "__main__":
    unittest.main()
