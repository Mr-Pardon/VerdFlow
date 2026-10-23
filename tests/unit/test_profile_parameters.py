from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages" / name / "src") for name in (
    "contracts", "planting", "library-store", "profile-package",
)]

from verdflow.planting import (
    PlantingDocumentError, build_profile_v2, load_catalog_yaml, load_profile_yaml,
    load_strategy_yaml, prepare_role_binding_profile_document, profile_generation_args,
    sync_profile_catalog_metadata, update_profile_v2, validate_binding_strategy,
)
from verdflow.profile_package import inspect_role_binding_profile_package, write_role_binding_profile_package


def _inputs():
    catalog = load_catalog_yaml((ROOT / "asset-packs/dts-default/catalog.yaml").read_text(encoding="utf-8"))
    profile = load_profile_yaml((ROOT / "asset-packs/dts-default/profiles/layered-ecological-community-v2.yaml").read_text(encoding="utf-8"))
    strategy = load_strategy_yaml((ROOT / "strategy-packs/layered-ecological-community-v2/strategy.yaml").read_text(encoding="utf-8"))
    return strategy, catalog, profile


class ProfileParameterTests(unittest.TestCase):
    def test_preset_is_optional_and_empty_is_distinct(self):
        strategy, _, profile = _inputs()
        validate_binding_strategy(strategy, profile)
        without = prepare_role_binding_profile_document(profile)
        profile["strategy_parameters"] = {}
        validate_binding_strategy(strategy, profile)
        self.assertNotEqual(without.semantic_hash, prepare_role_binding_profile_document(profile).semantic_hash)
        self.assertEqual(profile_generation_args(profile, {}), {"strategy_parameters": {}})

    def test_rejects_unknown_out_of_range_nonfinite_and_non_numeric(self):
        strategy, _, base = _inputs()
        for values in ({"unknown": 1}, {"focal_spacing_m": 500}, {"focal_spacing_m": True},
                       {"focal_spacing_m": "10"}, {"focal_spacing_m": float("nan")},
                       {"focal_spacing_m": float("inf")}, {"focal_spacing_m": 10**999}, None, []):
            with self.subTest(values=values), self.assertRaises(PlantingDocumentError):
                validate_binding_strategy(strategy, {**base, "strategy_parameters": values})

    def test_mapping_rebuild_update_and_catalog_sync_preserve_preset(self):
        strategy, catalog, profile = _inputs()
        profile["strategy_parameters"] = {"focal_spacing_m": 20}
        assignments = copy.deepcopy(profile["assignments"])
        rebuilt = build_profile_v2(profile_id=profile["profile_id"], display_name="Rebuilt",
            strategy_id=strategy["strategy_id"], strategy_version=strategy["strategy_version"],
            catalog=catalog, assignments=assignments, base_profile=profile)
        updated = update_profile_v2(profile, display_name="Updated", assignments=assignments)
        synced = sync_profile_catalog_metadata(profile, catalog)
        for result in (rebuilt, updated, synced):
            self.assertEqual(result["strategy_parameters"], profile["strategy_parameters"])

    def test_runtime_override_priority_and_no_mutation(self):
        _, _, profile = _inputs()
        profile["strategy_parameters"] = {"focal_spacing_m": 20, "groundcover_spacing_m": 4}
        args = {"strategy_parameters": {"focal_spacing_m": 12}, "other": {"value": 1}}
        frozen = profile_generation_args(profile, args)
        self.assertEqual(frozen["strategy_parameters"], {"focal_spacing_m": 12, "groundcover_spacing_m": 4})
        profile["strategy_parameters"]["groundcover_spacing_m"] = 5
        args["other"]["value"] = 9
        self.assertEqual(frozen["strategy_parameters"]["groundcover_spacing_m"], 4)
        self.assertEqual(frozen["other"]["value"], 1)
        self.assertEqual(args["strategy_parameters"], {"focal_spacing_m": 12})

    def test_pure_mapping_leaves_runtime_parameters_untouched(self):
        _, _, profile = _inputs()
        args = {"strategy_parameters": {"focal_spacing_m": 14}}
        self.assertEqual(profile_generation_args(profile, args), args)
        self.assertEqual(profile_generation_args(None, args), args)

    def test_vfp_roundtrip_includes_parameters_and_no_seed(self):
        _, _, profile = _inputs()
        for values in ({}, {"focal_spacing_m": 14}):
            with self.subTest(values=values), tempfile.TemporaryDirectory() as temporary:
                profile["strategy_parameters"] = values
                document = prepare_role_binding_profile_document(profile)
                first, second = Path(temporary) / "one.vfp", Path(temporary) / "two.vfp"
                for path in (first, second):
                    write_role_binding_profile_package(document, strategy_package_hash="sha256:" + "1" * 64, destination=path)
                self.assertEqual(first.read_bytes(), second.read_bytes())
                decoded = json.loads(inspect_role_binding_profile_package(first).profile.canonical_json)
                self.assertEqual(decoded["strategy_parameters"], values)
                self.assertNotIn("random_seed", decoded)


if __name__ == "__main__":
    unittest.main()
