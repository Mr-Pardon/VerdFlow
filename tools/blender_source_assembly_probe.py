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
    str(ROOT / "tools"),
]

from blender_capability_probe import (  # noqa: E402
    _artifact_json,
    _output_json,
    _submit,
)
from verdflow.blender_worker import (  # noqa: E402
    BLENDER_EXTRACT_REGIONS_COMMAND,
    BLENDER_INSPECT_SCENE_COMMAND,
    BLENDER_SAMPLE_ELEVATION_COMMAND,
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
    locate_blender,
)
from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobStatus,
    PointSetKind,
    SourceAssemblyMemberSpec,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    PointSetImportService,
    RegionImportService,
    SceneImportService,
)
from verdflow.project_store import ProjectStore  # noqa: E402


WORK_ROOT = ROOT / "tests" / ".tmp" / "blender-source-assembly-probe"
RESULT_PATH = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "blender-source-assembly-probe.json"
)
FIXTURE_SCRIPT = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "create_blender_capability_fixture.py"
)
IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)
TRANSLATED = (
    1.0, 0.0, 0.0, 20.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 3.0,
    0.0, 0.0, 0.0, 1.0,
)


def main() -> int:
    blender = locate_blender()
    if blender is None:
        raise SystemExit("Blender executable was not found")
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    fixture = WORK_ROOT / "assembly-member.fbx"
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

    runtime_path = WORK_ROOT / "runtime"
    with ProjectStore.create(
        WORK_ROOT / "probe.verdflow",
        "Source Assembly Probe",
    ) as store:
        source_artifact = store.put_file(
            fixture,
            media_type="model/vnd.autodesk.fbx",
        )
        leaf = store.register_source(
            kind=SourceKind.FBX,
            original_name=fixture.name,
            artifact_id=source_artifact.artifact_id,
        )
        assembly = store.create_source_assembly(
            original_name="identity-and-translated",
            members=(
                SourceAssemblyMemberSpec(leaf.source_id, IDENTITY),
                SourceAssemblyMemberSpec(leaf.source_id, TRANSLATED),
            ),
        )
        members = store.list_source_assembly_members(assembly.source_id)
        member_ids = [member.source_member_id for member in members]
        runtime = JobRuntime(
            store,
            lease_duration=timedelta(seconds=30),
        )
        supervisor = BlenderWorkerSupervisor(
            runtime,
            BlenderWorkerConfig(
                blender_executable=blender,
                runtime_root=runtime_path,
                ready_timeout_seconds=30.0,
                heartbeat_timeout_seconds=10.0,
                execution_timeout_seconds=90.0,
            ),
        )

        inspection = _submit(
            store,
            runtime,
            BLENDER_INSPECT_SCENE_COMMAND,
            "inspect_scene",
            {
                "source_id": assembly.source_id,
                "source_artifact_id": assembly.artifact_id,
            },
            "assembly-inspect",
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
        manifest = _output_json(
            store,
            inspection_result,
            "scene_manifest",
        )
        grass_names = sorted(
            str(material["name"])
            for material in facts.get("materials", [])
            if "grass_test" in str(material.get("name", "")).casefold()
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
                            "match": {"material_names": grass_names},
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
                "source_id": assembly.source_id,
                "source_artifact_id": assembly.artifact_id,
                "scene_revision_id": scene_revision.scene_revision_id,
                "rule_pack_artifact_id": rule_pack.artifact_id,
            },
            "assembly-extract",
        )
        extracted = supervisor.run_once()
        assert extracted is not None
        extraction_result = _artifact_json(
            store,
            extracted.result_artifact_id,
        )
        regions_payload = _output_json(store, extraction_result, "regions")
        metrics_payload = _output_json(
            store,
            extraction_result,
            "geometry_metrics",
        )
        region_set = RegionImportService(store).finalize(extraction.job_id)
        persisted_regions = store.list_regions(region_set.region_set_id)
        generated = _create_generated_points(
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
                "source_id": assembly.source_id,
                "source_artifact_id": assembly.artifact_id,
                "scene_revision_id": scene_revision.scene_revision_id,
                "parent_point_set_id": generated.point_set_id,
                "points_artifact_id": generated.point_artifact_id,
                "direction": "Z_DOWN",
                "ray_start_offset": 5.0,
                "ray_distance": 10.0,
            },
            "assembly-elevation",
        )
        elevated = supervisor.run_once()
        assert elevated is not None
        elevation_result = _artifact_json(
            store,
            elevated.result_artifact_id,
        )
        elevated_payload = _output_json(store, elevation_result, "points")
        elevated_point_set = PointSetImportService(store).finalize_elevation(
            elevation.job_id
        )

        object_rows = facts.get("objects", [])
        rows_by_member = {
            row.get("source_member_id"): row
            for row in object_rows
            if row.get("source_member_id")
        }
        elevation_rows = elevated_payload.get("points", [])
        region_member_ids = [
            row.get("source_member_id")
            for row in regions_payload.get("regions", [])
        ]
        metric_member_ids = [
            row.get("source_member_id")
            for row in metrics_payload.get("regions", [])
        ]
        checks = {
            "inspection_succeeded": inspected.status is JobStatus.SUCCEEDED,
            "manifest_has_two_provenanced_meshes": (
                len(
                    [
                        row
                        for row in manifest.get("objects", [])
                        if row.get("type") == "MESH"
                        and row.get("source_member_id") in member_ids
                    ]
                )
                == 2
            ),
            "identity_bbox_is_unchanged": _bbox_matches(
                rows_by_member.get(member_ids[0], {}).get("bbox"),
                [0.0, 0.0, 0.0, 5.0, 4.0, 0.0],
            ),
            "translated_bbox_uses_member_matrix": _bbox_matches(
                rows_by_member.get(member_ids[1], {}).get("bbox"),
                [20.0, 0.0, 3.0, 25.0, 4.0, 3.0],
            ),
            "extraction_succeeded": extracted.status is JobStatus.SUCCEEDED,
            "four_regions_keep_member_provenance": (
                len(region_member_ids) == 4
                and sorted(set(region_member_ids)) == sorted(member_ids)
                and all(region_member_ids.count(value) == 2 for value in member_ids)
                and region_member_ids == metric_member_ids
            ),
            "region_set_persists_member_provenance": (
                len(persisted_regions) == 4
                and sorted(
                    {row.source_member_id for row in persisted_regions}
                )
                == sorted(member_ids)
            ),
            "elevation_succeeded": elevated.status is JobStatus.SUCCEEDED,
            "elevation_hits_both_members": (
                elevated_point_set.kind is PointSetKind.ELEVATION_RESOLVED
                and elevated_point_set.hit_count == 2
                and [
                    row.get("terrain_sample", {}).get("source_member_id")
                    for row in elevation_rows
                ]
                == member_ids
            ),
            "elevation_uses_assembled_world_heights": (
                len(elevation_rows) == 2
                and abs(float(elevation_rows[0]["resolved_position"][2]))
                <= 1e-5
                and abs(
                    float(elevation_rows[1]["resolved_position"][2]) - 3.0
                )
                <= 1e-5
            ),
            "runtime_control_cleaned": not any(runtime_path.iterdir()),
        }
        report = {
            "schema": "verdflow.blender-source-assembly-probe",
            "version": "1",
            "passed": all(checks.values()),
            "blender_executable": str(blender),
            "checks": checks,
            "assembly_source_id": assembly.source_id,
            "source_member_ids": member_ids,
            "scene_revision_id": scene_revision.scene_revision_id,
            "region_set_id": region_set.region_set_id,
            "elevated_point_set_id": elevated_point_set.point_set_id,
            "elevation_rows": elevation_rows,
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1


def _create_generated_points(
    store: ProjectStore,
    runtime: JobRuntime,
    scene_revision_id: str,
    region_set_id: str,
) -> Any:
    region_by_member: dict[str, str] = {}
    for region in store.list_regions(region_set_id):
        if region.source_member_id is not None:
            region_by_member.setdefault(
                region.source_member_id,
                region.region_id,
            )
    region_ids = list(region_by_member.values())
    if len(region_ids) != 2:
        raise RuntimeError("Assembly probe requires Regions from two members")
    rows = []
    for index, position in enumerate(
        ([0.5, 0.5, 1.0], [20.5, 0.5, 4.0])
    ):
        rows.append(
            {
                "id": f"assembly_probe_{index}",
                "region_id": region_ids[index],
                "role_key": "probe_tree",
                "generated_position": position,
                "terrain_sample": None,
                "resolved_position": position,
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            }
        )
    points = {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": "assembly-probe",
            "strategy_version": "1",
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(rows),
        "points": rows,
    }
    command = store.put_bytes(
        json.dumps(
            {
                "scene_revision_id": scene_revision_id,
                "region_set_id": region_set_id,
                "strategy": {
                    "id": "assembly-probe",
                    "version": "1",
                    "package_hash": "sha256:assembly-probe",
                },
                "random_seed": 13,
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
        idempotency_key="assembly-probe-planting",
    )
    claimed = runtime.claim_next(
        "assembly-probe-generator",
        command_types=("verdflow.planting.generate",),
    )
    assert claimed is not None
    point_artifact = store.put_bytes(
        json.dumps(points, sort_keys=True).encode("utf-8"),
        media_type="application/vnd.verdflow.points+json",
        schema_id="verdflow.points",
        schema_version="2",
    )
    result = store.put_bytes(
        json.dumps(
            {
                "result_kind": "planting_generation",
                "producer": {"id": "assembly-probe", "version": "1"},
                "output_artifacts": [
                    {"role": "points", "artifact_id": point_artifact.artifact_id}
                ],
            },
            sort_keys=True,
        ).encode("utf-8"),
        media_type="application/json",
    )
    runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)
    return PointSetImportService(store).finalize_generated(job.job_id)


def _bbox_matches(actual: object, expected: list[float]) -> bool:
    return (
        isinstance(actual, list)
        and len(actual) == len(expected)
        and all(
            abs(float(left) - right) <= 1e-5
            for left, right in zip(actual, expected, strict=True)
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
