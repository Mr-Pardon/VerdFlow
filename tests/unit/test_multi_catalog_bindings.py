from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import sqlite3
from contextlib import closing
import unittest

from tests.unit.test_dts_live_configuration import _config, _prepare_layered_library
from tests.unit import test_project_snapshots as snapshot_fixtures
from verdflow.core.project_snapshots import ProjectSnapshotService
from verdflow.planting import (
    PlantingDocumentError, build_binding_draft, build_effective_binding_from_draft,
    catalog_binding_candidate, resolve_asset_assignments,
)
from verdflow.project_store import ProjectStore
from verdflow.system_host import DtsLiveConfigurationRequest, RoleBindingProfileSaveRequest, SystemHost
from verdflow.system_host.models import AssetCatalogSaveRequest

ROOT = Path(__file__).resolve().parents[2]


def catalog(identifier: str, model: str) -> dict:
    return {
        "schema": "verdflow.target-asset-catalog", "version": "1",
        "catalog_id": identifier, "display_name": identifier,
        "target_type": "dts", "provider_id": "dts.static-mesh",
        "assets": [{"asset_id": "tree_01", "display_name": "Tree",
                    "provider_metadata": {"model_name": model}, "reference_radius_m": 2.0,
                    "reference_height_m": 6.0}],
    }


