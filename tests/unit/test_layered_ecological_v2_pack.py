from __future__ import annotations

import importlib.util
from copy import deepcopy
import math
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_ROOT = ROOT / "strategy-packs" / "layered-ecological-community-v2"
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]
from verdflow.planting import (  # noqa: E402
    build_effective_binding_from_profile,
    load_profile_yaml,
    resolve_asset_assignments,
    validate_point_set_v2,
)
from verdflow.strategy_worker import StrategyWorker, inspect_strategy_package  # noqa: E402


class LayeredEcologicalCommunityV2PackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = inspect_strategy_package(STRATEGY_ROOT)
        cls.worker = StrategyWorker({cls.package.package_hash})
        cls.algorithm = _load_algorithm_module()

    def test_package_generates_stable_two_role_point_set(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_b", 150.0), _region_document("region_a", 0.0)],
            "seed": 17,
        }
        first = self.worker.run(self.package, context)
        repeated = self.worker.run(self.package, context)
        reordered = self.worker.run(
            self.package,
            {**context, "selected_regions": list(reversed(context["selected_regions"]))},
        )
        self.assertEqual(first, repeated)
        self.assertEqual(first, reordered)
        validate_point_set_v2(first)
        self.assertEqual(first["strategy"]["strategy_version"], "2.2.2")
        self.assertEqual(
            {point["role_key"] for point in first["points"]},
            {"groundcover_matrix", "focal_structure"},
        )
        for point in first["points"]:
            x, y, z = point["generated_position"]
            local_x = x - (150.0 if point["region_id"] == "region_b" else 0.0)
            self.assertTrue(0.0 <= local_x <= 120.0 and 0.0 <= y <= 90.0)
            self.assertFalse(45.0 < local_x < 75.0 and 30.0 < y < 60.0)
            self.assertEqual(z, 2.0)

    def test_public_parameters_use_percent_and_meter_units(self) -> None:
        contract = self.package.strategy["generation_parameters"]
        self.assertEqual(
            [parameter["key"] for parameter in contract],
            [
                "groundcover_coverage_percent",
                "focal_core_ratio_percent",
                "groundcover_spacing_m",
                "focal_spacing_m",
                "patch_scale_m",
            ],
        )
        self.assertEqual(
            {parameter["key"]: parameter["default"] for parameter in contract},
            {
                "groundcover_coverage_percent": 85,
                "focal_core_ratio_percent": 55,
                "groundcover_spacing_m": 3.4,
                "focal_spacing_m": 11,
                "patch_scale_m": 48,
            },
        )
        self.assertEqual(
            {
                parameter["key"]: (parameter["min"], parameter["max"])
                for parameter in contract
            },
            {
                "groundcover_coverage_percent": (5, 98),
                "focal_core_ratio_percent": (1, 100),
                "groundcover_spacing_m": (0.5, 12),
                "focal_spacing_m": (2, 40),
                "patch_scale_m": (10, 200),
            },
        )

    def test_parameter_overrides_change_point_density(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_a", 0.0)],
            "seed": 17,
        }
        baseline = self.worker.run(self.package, context)
        sparse = self.worker.run(
            self.package,
            {
                **context,
                "args": {
                    "strategy_parameters": {
                        "groundcover_spacing_m": 8.0,
                        "focal_spacing_m": 24.0,
                    }
                },
            },
        )
        self.assertLess(sparse["point_count"], baseline["point_count"])

    def test_legacy_boundary_loops_find_outer_ring_without_reextraction(self) -> None:
        outer = [
            [8200.0, -6120.0, 4.0],
            [8400.0, -6120.0, 4.0],
            [8400.0, -5920.0, 4.0],
            [8200.0, -5920.0, 4.0],
            [8200.0, -6120.0, 4.0],
        ]
        hole = [
            [8260.0, -6060.0, 4.0],
            [8340.0, -6060.0, 4.0],
            [8340.0, -5980.0, 4.0],
            [8260.0, -5980.0, 4.0],
            [8260.0, -6060.0, 4.0],
        ]
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [
                    {
                        "id": "legacy_inner_first",
                        "footprint_boundary_xyz": [hole, outer],
                    }
                ],
                "seed": 17,
            },
        )

        self.assertGreater(point_set["point_count"], 0)
        self.assertTrue(
            all(
                not (
                    8260.0 < point["generated_position"][0] < 8340.0
                    and -6060.0 < point["generated_position"][1] < -5980.0
                )
                for point in point_set["points"]
            )
        )

    def test_region_area_is_stable_for_large_world_coordinates(self) -> None:
        origin = 1_000_000_000_000.0
        ring = (
            (origin, origin),
            (origin + 4.0, origin),
            (origin + 4.0, origin + 3.0),
            (origin, origin + 3.0),
        )

        self.assertEqual(self.algorithm._signed_area(ring), 12.0)
        self.algorithm._validate_region(
            self.algorithm.RegionShape("large_coordinates", ring)
        )

    def test_disabling_focal_role_preserves_groundcover_points(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_a", 0.0)],
            "seed": 43,
            "args": {
                "strategy_parameters": {
                    "groundcover_coverage_percent": 92,
                    "focal_core_ratio_percent": 83,
                    "groundcover_spacing_m": 1.2,
                    "focal_spacing_m": 12.5,
                }
            },
        }
        full = self.worker.run(self.package, context)
        retained = self.worker.run(
            self.package,
            {**context, "excluded_role_keys": ["focal_structure"]},
        )
        self.assertEqual(
            retained["points"],
            [point for point in full["points"] if point["role_key"] == "groundcover_matrix"],
        )

    def test_focal_only_layout_ignores_hidden_groundcover_parameters(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_a", 0.0)],
            "seed": 43,
            "args": {"strategy_parameters": {
                "groundcover_coverage_percent": 5,
                "groundcover_spacing_m": 0.5,
                "focal_core_ratio_percent": 55,
            }},
        }
        original = deepcopy(context)
        full = self.worker.run(self.package, context)
        focal_context = {**context, "excluded_role_keys": ["groundcover_matrix"]}
        focal = self.worker.run(self.package, focal_context)
        repeated = self.worker.run(self.package, focal_context)
        other_groundcover_values = self.worker.run(self.package, {
            **focal_context,
            "args": {"strategy_parameters": {
                "groundcover_coverage_percent": 98,
                "groundcover_spacing_m": 12,
                "focal_core_ratio_percent": 55,
            }},
        })

        self.assertGreater(focal["point_count"], 0)
        self.assertEqual(focal, repeated)
        self.assertEqual(focal, other_groundcover_values)
        self.assertEqual({point["role_key"] for point in focal["points"]}, {"focal_structure"})
        self.assertNotEqual(
            focal["points"],
            [point for point in full["points"] if point["role_key"] == "focal_structure"],
        )
        self.assertEqual(context, original)
        self.assertEqual(self.worker.run(self.package, context), full)
        for point in focal["points"]:
            x, y, _ = point["generated_position"]
            self.assertTrue(0.0 <= x <= 120.0 and 0.0 <= y <= 90.0)
            self.assertFalse(45.0 < x < 75.0 and 30.0 < y < 60.0)
        self.assertNotIn("selective_role_generation", self.package.strategy["extensions"])

    def test_focal_coverage_base_changes_only_when_groundcover_is_disabled(self) -> None:
        region = self.algorithm.RegionShape(
            "coverage_base", ((0.0, 0.0), (120.0, 0.0), (120.0, 90.0), (0.0, 90.0)),
        )
        parameters = self.algorithm.CommunityParameters()
        for enabled_roles, expected_quantiles in (
            (("groundcover_matrix", "focal_structure"), (0.15, 0.5325)),
            (("focal_structure",), (0.0, 0.45)),
        ):
            with self.subTest(enabled_roles=enabled_roles):
                with patch.object(self.algorithm, "_quantile", wraps=self.algorithm._quantile) as quantile:
                    self.algorithm.generate_community(
                        region, parameters, seed=43, enabled_role_keys=enabled_roles,
                    )
                self.assertEqual(quantile.call_count, 2)
                for call, expected in zip(quantile.call_args_list, expected_quantiles):
                    self.assertAlmostEqual(call.args[1], expected)
        self.assertEqual(parameters.groundcover_coverage_percent, 85)

    def test_density_texture_reuses_world_aligned_texels(self) -> None:
        evaluations: list[tuple[float, float]] = []
        texture = self.algorithm.DensityTexture(
            lambda x, y: evaluations.append((x, y)) or (x + y),
            2.0,
        )

        first = texture(0.5, 0.5)
        repeated = texture(0.75, 0.75)
        adjacent = texture(2.5, 0.5)

        self.assertAlmostEqual(first, 0.625)
        self.assertGreater(repeated, first)
        self.assertGreater(adjacent, repeated)
        self.assertEqual(texture.evaluated_texel_count, 6)
        self.assertEqual(len(evaluations), 6)

    def test_cached_noise_preserves_values_across_seeds_and_eviction(self) -> None:
        randomizer = random.Random(3491)
        samples = [
            (randomizer.uniform(-1e7, 1e7), randomizer.uniform(-1e7, 1e7))
            for _ in range(64)
        ]
        samples.extend(((0.0, 0.0), (-1.0, 2.0), (1e12 + 0.25, -1e12)))
        for seed in (17, 43, 2**63 - 1):
            for x, y in samples:
                self.assertEqual(
                    self.algorithm._fbm(x, y, seed),
                    _reference_fbm(x, y, seed),
                )

        cache = self.algorithm._noise_cell
        cache.cache_clear()
        expected = self.algorithm._fbm(-12.25, 38.75, 43)
        for index in range(self.algorithm.NOISE_CELL_CACHE_SIZE + 1):
            cache(index, index + 1, 17)
        self.assertLessEqual(cache.cache_info().currsize, 65_536)
        self.assertEqual(self.algorithm._fbm(-12.25, 38.75, 43), expected)
        cache.cache_clear()

    def test_threshold_sampling_budget_keeps_candidate_grid_and_interior_samples(self) -> None:
        region = self.algorithm.RegionShape(
            "budget_region",
            ((0.0, 0.0), (480.0, 0.0), (480.0, 360.0), (0.0, 360.0)),
            (((120.0, 90.0), (240.0, 90.0), (240.0, 180.0), (120.0, 180.0)),),
        )
        prepared = self.algorithm._prepare_region(region)
        spacing = self.algorithm._field_sample_spacing(region, 14.0)
        samples = self.algorithm._field_samples(region, spacing, prepared)
        self.assertGreater(len(samples), 1024)
        self.assertLess(len(samples), 2200)
        self.assertTrue(
            all(_reference_point_in_region(point, region) for point in samples)
        )

        candidates = tuple(self.algorithm._role_candidates(region, prepared, 19.5, 17))
        with patch.object(self.algorithm, "FIELD_SAMPLE_BUDGET", 24_000):
            old_spacing = self.algorithm._field_sample_spacing(region, 14.0)
            old_samples = self.algorithm._field_samples(region, old_spacing, prepared)
            old_candidates = tuple(
                self.algorithm._role_candidates(region, prepared, 19.5, 17)
            )
        self.assertLess(len(samples), len(old_samples) / 8)
        self.assertEqual(candidates, old_candidates)

    def test_boundary_index_preserves_exact_containment_and_point_output(self) -> None:
        outer = _wavy_ring(0.0, 0.0, 70.0, 512, 11, 0.06)
        hole = _wavy_ring(8.0, -5.0, 16.0, 127, 7, 0.08)
        region = self.algorithm.RegionShape("indexed_region", outer, (hole,))
        prepared = self.algorithm._prepare_region(region)
        points = [*outer[::32], *hole[::16]]
        randomizer = random.Random(3491)
        points.extend(
            (randomizer.uniform(-80.0, 80.0), randomizer.uniform(-80.0, 80.0))
            for _ in range(1200)
        )
        for point in points:
            expected = _reference_point_in_region(point, region)
            actual = self.algorithm._point_in_prepared_region(point, prepared)
            self.assertEqual(actual, expected, point)

        parameters = self.algorithm.CommunityParameters()
        indexed = self.algorithm.generate_community(region, parameters, seed=43)
        indexed_contains = self.algorithm._point_in_prepared_region
        self.algorithm._point_in_prepared_region = (
            lambda point, _prepared: _reference_point_in_region(point, region)
        )
        try:
            reference = self.algorithm.generate_community(region, parameters, seed=43)
        finally:
            self.algorithm._point_in_prepared_region = indexed_contains
        self.assertEqual(indexed, reference)

        largest_bucket = max(len(bucket) for bucket in prepared.outer.buckets)
        self.assertLess(largest_bucket, len(outer) // 4)

        tall_edges = tuple(
            (float(index), 70.0 if index % 2 else -70.0)
            for index in range(512)
        )
        tall_index = self.algorithm._prepare_ring(tall_edges)
        self.assertLessEqual(
            sum(len(bucket) for bucket in tall_index.buckets),
            len(tall_edges) * 8,
        )

    def test_default_dts_profile_resolves_both_roles(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_region_document("region_a", 0.0)],
                "seed": 17,
            },
        )
        profile = load_profile_yaml(
            (
                ROOT
                / "asset-packs"
                / "dts-default"
                / "profiles"
                / "layered-ecological-community-v2.yaml"
            ).read_text(encoding="utf-8")
        )
        snapshot = build_effective_binding_from_profile(self.package.strategy, profile)
        assignments = resolve_asset_assignments(point_set, snapshot, random_seed=17)
        by_role: dict[str, set[str]] = {}
        for assignment in assignments["assignments"]:
            by_role.setdefault(assignment["role_key"], set()).add(assignment["asset_id"])
        self.assertEqual(by_role["groundcover_matrix"], {"SM_A_PinusMugo"})
        self.assertTrue(
            by_role["focal_structure"].issubset({"SM_tree01_01", "SM_Tree0301"})
        )
        self.assertTrue(by_role["focal_structure"])


