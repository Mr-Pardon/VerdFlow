from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(path) for parent in ("packages", "apps", "adapters") for path in (ROOT / parent).glob("*/src")]

from verdflow.profile_package import inspect_role_binding_profile_package
from verdflow.strategy_worker import inspect_strategy_package, write_strategy_package_archive
from verdflow.system_host import SystemHost, RoleBindingProfileSaveRequest
from verdflow.local_api import LocalApiConfig, LocalApiServer
from verdflow.system_host.resource_downloads import DownloadCancelled, StarterResource
from verdflow.system_host.resource_index import IndexedResource, ResourceIndex, parse_index, plan_metadata
from test_system_library_queries import _config, _prepare_library, _editable_assignments
import test_plan_version_compatibility as compatibility


def wait(host, operation, status=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        operation = host.get_starter_import(operation.operation_id)
        if operation.status == status or (status is None and operation.status not in {"preparing", "running", "cancelling"}):
            return operation
        time.sleep(0.01)
    raise AssertionError(operation)


def indexed(id, path, kind, identity, version="1.0.0", **metadata):
    content = path.read_bytes()
    resource = StarterResource(id, id, kind, version, identity, path.name, len(content), hashlib.sha256(content).hexdigest(),
        f"https://github.com/Mr-Pardon/verdflow-resources/releases/download/fixture/{path.name}",
        f"https://mr-pardon.github.io/verdflow-resources/?id={id}")
    return IndexedResource(resource, **metadata)


class PlanImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package, catalog, profile = _prepare_library(self.root / "library")
        self.catalog_id = catalog["catalog_id"]
        self.system = SystemHost(_config(self.root))
        self.system.start()
        self.addCleanup(self.system.close)
        self.system.trust_strategy(self.package.package_hash)
        self.created = self.system.save_role_binding_profile(RoleBindingProfileSaveRequest(
            profile_id="import-plan", display_name="Import plan", strategy_id=self.package.package_id,
            strategy_version=self.package.package_version, target_type="dts", catalog_id=self.catalog_id,
            assignments=_editable_assignments(profile), strategy_parameters={},
        ))
        self.plan = self.root / "plan.vfp"
        self.asset = self.root / "asset.vfa"
        self.strategy = self.root / "strategy.vfs"
        self.system.export_role_binding_profile_package("import-plan", self.plan)
        self.system.export_asset_catalog_package("dts", self.catalog_id, self.asset)
        write_strategy_package_archive(self.package, self.strategy)
        self.meta = plan_metadata(inspect_role_binding_profile_package(self.plan))
        self.assets = indexed("assets", self.asset, "assets", self.catalog_id, target_type="dts")
        self.strategies = indexed("strategy", self.strategy, "strategies", self.package.package_id,
                                  self.package.package_version, strategy_hash=self.package.package_hash)
        self.plans = indexed("plan", self.plan, "plans", self.meta.profile_id, plan=self.meta)
        self.system.delete_role_binding_profile("import-plan")
        self.index = ResourceIndex("a" * 64, (self.assets, self.strategies))
        self.downloads = []
        self.files = {"assets": self.asset, "strategy": self.strategy, "plan": self.plan}
        self.addCleanup(patch.stopall)
        self.load = patch("verdflow.system_host.plan_imports.load_index", side_effect=lambda **kw:
                          ResourceIndex("b" * 64, (self.plans,)) if kw.get("starter") else self.index).start()
        self.download = patch("verdflow.system_host.plan_imports.download_package", side_effect=self.copy_download).start()

    def copy_download(self, item, path, cancel, progress):
        self.downloads.append(item.id)
        shutil.copyfile(self.files[item.id], path)
        progress(path.stat().st_size)

    def prepare(self, **kwargs):
        return wait(self.system, self.system.prepare_resource_import((str(self.plan),), **kwargs))

    def confirm(self, operation, **kwargs):
        return wait(self.system, self.system.confirm_resource_import(operation.operation_id, **kwargs))

    def test_local_compatible_import_never_fetches_and_keeps_baseline(self):
        op = self.prepare()
        self.assertEqual(op.status, "awaiting_confirmation")
        newer = compatibility.PlanCompatibilityTests.install(self, "2.1.0")
        self.system.uninstall_strategy(self.package.package_hash)
        result = self.confirm(op)
        self.assertEqual(result.status, "completed")
        self.load.assert_not_called()
        self.download.assert_not_called()
        plan = self.system.get_role_binding_profile("import-plan")
        self.assertEqual(plan.summary.strategy_version, "2.0.0")
        self.assertEqual(plan.summary.resolved_strategy_hash, newer.summary.package_hash)

    def test_optional_catalog_default_skip_and_failed_download_do_not_block_plan(self):
        self.system.delete_asset_catalog("dts", self.catalog_id)
        op = self.prepare()
        self.assertEqual([c.id for c in op.choices if c.optional], ["assets"])
        self.assertEqual(self.confirm(op).status, "completed")
        self.assertEqual(self.downloads, [])
        self.system.delete_role_binding_profile("import-plan")
        op = self.prepare()
        self.download.side_effect = ValueError("offline")
        result = self.confirm(op, optional_ids=("assets",))
        self.assertEqual(result.status, "partial")
        self.assertIsNotNone(self.system.get_role_binding_profile("import-plan"))
        self.assertEqual(next(i.status for i in result.items if i.id == "assets"), "failed")

    def test_missing_optional_index_is_not_required_and_existing_catalog_is_preserved(self):
        self.system.delete_asset_catalog("dts", self.catalog_id)
        self.load.side_effect = ValueError("offline")
        op = self.prepare()
        self.assertTrue(op.warnings)
        self.assertEqual(self.confirm(op).status, "completed")

    def test_optional_catalog_added_during_confirmation_is_never_overwritten(self):
        self.system.delete_asset_catalog("dts", self.catalog_id)
        op = self.prepare()
        self.system.import_asset_catalog_package(self.asset)
        result = self.confirm(op, optional_ids=("assets",))
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.downloads, [])
        self.assertEqual(next(i.status for i in result.items if i.id == "assets"), "existing")

    def test_optional_catalog_success_preserves_plan_snapshot(self):
        self.system.delete_asset_catalog("dts", self.catalog_id)
        result = self.confirm(self.prepare(), optional_ids=("assets",))
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.downloads, ["assets"])
        exported = self.root / "after.vfp"
        self.system.export_role_binding_profile_package("import-plan", exported)
        self.assertEqual(plan_metadata(inspect_role_binding_profile_package(exported)), self.meta)

    def test_invalid_plan_does_not_block_another_plan(self):
        invalid = self.root / "invalid.vfp"
        invalid.write_bytes(b"not an archive")
        op = wait(self.system, self.system.prepare_resource_import((str(invalid), str(self.plan))))
        self.assertEqual(op.status, "awaiting_confirmation")
        self.assertEqual(sum(c.blocked for c in op.choices), 1)
        self.assertEqual(self.confirm(op).status, "partial")
        self.assertIsNotNone(self.system.get_role_binding_profile("import-plan"))

    def test_cancel_download_prevents_install_and_cleans_staging(self):
        self.system.uninstall_strategy(self.package.package_hash)
        started = Event()
        def stalled(item, path, cancel, progress):
            path.write_bytes(b"partial")
            started.set()
            if not cancel.wait(5):
                raise AssertionError("Cancellation did not arrive")
            raise DownloadCancelled()
        self.download.side_effect = stalled
        op = self.prepare()
        temporary = Path(self.system._starter_resources._temporary.name)
        self.system.confirm_resource_import(op.operation_id)
        self.assertTrue(started.wait(5))
        self.system.cancel_starter_import(op.operation_id)
        self.assertEqual(wait(self.system, op).status, "cancelled")
        self.assertFalse(temporary.exists())
        self.assertFalse(self.system.list_strategies())
        self.assertFalse(self.system.list_role_binding_profiles("dts"))

    def test_incompatible_remote_minor_is_rejected_before_strategy_installation(self):
        newer = compatibility.PlanCompatibilityTests.install(self, "2.1.0", trust=False, change=lambda s: s["roles"].pop())
        path = self.root / "incompatible.vfs"
        write_strategy_package_archive(inspect_strategy_package(self.root / "2.1.0"), path)
        self.files["bad"] = path
        self.index = ResourceIndex("a" * 64, (indexed("bad", path, "strategies", self.package.package_id,
            "2.1.0", strategy_hash=newer.summary.package_hash),))
        self.system.uninstall_strategy(self.package.package_hash)
        self.system.uninstall_strategy(newer.summary.package_hash)
        result = self.confirm(self.prepare())
        self.assertEqual(result.status, "failed")
        self.assertFalse(self.system.list_strategies())
        self.assertFalse(self.system.list_role_binding_profiles("dts"))

    def test_remote_latest_minor_is_confirmed_download_and_never_trusted(self):
        newer = compatibility.PlanCompatibilityTests.install(self, "2.1.0", trust=False)
        path = self.root / "newer.vfs"
        write_strategy_package_archive(inspect_strategy_package(self.root / "2.1.0"), path)
        self.files["newer"] = path
        candidate = indexed("newer", path, "strategies", self.package.package_id, "2.1.0", strategy_hash=newer.summary.package_hash)
        self.index = replace(self.index, resources=(*self.index.resources, candidate))
        self.system.uninstall_strategy(self.package.package_hash)
        self.system.uninstall_strategy(newer.summary.package_hash)
        op = self.prepare()
        self.assertIn("newer", [c.id for c in op.choices])
        self.index = ResourceIndex("c" * 64, ())
        result = self.confirm(op)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.downloads, ["newer"])
        self.assertEqual(self.system.get_role_binding_profile("import-plan").summary.strategy_version, "2.0.0")
        self.assertNotEqual(self.system.list_strategies()[0].trust_state, "trusted")
        self.assertTrue(any("信任" in item.message for item in result.items))

    def test_confirmed_generation_cannot_overwrite_new_local_changes(self):
        op = self.prepare()
        self.system.import_role_binding_profile_package(self.plan)
        result = self.confirm(op)
        self.assertEqual(result.status, "failed")
        self.assertIn("确认后已改变", result.items[0].message)

    def test_conflicting_plan_requires_explicit_overwrite(self):
        current = self.system.import_role_binding_profile_package(self.plan)
        detail = self.system.get_role_binding_profile(current.profile.profile_id)
        self.system.save_role_binding_profile(RoleBindingProfileSaveRequest(
            profile_id="import-plan", display_name="Local edit", strategy_id=detail.summary.strategy_id,
            strategy_version=detail.summary.strategy_version, target_type="dts", catalog_id=None,
            assignments=detail.assignments, expected_generation=detail.summary.generation,
        ))
        op = self.prepare()
        self.assertTrue(op.choices[0].conflict)
        self.confirm(op)
        self.assertEqual(self.system.get_role_binding_profile("import-plan").summary.display_name, "Local edit")
        op = self.prepare()
        self.confirm(op, overwrite_ids=(op.choices[0].id,))
        self.assertEqual(self.system.get_role_binding_profile("import-plan").summary.display_name, "Import plan")

    def test_local_strategy_recovers_offline_and_cancel_releases_staging(self):
        self.system.uninstall_strategy(self.package.package_hash)
        self.load.side_effect = ValueError("offline")
        op = self.prepare(strategy_paths=(str(self.strategy),))
        self.assertFalse(any(c.blocked for c in op.choices))
        self.assertEqual(self.confirm(op).status, "completed")
        self.assertEqual(self.downloads, [])
        op = self.prepare()
        temporary = Path(self.system._starter_resources._temporary.name)
        self.system.cancel_starter_import(op.operation_id)
        self.assertFalse(temporary.exists())

    def test_recommended_plan_actual_metadata_checked_and_dependency_order_independent(self):
        self.system.uninstall_strategy(self.package.package_hash)
        op = wait(self.system, self.system.prepare_resource_import((), recommended=True))
        result = self.confirm(op)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.downloads, ["plan", "strategy"])

    def test_untrusted_local_strategy_is_reused_without_restoring_trust(self):
        self.system.revoke_strategy_trust(self.package.package_hash)
        op = self.prepare()
        result = self.confirm(op)
        self.assertEqual(result.status, "completed")
        self.assertNotEqual(self.system.list_strategies()[0].trust_state, "trusted")
        self.load.assert_not_called()

    def test_v2_parser_requires_real_dependency_metadata_and_rejects_ambiguity(self):
        record = {**asdict(self.plans.resource), "status": "published", "plan": asdict(self.meta)}
        def parse(records):
            return parse_index(json.dumps({"schemaVersion": 2, "revision": "a" * 64, "resources": records}).encode(), starter=True)
        self.assertEqual(parse([record]).resources[0].plan, self.meta)
        with self.assertRaises(ValueError):
            parse([record, record])
        with self.assertRaises(ValueError):
            parse([{**record, "plan": None}])

    def test_http_preflight_confirmation_and_status_are_management_authenticated(self):
        with LocalApiServer(self.system, LocalApiConfig(management_token="x" * 32)) as server:
            def request(path, body=None, auth=True):
                headers = {"Content-Type": "application/json"}
                if auth:
                    headers["Authorization"] = "Bearer " + "x" * 32
                with urlopen(Request(server.base_url + path, data=json.dumps(body).encode() if body else None,
                                     headers=headers), timeout=3) as response:
                    return json.load(response)
            with self.assertRaises(HTTPError) as caught:
                request("/api/v1/resource-imports/prepare", {"paths": [str(self.plan)]}, auth=False)
            self.assertEqual(caught.exception.code, 403)
            operation = request("/api/v1/resource-imports/prepare", {"paths": [str(self.plan)]})
            op = wait(self.system, self.system.get_starter_import(operation["operation_id"]))
            with self.assertRaises(HTTPError):
                request("/api/v1/starter-resources/import?operation_id=" + op.operation_id, auth=False)
            with self.assertRaises(HTTPError):
                request("/api/v1/resource-imports/confirm", {"operation_id": op.operation_id, "optional_ids": ["unshown"]})
            request("/api/v1/resource-imports/confirm", {"operation_id": op.operation_id})
            self.assertEqual(wait(self.system, op).status, "completed")


if __name__ == "__main__":
    unittest.main()
