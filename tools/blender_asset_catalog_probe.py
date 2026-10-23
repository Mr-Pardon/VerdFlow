from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]

from verdflow.blender_worker import (  # noqa: E402
    BLENDER_INSPECT_ASSET_LIBRARY_COMMAND,
    BlenderAssetLibraryRegistry,
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
    build_blender_asset_catalog,
    locate_blender,
)
from verdflow.contracts import JobCommandRef, JobStatus  # noqa: E402
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    build_effective_binding_from_profile,
    load_catalog_yaml,
    load_profile_yaml,
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
    resolve_asset_assignments,
)
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.strategy_worker import inspect_strategy_package  # noqa: E402


WORK_ROOT = ROOT / "tests" / ".tmp" / "blender-asset-catalog-probe"
RESULT_PATH = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "blender-asset-catalog-probe.json"
)
FIXTURE_SCRIPT = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "create_blender_asset_library_fixture.py"
)
STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-street-tree"
DTS_ASSET_ROOT = ROOT / "asset-packs" / "dts-default"
COLLECTIONS = ("VF_LowShrub", "VF_MiddleShrub", "VF_Tree")


def main() -> int:
    blender = locate_blender()
    if blender is None:
        raise SystemExit("Blender executable was not found")
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    library_path = WORK_ROOT / "vegetation-library.blend"
    subprocess.run(
        [
            str(blender),
            "--factory-startup",
            "--background",
            "--python",
            str(FIXTURE_SCRIPT),
            "--",
            str(library_path),
        ],
        check=True,
        timeout=60,
    )

    registry = BlenderAssetLibraryRegistry(WORK_ROOT / "provider-registry")
    registration = registry.register("verdflow-fixture", library_path)
    project_path = WORK_ROOT / "probe.verdflow"
    with ProjectStore.create(project_path, "Blender Asset Catalog Probe") as store:
        runtime = JobRuntime(store, lease_duration=timedelta(seconds=20))
        supervisor = BlenderWorkerSupervisor(
            runtime,
            BlenderWorkerConfig(
                blender_executable=blender,
                runtime_root=WORK_ROOT / "runtime",
                ready_timeout_seconds=30.0,
                heartbeat_timeout_seconds=10.0,
                blocking_operation_timeout_seconds=60.0,
                execution_timeout_seconds=90.0,
            ),
            asset_library_registry=registry,
        )
        inspection_job = _submit(
            store,
            runtime,
            registration.library_id,
            list(COLLECTIONS),
            "asset-library-inspection",
        )
        inspected = supervisor.run_once()
        assert inspected is not None
        if inspected.status is not JobStatus.SUCCEEDED:
            raise RuntimeError(repr(inspected.error))
        result = _artifact_json(store, inspected.result_artifact_id)
        inspection = _output_json(
            store,
            result,
            "asset_library_inspection",
        )

        missing_job = _submit(
            store,
            runtime,
            registration.library_id,
            ["VF_Missing"],
            "asset-library-missing-collection",
        )
        missing = supervisor.run_once()
        assert missing is not None

    package = inspect_strategy_package(STRATEGY_ROOT)
    catalog = build_blender_asset_catalog(
        inspection,
        catalog_id="blender_verdflow_fixture",
        display_name="Blender VerdFlow Fixture",
    )
    blender_profile = _blender_profile(catalog)
    updated_catalog = copy.deepcopy(catalog)
    updated_catalog["display_name"] = "Blender VerdFlow Fixture Updated"
    dts_catalog = load_catalog_yaml(
        (DTS_ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    )
    dts_profile = load_profile_yaml(
        (DTS_ASSET_ROOT / "profiles" / "universal-street-tree.yaml").read_text(
            encoding="utf-8"
        )
    )
    with LibraryStore.open(WORK_ROOT / "library") as library:
        library.install_verified_strategy(
            package.root,
            package_id=package.package_id,
            package_version=package.package_version,
            package_hash=package.package_hash,
            display_name=package.strategy["display_name"],
        )
        blender_catalog_info = library.save_asset_catalog(
            prepare_asset_catalog_document(catalog),
            expected_generation=0,
        )
        blender_profile_info = library.save_role_binding_profile(
            prepare_role_binding_profile_document(blender_profile),
            expected_generation=0,
        )
        active_blender_catalog = library.save_asset_catalog(
            prepare_asset_catalog_document(updated_catalog),
            expected_generation=blender_catalog_info.generation,
        )
        library.save_asset_catalog(
            prepare_asset_catalog_document(dts_catalog),
            expected_generation=0,
        )
        library.save_role_binding_profile(
            prepare_role_binding_profile_document(dts_profile),
            expected_generation=0,
        )

    with LibraryStore.open(WORK_ROOT / "library") as library:
        stored_catalog = json.loads(
            library.load_asset_catalog_document(
                "blender",
                active_blender_catalog.catalog_id,
            ).canonical_json
        )
        stored_profile = json.loads(
            library.load_role_binding_profile_document(
                blender_profile_info.profile_id,
            ).canonical_json
        )
        points = _point_set(package.strategy)
        blender_effective = build_effective_binding_from_profile(
            package.strategy,
            stored_profile,
        )
        dts_effective = build_effective_binding_from_profile(
            package.strategy,
            dts_profile,
        )
        blender_assignments = resolve_asset_assignments(
            points,
            blender_effective,
            random_seed=2026080701,
        )
        dts_assignments = resolve_asset_assignments(
            points,
            dts_effective,
            random_seed=2026080701,
        )

    checks = {
        "fixture_created": library_path.is_file(),
        "inspection_succeeded": inspected.status is JobStatus.SUCCEEDED,
        "marked_collection_count": inspection["asset_count"] == 3,
        "unmarked_collection_excluded": all(
            item["datablock_name"] != "VF_NotAnAsset"
            for item in inspection["assets"]
        ),
        "missing_collection_rejected": (
            missing_job.job_id == missing.job_id
            and missing.status is JobStatus.FAILED
            and missing.error is not None
            and "VF_Missing" in missing.error.message
        ),
        "catalog_has_no_local_path": "library_path" not in json.dumps(catalog),
        "direct_observation_recorded": (
            inspection["provider_observation"]["access_mode"]
            == "direct_observed"
        ),
        "library_store_reopened": stored_catalog == updated_catalog,
        "same_point_set_has_two_targets": (
            blender_assignments["target_type"] == "blender"
            and dts_assignments["target_type"] == "dts"
            and {
                item["point_id"] for item in blender_assignments["assignments"]
            }
            == {item["point_id"] for item in dts_assignments["assignments"]}
        ),
        "profile_remains_self_contained_after_catalog_edit": (
            active_blender_catalog.generation == 2
            and blender_effective["asset_use"]["source"]["kind"] == "profile"
        ),
        "blender_assignments_are_collection_assets": {
            item["asset_id"] for item in blender_assignments["assignments"]
        }
        == set(COLLECTIONS),
    }
    payload = {
        "schema": "verdflow.validation.blender-asset-catalog-chain",
        "version": "1",
        "blender_executable": str(blender),
        "blender_version": result.get("blender", {}).get("version"),
        "library_content_hash": inspection["library_content_hash"],
        "inspection_asset_names": [
            item["datablock_name"] for item in inspection["assets"]
        ],
        "blender_catalog": {
            "catalog_id": active_blender_catalog.catalog_id,
            "generation": active_blender_catalog.generation,
            "semantic_hash": active_blender_catalog.semantic_hash,
        },
        "blender_profile": {
            "profile_id": blender_profile_info.profile_id,
            "generation": blender_profile_info.generation,
            "semantic_hash": blender_profile_info.semantic_hash,
        },
        "assignment_counts": {
            "blender": _counts(blender_assignments),
            "dts": _counts(dts_assignments),
        },
        "checks": checks,
        "ok": all(checks.values()),
    }
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if payload["ok"] else 1


def _submit(
    store: ProjectStore,
    runtime: JobRuntime,
    library_id: str,
    required_names: list[str],
    idempotency_key: str,
):
    command = store.put_bytes(
        json.dumps(
            {
                "schema": "verdflow.blender-worker.command",
                "version": "1",
                "operation": "inspect_asset_library",
                "library_id": library_id,
                "required_collection_names": required_names,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        media_type="application/vnd.verdflow.command+json",
        schema_id="verdflow.blender-worker.command",
        schema_version="1",
    )
    return runtime.submit(
        JobCommandRef(
            command_type=BLENDER_INSPECT_ASSET_LIBRARY_COMMAND,
            command_version="1",
            artifact_id=command.artifact_id,
        ),
        idempotency_key=idempotency_key,
    )


def _artifact_json(store: ProjectStore, artifact_id: str | None) -> dict[str, Any]:
    if artifact_id is None:
        raise RuntimeError("Expected result Artifact")
    with store.open_artifact(artifact_id) as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise RuntimeError("Result Artifact must be an object")
    return value


def _output_json(
    store: ProjectStore,
    result: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    descriptor = next(
        item for item in result["output_artifacts"] if item["role"] == role
    )
    return _artifact_json(store, descriptor["artifact_id"])


def _blender_profile(catalog: dict[str, Any]) -> dict[str, Any]:
    target = {
        "target_type": catalog["target_type"],
        "provider_id": catalog["provider_id"],
    }
    assets = {asset["asset_id"]: asset for asset in catalog["assets"]}
    low = _candidate(target, assets["VF_LowShrub"], 0.8, 1.2)
    return {
        "schema": "verdflow.role-binding-profile",
        "version": "2",
        "profile_id": "universal_street_tree.blender_verdflow_fixture",
        "display_name": "Universal Street Tree Blender Fixture",
        "strategy": {
            "strategy_id": "universal_street_tree",
            "strategy_version": "2.0.0",
        },
        "target": target,
        "catalog_hint": {"catalog_id": catalog["catalog_id"]},
        "assignments": {
            "narrow_center_shrub": low,
            "double_left_shrub": low,
            "double_right_shrub": low,
            "outer_shrub": low,
            "middle_shrub": _candidate(
                target, assets["VF_MiddleShrub"], 0.6, 1.0
            ),
            "tree_accent": _candidate(target, assets["VF_Tree"], 0.3, 0.5),
        },
    }


def _candidate(
    target: dict[str, str],
    asset: dict[str, Any],
    minimum: float,
    maximum: float,
) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "asset_id": asset["asset_id"],
                "display_name_snapshot": asset["display_name"],
                "target_asset_ref": {
                    **target,
                    "locator": asset["provider_metadata"],
                },
                "weight": 1,
                "scale": {
                    "mode": "uniform_random",
                    "min": minimum,
                    "max": maximum,
                },
            }
        ]
    }


def _point_set(strategy: dict[str, Any]) -> dict[str, Any]:
    points = []
    for index, role in enumerate(strategy["roles"]):
        position = [float(index), 0.0, 0.0]
        points.append(
            {
                "id": f"point-{index}",
                "region_id": "region-1",
                "role_key": role["role_key"],
                "generated_position": position,
                "terrain_sample": None,
                "resolved_position": position,
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            }
        )
    return {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": strategy["strategy_id"],
            "strategy_version": strategy["strategy_version"],
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(points),
        "points": points,
    }


def _counts(value: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in value["assignments"]:
        asset_id = item["asset_id"]
        result[asset_id] = result.get(asset_id, 0) + 1
    return dict(sorted(result.items()))


if __name__ == "__main__":
    raise SystemExit(main())
