from __future__ import annotations

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
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
]

from verdflow.blender_worker import (  # noqa: E402
    BLENDER_EXTRACT_REGIONS_COMMAND,
    BLENDER_INSPECT_SCENE_COMMAND,
    BLENDER_SAMPLE_ELEVATION_COMMAND,
    BLENDER_WORKER_ADAPTER_ID,
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
    locate_blender,
)
from verdflow.contracts import (  # noqa: E402
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    JobCommandRef,
    JobStatus,
    PointSetKind,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    PointSetImportService,
    RegionImportService,
    SceneImportService,
)
from verdflow.project_store import ProjectStore  # noqa: E402


WORK_ROOT = ROOT / "tests" / ".tmp" / "blender-capability-probe"
RESULT_PATH = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "blender-capability-probe.json"
)
FIXTURE_SCRIPT = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "create_blender_capability_fixture.py"
)


def main() -> int:
    blender = locate_blender()
    if blender is None:
        raise SystemExit("Blender executable was not found")
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    fixture = WORK_ROOT / "capability-fixture.fbx"
    subprocess.run(
        [
            str(blender),
            "--factory-startup",
            "--background",
            "--python",
            str(FIXTURE_SCRIPT),
            "--",
            str(fixture),
        ],
        check=True,
        timeout=60,
    )

    project_path = WORK_ROOT / "probe.verdflow"
    runtime_path = WORK_ROOT / "runtime"
    with ProjectStore.create(project_path, "Capability Probe") as store:
        source_artifact = store.put_file(
            fixture,
            media_type="model/vnd.autodesk.fbx",
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name=fixture.name,
            artifact_id=source_artifact.artifact_id,
        )
        runtime = JobRuntime(
            store,
            lease_duration=timedelta(seconds=20),
        )
        supervisor = BlenderWorkerSupervisor(
            runtime,
            BlenderWorkerConfig(
                blender_executable=blender,
                runtime_root=runtime_path,
                ready_timeout_seconds=30.0,
                heartbeat_timeout_seconds=10.0,
                execution_timeout_seconds=60.0,
            ),
        )

        inspection = _submit(
            store,
            runtime,
            BLENDER_INSPECT_SCENE_COMMAND,
            "inspect_scene",
            {
                "source_id": source.source_id,
                "source_artifact_id": source.artifact_id,
            },
            "capability-inspect",
        )
        inspected = supervisor.run_once()
        assert inspected is not None
        inspection_result = _artifact_json(
            store,
            inspected.result_artifact_id,
        )
        scene_revision = SceneImportService(store).finalize(
            inspection.job_id
        )
        facts = _output_json(
            store,
            inspection_result,
            "material_fact_report",
        )

        rule_pack = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.region-rule-pack",
                    "version": "1",
                    "rules": [
                        {
                            "id": "grass-exact",
                            "label": "Grass",
                            "name_prefix": "grass",
                            "match": {
                                "material_names": ["Grass_Test"],
                            },
                        }
                    ],
                },
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.rule-pack+json",
            schema_id="verdflow.region-rule-pack",
            schema_version="1",
        )
        extraction = _submit(
            store,
            runtime,
            BLENDER_EXTRACT_REGIONS_COMMAND,
            "extract_regions",
            {
                "source_id": source.source_id,
                "source_artifact_id": source.artifact_id,
                "scene_revision_id": scene_revision.scene_revision_id,
                "rule_pack_artifact_id": rule_pack.artifact_id,
            },
            "capability-extract",
        )
        extracted = supervisor.run_once()
        assert extracted is not None
        extraction_result = _artifact_json(
            store,
            extracted.result_artifact_id,
        )
        regions = _output_json(store, extraction_result, "regions")
        metrics = _output_json(
            store,
            extraction_result,
            "geometry_metrics",
        )
        region_set = RegionImportService(store).finalize(
            extraction.job_id
        )
        repeated_region_set = RegionImportService(store).finalize(
            extraction.job_id
        )
        generated_point_set = _fixture_planting(
            store,
            runtime,
            scene_revision.scene_revision_id,
            region_set.region_set_id,
        )
        elevation = _submit(
            store,
            runtime,
            BLENDER_SAMPLE_ELEVATION_COMMAND,
            "sample_elevation",
            {
                "source_id": source.source_id,
                "source_artifact_id": source.artifact_id,
                "scene_revision_id": scene_revision.scene_revision_id,
                "coordinate_frame_id": scene_revision.coordinate_frame_id,
                "parent_point_set_id": (
                    generated_point_set.point_set_id
                ),
                "points_artifact_id": (
                    generated_point_set.point_artifact_id
                ),
                "provider": {
                    "id": BLENDER_WORKER_ADAPTER_ID,
                    "version": "1",
                },
                "options": {
                    "direction": "Z_DOWN",
                    "ray_start_offset": 5.0,
                    "ray_distance": 10.0,
                },
            },
            "capability-elevation",
        )
        elevated = supervisor.run_once()
        assert elevated is not None
        elevation_result = _artifact_json(
            store,
            elevated.result_artifact_id,
        )
        elevated_payload = _output_json(
            store,
            elevation_result,
            "points",
        )
        elevated_point_set = PointSetImportService(
            store
        ).finalize_elevation(elevation.job_id)
        repeated_elevated_point_set = PointSetImportService(
            store
        ).finalize_elevation(elevation.job_id)
        elevated_rows = elevated_payload.get("points", [])
        grass_material = next(
            (
                material
                for material in facts.get("materials", [])
                if material.get("name") == "Grass_Test"
            ),
            {},
        )
        region_materials = {
            material
            for region in regions.get("regions", [])
            for material in region.get("source_materials", [])
        }
        face_counts = sorted(
            item.get("face_count")
            for item in metrics.get("regions", [])
        )
        imported_bbox = facts.get("objects", [{}])[0].get("bbox", [])
        expected_bbox = [0.0, 0.0, 0.0, 5.0, 4.0, 0.0]
        checks = {
            "fixture_created": fixture.is_file(),
            "inspection_succeeded": (
                inspected.status is JobStatus.SUCCEEDED
            ),
            "scene_revision_created": bool(
                scene_revision.scene_revision_id
            ),
            "scene_revision_active": (
                store.project_info().active_scene_revision_id
                == scene_revision.scene_revision_id
            ),
            "grass_fact_has_three_faces": (
                grass_material.get("face_count") == 3
            ),
            "extraction_succeeded": (
                extracted.status is JobStatus.SUCCEEDED
            ),
            "two_connected_islands_exported": (
                regions.get("region_count") == 2
                and metrics.get("region_count") == 2
            ),
            "region_set_finalized": (
                region_set.region_count == 2
                and len(store.list_regions(region_set.region_set_id)) == 2
            ),
            "region_set_finalize_is_idempotent": (
                repeated_region_set.region_set_id
                == region_set.region_set_id
            ),
            "generated_point_set_finalized": (
                generated_point_set.kind is PointSetKind.GENERATED
                and generated_point_set.point_count == 2
            ),
            "elevation_succeeded": (
                elevated.status is JobStatus.SUCCEEDED
            ),
            "elevation_uses_point_set_v2": (
                elevated_payload.get("version") == "2"
                and "elevation" not in elevated_payload
                and any(
                    output.get("role") == "points"
                    and output.get("schema_version") == "2"
                    for output in elevation_result.get(
                        "output_artifacts",
                        [],
                    )
                )
            ),
            "elevation_metrics_are_job_metadata": isinstance(
                elevation_result.get("metrics", {}).get("elevation"),
                dict,
            ),
            "elevation_point_set_finalized": (
                elevated_point_set.kind
                is PointSetKind.ELEVATION_RESOLVED
                and elevated_point_set.parent_point_set_id
                == generated_point_set.point_set_id
            ),
            "elevation_finalize_is_idempotent": (
                repeated_elevated_point_set.point_set_id
                == elevated_point_set.point_set_id
            ),
            "elevation_hit_and_miss_recorded": (
                elevated_point_set.hit_count == 1
                and elevated_point_set.miss_count == 1
            ),
            "elevation_hit_resolved_to_ground": (
                len(elevated_rows) == 2
                and elevated_rows[0].get("terrain_sample", {}).get(
                    "hit"
                )
                is True
                and abs(
                    float(
                        elevated_rows[0]["resolved_position"][2]
                    )
                )
                <= 1e-5
            ),
            "elevation_miss_kept_generated_position": (
                len(elevated_rows) == 2
                and elevated_rows[1].get("terrain_sample", {}).get(
                    "hit"
                )
                is False
                and elevated_rows[1].get("resolved_position")
                == elevated_rows[1].get("generated_position")
            ),
            "connected_pair_merged": face_counts == [1, 2],
            "exact_material_only": region_materials == {"Grass_Test"},
            "texture_evidence_exported": all(
                region.get("source_texture_paths") == []
                for region in regions.get("regions", [])
            ),
            "coordinates_match_fixture": (
                len(imported_bbox) == len(expected_bbox)
                and all(
                    abs(float(actual) - expected) <= 1e-5
                    for actual, expected in zip(
                        imported_bbox,
                        expected_bbox,
                        strict=True,
                    )
                )
            ),
            "unshifted_coordinates": all(
                region.get("coordinate_space") == "source_scene_world"
                and region.get("offset_applied") is False
                for region in regions.get("regions", [])
            ),
            "runtime_control_cleaned": not any(runtime_path.iterdir()),
        }
        report = {
            "schema": "verdflow.blender-capability-probe",
            "version": "1",
            "passed": all(checks.values()),
            "blender_executable": str(blender),
            "checks": checks,
            "source_id": source.source_id,
            "scene_revision_id": scene_revision.scene_revision_id,
            "region_set_id": region_set.region_set_id,
            "generated_point_set_id": generated_point_set.point_set_id,
            "elevated_point_set_id": elevated_point_set.point_set_id,
            "inspection_job_id": inspection.job_id,
            "extraction_job_id": extraction.job_id,
            "elevation_job_id": elevation.job_id,
            "region_count": regions.get("region_count"),
            "region_face_counts": face_counts,
            "materials": [
                {
                    "name": item.get("name"),
                    "face_count": item.get("face_count"),
                }
                for item in facts.get("materials", [])
            ],
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1


def _submit(
    store: ProjectStore,
    runtime: JobRuntime,
    command_type: str,
    operation: str,
    values: dict[str, object],
    idempotency_key: str,
) -> Any:
    schema = "verdflow.blender-worker.command"
    version = "1"
    if command_type == BLENDER_SAMPLE_ELEVATION_COMMAND:
        schema = ELEVATION_SAMPLING_COMMAND_SCHEMA
        version = ELEVATION_SAMPLING_COMMAND_VERSION
    command = store.put_bytes(
        json.dumps(
            {
                "schema": schema,
                "version": version,
                "operation": operation,
                **values,
            },
            sort_keys=True,
        ).encode("utf-8"),
        media_type="application/vnd.verdflow.command+json",
        schema_id=schema,
        schema_version=version,
    )
    return runtime.submit(
        JobCommandRef(
            command_type=command_type,
            command_version="1",
            artifact_id=command.artifact_id,
        ),
        idempotency_key=idempotency_key,
    )


def _fixture_planting(
    store: ProjectStore,
    runtime: JobRuntime,
    scene_revision_id: str,
    region_set_id: str,
) -> Any:
    region_id = store.list_regions(region_set_id)[0].region_id
    points = {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": "probe-strategy",
            "strategy_version": "1",
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": 2,
        "points": [
            {
                "id": "probe_hit",
                "region_id": region_id,
                "role_key": "probe_tree",
                "generated_position": [0.5, 0.5, 2.0],
                "terrain_sample": None,
                "resolved_position": [0.5, 0.5, 2.0],
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            {
                "id": "probe_miss",
                "region_id": region_id,
                "role_key": "probe_tree",
                "generated_position": [10.0, 10.0, 2.0],
                "terrain_sample": None,
                "resolved_position": [10.0, 10.0, 2.0],
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        ],
    }
    command = store.put_bytes(
        json.dumps(
            {
                "scene_revision_id": scene_revision_id,
                "region_set_id": region_set_id,
                "strategy": {
                    "id": "probe-strategy",
                    "version": "1",
                    "package_hash": "sha256:probe-strategy",
                },
                "random_seed": 31,
            },
            sort_keys=True,
        ).encode("utf-8"),
        media_type="application/vnd.verdflow.command+json",
    )
    job = runtime.submit(
        JobCommandRef(
            command_type="verdflow.planting.generate",
            command_version="1",
            artifact_id=command.artifact_id,
        ),
        idempotency_key="capability-fixture-planting",
    )
    claimed = runtime.claim_next(
        "fixture-planting-worker",
        command_types=("verdflow.planting.generate",),
    )
    assert claimed is not None
    point_artifact = store.put_bytes(
        json.dumps(points, sort_keys=True).encode("utf-8"),
        media_type="application/vnd.verdflow.points+json",
        schema_id="verdflow.points",
        schema_version="2",
    )
    package_artifact = store.put_bytes(
        b"probe strategy package",
        media_type="application/vnd.verdflow.strategy-package+zip",
        schema_id="verdflow.strategy-package",
        schema_version="1",
    )
    result = store.put_bytes(
        json.dumps(
            {
                "result_kind": "planting_generation",
                "producer": {
                    "id": "probe-generator",
                    "version": "1",
                },
                "output_artifacts": [
                    {
                        "role": "points",
                        "artifact_id": point_artifact.artifact_id,
                    },
                    {
                        "role": "strategy_package",
                        "artifact_id": package_artifact.artifact_id,
                    },
                ],
            },
            sort_keys=True,
        ).encode("utf-8"),
        media_type="application/json",
    )
    runtime.succeed(
        claimed.lease,
        result_artifact_id=result.artifact_id,
    )
    return PointSetImportService(store).finalize_generated(job.job_id)


def _artifact_json(
    store: ProjectStore,
    artifact_id: str | None,
) -> dict[str, Any]:
    if artifact_id is None:
        raise RuntimeError("Expected an Artifact")
    with store.open_artifact(artifact_id) as stream:
        return json.load(stream)


def _output_json(
    store: ProjectStore,
    result: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    output = next(
        item
        for item in result["output_artifacts"]
        if item["role"] == role
    )
    return _artifact_json(store, output["artifact_id"])


if __name__ == "__main__":
    raise SystemExit(main())