def _region_document(region_id: str, x: float) -> dict:
    return {
        "id": region_id,
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [x, 0.0, 2.0],
                    [x + 120.0, 0.0, 2.0],
                    [x + 120.0, 90.0, 2.0],
                    [x, 90.0, 2.0],
                ],
            },
            {
                "index": 1,
                "role": "hole",
                "loop": [
                    [x + 45.0, 30.0, 2.0],
                    [x + 75.0, 30.0, 2.0],
                    [x + 75.0, 60.0, 2.0],
                    [x + 45.0, 60.0, 2.0],
                ],
            },
        ],
    }


def _reference_fbm(x: float, y: float, seed: int) -> float:
    def lattice(ix: int, iy: int, noise_seed: int) -> float:
        value = (ix * 374761393 + iy * 668265263 + noise_seed * 2246822519) & 0xFFFFFFFF
        value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
        value ^= value >> 16
        return value / 0xFFFFFFFF

    def smooth(value: float) -> float:
        return value * value * (3.0 - 2.0 * value)

    def lerp(start: float, end: float, amount: float) -> float:
        return start + (end - start) * amount

    def noise(sx: float, sy: float, noise_seed: int) -> float:
        ix, iy = math.floor(sx), math.floor(sy)
        tx, ty = smooth(sx - ix), smooth(sy - iy)
        lower = lerp(lattice(ix, iy, noise_seed), lattice(ix + 1, iy, noise_seed), tx)
        upper = lerp(lattice(ix, iy + 1, noise_seed), lattice(ix + 1, iy + 1, noise_seed), tx)
        return lerp(lower, upper, ty)

    total = 0.0
    amplitude = 1.0
    amplitude_sum = 0.0
    frequency = 1.0
    for octave in range(4):
        total += noise(x * frequency, y * frequency, seed + octave * 7919) * amplitude
        amplitude_sum += amplitude
        amplitude *= 0.52
        frequency *= 2.03
    return total / amplitude_sum


