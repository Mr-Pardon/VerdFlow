from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(path) for parent in ("packages", "apps", "adapters")
               for path in (ROOT / parent).glob("*/src")]

from verdflow.contracts import require_strategy_version, strategy_version_accepts
from verdflow.planting import (
    PlantingDocumentError, build_effective_binding_from_profile, validate_binding_strategy,
)
from verdflow.profile_package import inspect_role_binding_profile_package
from verdflow.system_host import SystemHost, RoleBindingProfileSaveRequest, DtsLiveConfigurationRequest
from verdflow.system_host.profile_strategies import ProfileStrategyResolver
from test_system_library_queries import _config, _prepare_library, _editable_assignments, STRATEGY_ROOT


class StrategyVersionTests(unittest.TestCase):
    def test_forward_stable_versions_only(self):
        for saved, candidate in [
            ("2.2.2", "2.2.3"), ("2.2.2", "2.10.0"), ("2.2.2", "2.2.2"),
            ("1.0.0+first", "1.0.1+next"),
        ]:
            self.assertTrue(strategy_version_accepts(saved, candidate))
        for saved, candidate in [
            ("2.2.2", "3.0.0"), ("2.2.2", "2.1.9"), ("2.2.2", "2.3.0-beta.1"),
            ("0.2.0", "0.2.1"), ("2.2.0-beta.1", "2.2.0"),
            ("2.2", "2.3.0"), ("1.0.0+a", "1.0.0+b"),
        ]:
            self.assertFalse(strategy_version_accepts(saved, candidate))
        for version in ("0.1.0", "1.0.0-alpha.1+build", "2.10.0"):
            self.assertEqual(require_strategy_version(version), version)
        for version in ("2.2", "v2.2.0", "02.2.0", "2.2.0-01", "2.2.0\n"):
            with self.assertRaises(ValueError):
                require_strategy_version(version)


class PlanCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package, catalog, profile = _prepare_library(self.root / "library")
        self.system = SystemHost(_config(self.root))
        self.system.start()
        self.addCleanup(self.system.close)
        self.system.trust_strategy(self.package.package_hash)
        self.created = self.system.save_role_binding_profile(RoleBindingProfileSaveRequest(
            profile_id="compatibility_plan", display_name="Compatibility plan",
            strategy_id=self.package.package_id, strategy_version=self.package.package_version,
            target_type="dts", catalog_id=catalog["catalog_id"],
            assignments=_editable_assignments(profile), strategy_parameters={},
        ))

    def install(self, version: str, *, trust=True, change=None):
        source = self.root / version
        shutil.copytree(STRATEGY_ROOT, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        strategy_file = source / "strategy.yaml"
        strategy = yaml.safe_load(strategy_file.read_text(encoding="utf-8"))
        strategy["strategy_version"] = version
        if change:
            change(strategy)
        strategy_file.write_text(yaml.safe_dump(strategy, allow_unicode=True, sort_keys=False), encoding="utf-8")
        manifest_file = source / "strategy-package.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["package_version"] = version
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        installed = self.system.install_strategy(source)
        if trust:
            self.system.trust_strategy(installed.summary.package_hash)
        return installed

    def test_resolve_latest_trusted_without_mutating_and_dts_freezes_actual_version(self):
        minor = self.install("2.10.0")
        self.install("2.11.0", trust=False)
        self.install("3.0.0")
        self.install("2.12.0-beta.1")
        summary = self.system.list_role_binding_profiles("dts")[0]
        self.assertEqual(summary.strategy_version, "2.0.0")
        self.assertEqual(summary.resolved_strategy_version, "2.10.0")
        self.assertEqual(summary.resolved_strategy_hash, minor.summary.package_hash)
        self.assertEqual(summary.semantic_hash, self.created.summary.semantic_hash)
        snapshot = self.system.freeze_dts_live_execution(DtsLiveConfigurationRequest(
            strategy_package_hash=minor.summary.package_hash, profile_id=summary.profile_id,
        ))
        self.assertEqual(snapshot.strategy_version, "2.10.0")
        self.system.revoke_strategy_trust(minor.summary.package_hash)
        self.assertEqual(self.system.get_role_binding_profile(summary.profile_id).summary.resolved_strategy_version, "2.0.0")

    def test_compatibility_works_after_original_removed_and_save_advances_only_explicitly(self):
        newer = self.install("2.1.0")
        self.system.uninstall_strategy(self.package.package_hash)
        found = self.system.list_role_binding_profiles("dts", strategy_id=self.package.package_id, strategy_version="2.1.0")
        self.assertEqual(len(found), 1)
        exported = self.root / "plan.vfp"
        self.system.export_role_binding_profile_package(self.created.summary.profile_id, exported)
        archive = inspect_role_binding_profile_package(exported)
        self.assertEqual(archive.strategy.strategy_version, "2.1.0")
        self.assertEqual(archive.strategy.package_hash, newer.summary.package_hash)
        current = self.system.get_role_binding_profile(self.created.summary.profile_id)
        self.assertEqual(current.summary.strategy_version, "2.0.0")
        saved = self.system.save_role_binding_profile(RoleBindingProfileSaveRequest(
            profile_id=current.summary.profile_id, display_name="Updated",
            strategy_id=current.summary.strategy_id, strategy_version="2.1.0",
            target_type="dts", catalog_id=None, assignments=current.assignments,
            expected_generation=current.summary.generation,
        ))
        self.assertEqual(saved.summary.strategy_version, "2.1.0")
        self.assertEqual(saved.summary.generation, 2)

    def test_exchange_import_accepts_newer_package_without_original(self):
        exported = self.root / "original.vfp"
        self.system.export_role_binding_profile_package(self.created.summary.profile_id, exported)
        self.install("2.1.0")
        self.system.delete_role_binding_profile(self.created.summary.profile_id)
        self.system.uninstall_strategy(self.package.package_hash)
        imported = self.system.import_role_binding_profile_package(exported)
        detail = self.system.get_role_binding_profile(imported.profile.profile_id)
        self.assertEqual(detail.summary.strategy_version, "2.0.0")
        self.assertEqual(detail.summary.resolved_strategy_version, "2.1.0")

    def test_bad_structure_is_not_accepted_just_because_major_matches(self):
        self.install("2.1.0", change=lambda strategy: strategy["roles"].pop())
        summary = self.system.list_role_binding_profiles("dts")[0]
        self.assertEqual(summary.resolved_strategy_version, "2.0.0")
        self.system.uninstall_strategy(self.package.package_hash)
        summary = self.system.list_role_binding_profiles("dts")[0]
        self.assertIsNone(summary.resolved_strategy_hash)
        self.assertIn("不兼容", summary.strategy_resolution_issue)

    def test_new_required_role_blocks_automatic_upgrade(self):
        def extend(strategy):
            strategy["roles"].append({
                "role_key": "new_required", "display_name": "Required", "description": "Required",
                "required": True, "assignment_policy": "weighted_pool",
            })
        self.install("2.1.0", change=extend)
        summary = self.system.list_role_binding_profiles("dts")[0]
        self.assertEqual(summary.resolved_strategy_version, "2.0.0")
        self.system.uninstall_strategy(self.package.package_hash)
        summary = self.system.list_role_binding_profiles("dts")[0]
        self.assertIsNone(summary.resolved_strategy_hash)
        self.assertIn("required roles", summary.strategy_resolution_issue)

    def test_execution_rejects_malformed_parameters_without_a_saved_preset(self):
        profile = {
            "schema": "verdflow.role-binding-profile", "version": "2",
            "profile_id": self.created.summary.profile_id, "display_name": "Plan",
            "strategy": {"strategy_id": self.package.package_id, "strategy_version": "2.0.0"},
            "target": {"target_type": "dts", "provider_id": self.created.summary.provider_id},
            "assignments": self.created.assignments,
        }
        from unittest.mock import Mock
        resolver = ProfileStrategyResolver(Mock(list_strategies=lambda: []))
        resolver.store.get_strategy_by_hash.return_value = self.package
        resolver.trusted = lambda package: True
        resolver.document = lambda package: self.package.strategy
        resolver.load_profile = lambda profile_id: profile
        with self.assertRaisesRegex(PlantingDocumentError, "object"):
            resolver.prepare_execution(self.package.package_hash, profile["profile_id"], {}, {
                "strategy_parameters": [],
            })

    def test_new_optional_role_is_disabled_and_binding_freezes_resolved_identity(self):
        def extend(strategy):
            strategy["roles"].append({
                "role_key": "optional_accent", "display_name": "Accent", "description": "Optional",
                "required": False, "assignment_policy": "weighted_pool",
            })
        newer = self.install("2.1.0", change=extend)
        snapshot = self.system.freeze_dts_live_execution(DtsLiveConfigurationRequest(
            strategy_package_hash=newer.summary.package_hash, profile_id=self.created.summary.profile_id,
        ))
        self.assertEqual(snapshot.binding_overrides["optional_accent"], {"enabled": False, "candidates": []})
        profile = {
            "schema": "verdflow.role-binding-profile", "version": "2",
            "profile_id": self.created.summary.profile_id, "display_name": "Plan",
            "strategy": {"strategy_id": self.package.package_id, "strategy_version": "2.0.0"},
            "target": {"target_type": "dts", "provider_id": self.created.summary.provider_id},
            "assignments": self.created.assignments,
        }
        strategy = copy.deepcopy(self.package.strategy)
        strategy["strategy_version"] = "2.1.0"
        extend(strategy)
        effective = build_effective_binding_from_profile(strategy, profile)
        self.assertEqual(effective["strategy"]["strategy_version"], "2.1.0")
        self.assertEqual(profile["strategy"]["strategy_version"], "2.0.0")
        invalid = {**profile, "strategy_parameters": {"removed_parameter": 1}}
        with self.assertRaises(PlantingDocumentError):
            validate_binding_strategy(strategy, invalid)


if __name__ == "__main__":
    unittest.main()