class MultiCatalogBindingTests(unittest.TestCase):
    def test_ids_are_source_qualified_stable_and_not_derived_from_metadata(self) -> None:
        a, b = catalog("permanent", "ModelA"), catalog("temporary", "ModelB")
        first = catalog_binding_candidate(a, a["assets"][0])
        second = catalog_binding_candidate(b, b["assets"][0])
        self.assertNotEqual(first["asset_id"], second["asset_id"])
        a["assets"][0].update(display_name="Renamed", reference_radius_m=5)
        self.assertEqual(first["asset_id"], catalog_binding_candidate(a, a["assets"][0])["asset_id"])
        self.assertEqual(first["catalog_link"]["asset_id"], "tree_01")

    def test_provider_mixing_and_incomplete_choices_fail_before_execution(self) -> None:
        a, b = catalog("a", "A"), catalog("b", "B")
        candidates = [catalog_binding_candidate(value, value["assets"][0]) for value in (a, b)]
        candidates[1]["target_asset_ref"]["provider_id"] = "other.provider"
        with self.assertRaisesRegex(PlantingDocumentError, "compatible"):
            build_binding_draft({"tree": {"candidates": candidates}}, target_type="dts")
        with self.assertRaises(PlantingDocumentError):
            build_binding_draft({"tree": {"candidates": [{"asset_id": "tree_01"}]}}, target_type="dts")

    def test_same_target_from_different_catalogs_is_not_silently_merged(self) -> None:
        fixture = snapshot_fixtures.ProjectSnapshotServiceTests()
        candidates = [catalog_binding_candidate(source, source["assets"][0])
                      for source in (catalog("a", "SameModel"), catalog("b", "SameModel"))]
        self.assertEqual(candidates[0]["catalog_link"]["observed_target_hash"], candidates[1]["catalog_link"]["observed_target_hash"])
        assignments = {key: {"candidates": [candidates[index]]}
                       for index, key in enumerate(fixture._profile()["assignments"])}
        effective = build_effective_binding_from_draft(fixture._strategy(), assignments, target_type="dts")
        self.assertEqual(len(effective["asset_use"]["assets"]), 2)

    def test_draft_snapshot_is_persisted_without_a_synthetic_catalog_or_profile(self) -> None:
        fixture = snapshot_fixtures.ProjectSnapshotServiceTests()
        profile = fixture._profile()
        candidates = []
        for identifier, model in (("a", "ModelA"), ("b", "ModelB")):
            source = catalog(identifier, model)
            candidates.append(catalog_binding_candidate(source, source["assets"][0]))
        assignments = copy.deepcopy(profile["assignments"])
        for index, assignment in enumerate(assignments.values()):
            assignment["candidates"] = [candidates[index % len(candidates)]]
        effective = build_effective_binding_from_draft(fixture._strategy(), assignments, target_type="dts")
        self.assertIsNone(effective["source_profile"])
        self.assertEqual(effective["asset_use"]["source"]["kind"], "draft")
        self.assertEqual(len(effective["asset_use"]["assets"]), 2)
        resolved = resolve_asset_assignments(fixture._points_document(), effective, random_seed=42)
        self.assertEqual(len(resolved["assignments"]), 2)
        with tempfile.TemporaryDirectory() as directory:
            with ProjectStore.create(Path(directory) / "test.verdflow", "Test") as store:
                points = fixture._point_set(store)
                service = ProjectSnapshotService(store)
                service.finalize_strategy_package(points.planting_run_id, fixture._package_artifact(store))
                snapshot = service.finalize_effective_binding(points.planting_run_id, effective)
                self.assertEqual(snapshot.asset_use.source_kind, "draft")
                self.assertEqual(snapshot.asset_use.source_id, snapshot.asset_use.source_hash)

    def test_v22_migration_preserves_existing_binding_graph(self) -> None:
        fixture = snapshot_fixtures.ProjectSnapshotServiceTests()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.verdflow"
            with ProjectStore.create(path, "Old") as store:
                points = fixture._point_set(store)
                service = ProjectSnapshotService(store)
                service.finalize_strategy_package(points.planting_run_id, fixture._package_artifact(store))
                from verdflow.planting import build_effective_binding_from_profile
                original = service.finalize_effective_binding(points.planting_run_id,
                    build_effective_binding_from_profile(fixture._strategy(), fixture._profile()))
            with closing(sqlite3.connect(path / "project.db")) as connection:
                connection.execute("UPDATE schema_metadata SET value = '22' WHERE key = 'schema_version'")
                connection.execute("UPDATE projects SET schema_version = 22")
                connection.commit()
            with ProjectStore.open(path) as store:
                self.assertEqual(store.project_info().schema_version, 23)
                preserved = store.get_effective_binding_snapshot(original.effective_binding.effective_binding_snapshot_id)
                self.assertEqual(preserved.semantic_hash, original.effective_binding.semantic_hash)
                source = catalog("temporary", "Model")
                candidate = catalog_binding_candidate(source, source["assets"][0])
                assignments = {key: {"candidates": [candidate]} for key in fixture._profile()["assignments"]}
                draft = build_effective_binding_from_draft(fixture._strategy(), assignments, target_type="dts")
                result = ProjectSnapshotService(store).finalize_effective_binding(points.planting_run_id, draft)
                self.assertEqual(result.asset_use.source_kind, "draft")

    def test_multi_catalog_plan_saves_reopens_syncs_and_executes_after_sources_are_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_hash, base = _prepare_layered_library(root / "library")
            a, b = catalog("permanent", "ModelA"), catalog("temporary", "ModelB")
            with SystemHost(_config(root)) as system:
                candidates = []
                for source in (a, b):
                    system.save_asset_catalog(AssetCatalogSaveRequest(document=source, expected_generation=0))
                    item = system.get_asset_catalog_assets("dts", source["catalog_id"]).items[0]
                    self.assertEqual(item.asset_id, "tree_01")
                    candidates.append(item.binding_candidate)
                assignments = copy.deepcopy(base["assignments"])
                for assignment in assignments.values():
                    assignment["candidates"] = copy.deepcopy(candidates)
                saved = system.save_role_binding_profile(RoleBindingProfileSaveRequest(
                    display_name="Mixed", strategy_id=base["strategy"]["strategy_id"],
                    strategy_version=base["strategy"]["strategy_version"], target_type="dts",
                    catalog_id=None, assignments=assignments,
                ))
                self.assertIsNone(saved.summary.catalog_id)
                self.assertEqual(saved.drift_count, 0)
                self.assertEqual(len(saved.assets), 2)
                changed = copy.deepcopy(b)
                changed["assets"][0].update(display_name="Updated", reference_radius_m=4.0)
                system.save_asset_catalog(AssetCatalogSaveRequest(document=changed, expected_generation=1))
                drifted = system.get_role_binding_profile(saved.summary.profile_id)
                self.assertIn("metadata_changed", drifted.catalog_drift.values())
                synced = system.sync_role_binding_profile_assets(saved.summary.profile_id, expected_generation=saved.summary.generation)
                values = next(iter(synced.assignments.values()))["candidates"]
                self.assertEqual(values[0]["reference_radius_m"], 2.0)
                self.assertEqual(values[1]["reference_radius_m"], 4.0)
                output = root / "mixed.vfp"
                system.export_role_binding_profile_package(saved.summary.profile_id, output)
                removed = system.delete_asset_catalog("dts", "permanent")
                self.assertEqual(removed.affected_profiles, 1)
                system.delete_asset_catalog("dts", "temporary")
                system.delete_role_binding_profile(saved.summary.profile_id)
                system.import_role_binding_profile_package(output)
                reopened = system.get_role_binding_profile(saved.summary.profile_id)
                self.assertEqual(len(reopened.assets), 2)
                snapshot = system.freeze_dts_live_execution(DtsLiveConfigurationRequest(
                    strategy_package_hash=package_hash, profile_id=saved.summary.profile_id,
                ))
                self.assertEqual(snapshot.asset_source_kind, "profile")
                one_time = system.freeze_dts_live_execution(DtsLiveConfigurationRequest(
                    strategy_package_hash=package_hash, assignments=reopened.assignments,
                ))
                self.assertEqual(one_time.asset_source_kind, "draft")
