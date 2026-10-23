from __future__ import annotations

import copy
import unittest

from tests.unit.test_planting_phase2a import _catalog, _points, _profile, _strategy
from verdflow.planting import (
    PlantingDocumentError,
    build_effective_binding_from_profile,
    build_effective_binding_snapshot,
    build_profile_v2,
    binding_catalog_drift,
    filter_point_roles,
    load_profile_yaml,
    normalize_asset_selection,
    resolve_asset_assignments,
    select_output_assets,
    sync_profile_catalog_metadata,
    prepare_asset_catalog_document,
    update_profile_v2,
    prepare_role_binding_profile_document,
)
import yaml


class PlantingSelectionTests(unittest.TestCase):
    def test_reference_height_freezes_and_syncs_without_changing_assignments(self):
        catalog = _catalog()
        profile = _profile()
        strategy = _strategy()
        role = "low_canopy_filler"
        asset_id = profile["assignments"][role]["candidates"][0]["asset_id"]
        source = next(asset for asset in catalog["assets"] if asset["asset_id"] == asset_id)
        source["reference_height_m"] = 4.2
        created = build_profile_v2(
            profile_id="height_profile", display_name="Height Profile",
            strategy_id=strategy["strategy_id"], strategy_version=strategy["strategy_version"],
            catalog=catalog, assignments=profile["assignments"],
        )
        candidate = created["assignments"][role]["candidates"][0]
        self.assertEqual(candidate["reference_height_m"], 4.2)
        effective = build_effective_binding_from_profile(strategy, created)
        runtime = next(asset for asset in effective["asset_use"]["assets"] if asset["asset_id"] == asset_id)
        self.assertEqual(runtime["reference_height_m"], 4.2)
        without_height = copy.deepcopy(created)
        without_height["assignments"][role]["candidates"][0].pop("reference_height_m")
        self.assertEqual(
            resolve_asset_assignments(_points(), effective, random_seed=17)["assignments"],
            resolve_asset_assignments(_points(), build_effective_binding_from_profile(strategy, without_height), random_seed=17)["assignments"],
        )
        source["reference_height_m"] = 5.8
        self.assertEqual(binding_catalog_drift(created, catalog)[f"{role}/{asset_id}"], "metadata_changed")
        self.assertEqual(candidate["reference_height_m"], 4.2)
        synced = sync_profile_catalog_metadata(created, catalog)
        self.assertEqual(synced["assignments"][role]["candidates"][0]["reference_height_m"], 5.8)
        self.assertEqual(synced["assignments"][role]["candidates"][0]["target_asset_ref"], candidate["target_asset_ref"])
        source.pop("reference_height_m")
        cleared = sync_profile_catalog_metadata(synced, catalog)
        self.assertNotIn("reference_height_m", cleared["assignments"][role]["candidates"][0])

    def test_reference_height_rejects_invalid_values_and_remains_optional(self):
        prepare_asset_catalog_document(_catalog())
        prepare_role_binding_profile_document(_profile())
        for value in (0, -1, float("inf"), float("nan"), True, "5", None):
            with self.subTest(value=value):
                catalog = _catalog()
                catalog["assets"][0]["reference_height_m"] = value
                with self.assertRaises(PlantingDocumentError):
                    prepare_asset_catalog_document(catalog)
                profile = _profile()
                profile["assignments"]["low_canopy_filler"]["candidates"][0]["reference_height_m"] = value
                with self.assertRaises(PlantingDocumentError):
                    prepare_role_binding_profile_document(profile)

    def test_reference_radius_is_frozen_in_profile_and_runtime_snapshot(self):
        catalog = _catalog()
        profile = _profile()
        role_key = "low_canopy_filler"
        asset_id = profile["assignments"][role_key]["candidates"][0]["asset_id"]
        asset = next(item for item in catalog["assets"] if item["asset_id"] == asset_id)
        asset["reference_radius_m"] = 1.75
        created = build_profile_v2(
            profile_id="radius_profile",
            display_name="Radius Profile",
            strategy_id=_strategy()["strategy_id"],
            strategy_version=_strategy()["strategy_version"],
            catalog=catalog,
            assignments=profile["assignments"],
        )

        candidate = created["assignments"][role_key]["candidates"][0]
        self.assertEqual(candidate["reference_radius_m"], 1.75)
        effective = build_effective_binding_from_profile(_strategy(), created)
        runtime_asset = next(
            item for item in effective["asset_use"]["assets"]
            if item["asset_id"] == asset_id
        )
        self.assertEqual(runtime_asset["reference_radius_m"], 1.75)

        changed_catalog = copy.deepcopy(catalog)
        next(
            item for item in changed_catalog["assets"]
            if item["asset_id"] == asset_id
        )["reference_radius_m"] = 2.25
        self.assertEqual(
            binding_catalog_drift(created, changed_catalog)[f"{role_key}/{asset_id}"],
            "metadata_changed",
        )

    def test_reference_radius_must_be_finite_and_positive(self):
        for value in (0, -1, float("inf")):
            with self.subTest(value=value), self.assertRaises(PlantingDocumentError):
                catalog = _catalog()
                catalog["assets"][0]["reference_radius_m"] = value
                build_effective_binding_snapshot(
                    _strategy(),
                    catalog,
                    catalog_generation=1,
                    overrides={
                        role_key: {
                            "candidates": [
                                {
                                    "asset_id": assignment["candidates"][0]["asset_id"],
                                    "weight": 1,
                                }
                            ]
                        }
                        for role_key, assignment in _profile()["assignments"].items()
                    },
                )

    def test_catalog_metadata_sync_is_explicit_and_preserves_target_identity(self):
        catalog = _catalog()
        profile = _profile()
        role_key = "low_canopy_filler"
        asset_id = profile["assignments"][role_key]["candidates"][0]["asset_id"]
        created = build_profile_v2(
            profile_id="sync_profile",
            display_name="Sync Profile",
            strategy_id=_strategy()["strategy_id"],
            strategy_version=_strategy()["strategy_version"],
            catalog=catalog,
            assignments=profile["assignments"],
        )
        original_target = copy.deepcopy(
            created["assignments"][role_key]["candidates"][0][
                "target_asset_ref"
            ]
        )

        changed = copy.deepcopy(catalog)
        asset = next(
            item for item in changed["assets"] if item["asset_id"] == asset_id
        )
        asset["display_name"] = "Updated Display Name"
        asset["reference_radius_m"] = 2.5
        synced = sync_profile_catalog_metadata(created, changed)
        candidate = synced["assignments"][role_key]["candidates"][0]

        self.assertEqual(candidate["display_name_snapshot"], "Updated Display Name")
        self.assertEqual(candidate["reference_radius_m"], 2.5)
        self.assertEqual(candidate["target_asset_ref"], original_target)
        self.assertEqual(
            binding_catalog_drift(synced, changed)[f"{role_key}/{asset_id}"],
            "current",
        )
        self.assertNotIn(
            "reference_radius_m",
            created["assignments"][role_key]["candidates"][0],
        )

        target_changed = copy.deepcopy(changed)
        next(
            item
            for item in target_changed["assets"]
            if item["asset_id"] == asset_id
        )["provider_metadata"] = {"model_name": "replacement_asset"}
        blocked = sync_profile_catalog_metadata(created, target_changed)
        blocked_candidate = blocked["assignments"][role_key]["candidates"][0]
        self.assertNotIn("reference_radius_m", blocked_candidate)
        self.assertEqual(blocked_candidate["target_asset_ref"], original_target)
        self.assertEqual(
            binding_catalog_drift(blocked, target_changed)[f"{role_key}/{asset_id}"],
            "target_changed",
        )

    def test_disabled_role_preserves_configuration_and_other_random_choices(self):
        profile = _profile()
        original = copy.deepcopy(profile)
        full = resolve_asset_assignments(
            _points(), build_effective_binding_from_profile(_strategy(), profile), random_seed=17,
        )
        profile["assignments"]["medium_canopy_accent"]["enabled"] = False
        effective = build_effective_binding_from_profile(_strategy(), profile)
        points = filter_point_roles(_points(), ("medium_canopy_accent",))
        self.assertEqual(points["points"], [_points()["points"][0]])
        assigned = resolve_asset_assignments(points, effective, random_seed=17)
        self.assertEqual(assigned["assignments"], [full["assignments"][0]])
        self.assertNotIn("medium_canopy_accent", effective["assignments"])
        self.assertEqual(profile["assignments"]["medium_canopy_accent"]["candidates"],
                         original["assignments"]["medium_canopy_accent"]["candidates"])

    def test_disabled_empty_mapping_can_be_saved_and_loaded(self):
        profile = _profile()
        assignments = copy.deepcopy(profile["assignments"])
        assignments["medium_canopy_accent"] = {"enabled": False, "candidates": []}
        created = build_profile_v2(
            profile_id="test_profile", display_name="Test", strategy_id=_strategy()["strategy_id"],
            strategy_version=_strategy()["strategy_version"], catalog=_catalog(), assignments=assignments,
        )
        updated = update_profile_v2(created, display_name="Edited", assignments=assignments)
        self.assertEqual(updated["assignments"]["medium_canopy_accent"], assignments["medium_canopy_accent"])
        self.assertEqual(load_profile_yaml(yaml.safe_dump(updated)), updated)
        effective = build_effective_binding_snapshot(_strategy(), _catalog(), catalog_generation=1, overrides=assignments)
        self.assertNotIn("medium_canopy_accent", effective["assignments"])

    def test_profile_rejects_all_disabled_invalid_flag_and_enabled_empty(self):
        for invalid in ("all_disabled", "invalid_flag", "enabled_empty"):
            with self.subTest(invalid=invalid):
                profile = _profile()
                role = profile["assignments"]["low_canopy_filler"]
                if invalid == "all_disabled":
                    for value in profile["assignments"].values():
                        value["enabled"] = False
                elif invalid == "invalid_flag":
                    role["enabled"] = "false"
                else:
                    role["candidates"] = []
                with self.assertRaises(PlantingDocumentError):
                    prepare_role_binding_profile_document(profile)

    def test_required_omission_is_not_explicit_disable(self):
        profile = _profile()
        del profile["assignments"]["medium_canopy_accent"]
        with self.assertRaises(PlantingDocumentError):
            build_effective_binding_from_profile(_strategy(), profile)

    def test_override_does_not_mutate_profile_and_identical_freeze_is_stable(self):
        profile = _profile()
        original = copy.deepcopy(profile)
        base = build_effective_binding_from_profile(_strategy(), profile)
        identical = build_effective_binding_from_profile(_strategy(), profile, overrides={
            key: {**assignment, "enabled": True} for key, assignment in profile["assignments"].items()
        })
        self.assertEqual(base, identical)
        changed = build_effective_binding_from_profile(_strategy(), profile,
            overrides={"medium_canopy_accent": {"enabled": False}})
        self.assertTrue(changed["override_applied"])
        self.assertEqual(profile, original)

    def test_output_selection_preserves_assignments_and_is_reversible(self):
        points = _points()
        full = resolve_asset_assignments(points, build_effective_binding_from_profile(_strategy(), _profile()), random_seed=7)
        original = copy.deepcopy((points, full))
        selected = (full["assignments"][0]["asset_id"],)
        filtered_points, filtered_assignments = select_output_assets(points, full, selected)
        self.assertEqual(filtered_points["points"], [points["points"][0]])
        self.assertEqual(filtered_assignments["assignments"], [full["assignments"][0]])
        self.assertEqual(select_output_assets(points, full, None), original)
        self.assertEqual((points, full), original)
        with self.assertRaises(PlantingDocumentError):
            select_output_assets(points, full, ("missing",))

    def test_same_asset_across_roles_is_selected_together(self):
        profile = _profile()
        profile["assignments"]["medium_canopy_accent"]["candidates"] = copy.deepcopy(
            profile["assignments"]["low_canopy_filler"]["candidates"])
        points = _points()
        assigned = resolve_asset_assignments(points, build_effective_binding_from_profile(_strategy(), profile), random_seed=8)
        selected = (assigned["assignments"][0]["asset_id"],)
        self.assertEqual(select_output_assets(points, assigned, selected)[0]["points"], points["points"])

    def test_terrain_hit_filter_is_explicit_and_preserves_input(self):
        points = _points()
        points["kind"] = "elevation_resolved"
        points["resolution_policy"] = "sample_hit_else_generated"
        for index, point in enumerate(points["points"]):
            point["resolved_position"] = point["generated_position"]
            point["terrain_sample"] = {
                "hit": index == 1, "target": "test", "position": point["generated_position"], "normal": [0, 0, 1],
            }
        assigned = resolve_asset_assignments(
            points, build_effective_binding_from_profile(_strategy(), _profile()), random_seed=7,
        )
        original = copy.deepcopy((points, assigned))
        filtered, mappings = select_output_assets(points, assigned, None, require_terrain_hit=True)
        self.assertEqual(filtered["points"], [points["points"][1]])
        self.assertEqual(mappings["assignments"], [assigned["assignments"][1]])
        self.assertEqual(select_output_assets(points, assigned, None), original)
        self.assertEqual((points, assigned), original)
        with self.assertRaises(PlantingDocumentError):
            select_output_assets(points, assigned, (assigned["assignments"][0]["asset_id"],), require_terrain_hit=True)

    def test_empty_selection_and_no_surviving_points_are_rejected(self):
        for value in ([], (), ["x", "x"], [1], "x"):
            with self.subTest(value=value), self.assertRaises(PlantingDocumentError):
                normalize_asset_selection(value)
        with self.assertRaises(PlantingDocumentError):
            filter_point_roles(_points(), ("low_canopy_filler", "medium_canopy_accent"))


if __name__ == "__main__":
    unittest.main()