def _load_algorithm_module():
    module_name = "verdflow_layered_ecological_v2_algorithm_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        STRATEGY_ROOT / "algorithm.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load layered ecological v2 algorithm")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _wavy_ring(
    center_x: float,
    center_y: float,
    radius: float,
    vertices: int,
    waves: int,
    amplitude: float,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (
            center_x
            + math.cos(angle) * radius * (1.0 + amplitude * math.sin(waves * angle)),
            center_y
            + math.sin(angle) * radius * (1.0 + amplitude * math.sin(waves * angle)),
        )
        for index in range(vertices)
        for angle in (2.0 * math.pi * index / vertices,)
    )


def _reference_point_in_region(point, region) -> bool:
    return _reference_point_in_ring(point, region.outer) and not any(
        _reference_point_in_ring(point, hole) for hole in region.holes
    )


def _reference_point_in_ring(point, ring) -> bool:
    x, y = point
    inside = False
    previous = len(ring) - 1
    for index, current in enumerate(ring):
        prior = ring[previous]
        if (current[1] > y) != (prior[1] > y):
            crossing_x = (
                (prior[0] - current[0])
                * (y - current[1])
                / ((prior[1] - current[1]) or 1e-12)
                + current[0]
            )
            if x < crossing_x:
                inside = not inside
        previous = index
    return inside


if __name__ == "__main__":
    unittest.main()
