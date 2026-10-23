from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    SourceAssemblyMemberSpec,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    RegionSourceImportService,
    RegionSourceProviderDefinition,
    RegionSourceRequest,
    RegionSourceService,
)
from verdflow.project_store import ProjectStore  # noqa: E402


IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class RegionSourceTests(unittest.TestCase):
    def test_submits_artifact_only_command_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "source.verdflow",
                "Region source",
            ) as store:
                assembly, _ = _assembly(store)
                service = RegionSourceService(
                    store,
                    JobRuntime(store),
                    (
                        RegionSourceProviderDefinition(
                            provider_id="verdflow.geospatial",
                            provider_version="1",
                            command_type="verdflow.geospatial.prepare-regions",
                            command_version="1",
                        ),
                    ),
                )
                request = RegionSourceRequest(
                    source_id=assembly.source_id,
                    provider_id="verdflow.geospatial",
                )

                first = service.submit(request)
                repeated = service.submit(request)
                with store.open_artifact(first.command.artifact_id) as stream:
                    command = json.load(stream)

                self.assertEqual(first.job_id, repeated.job_id)
                self.assertEqual(command["operation"], "prepare_regions")
                self.assertEqual(command["source_id"], assembly.source_id)
                self.assertNotIn("sources", command)
                with self.assertRaisesRegex(ValueError, "local path"):
                    service.submit(
                        RegionSourceRequest(
                            source_id=assembly.source_id,
                            provider_id="verdflow.geospatial",
                            options={"source_path": "C:/unsafe"},
                        )
                    )

    def test_finalizes_scene_and_regions_without_material_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "finalize.verdflow",
                "Region source finalize",
            ) as store:
                assembly, vector_member_id = _assembly(store)
                runtime = JobRuntime(store)
                command_artifact = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.region-source.command",
                            "version": "1",
                            "operation": "prepare_regions",
                            "source_id": assembly.source_id,
                            "source_artifact_id": assembly.artifact_id,
                            "provider": {"id": "verdflow.geospatial", "version": "1"},
                            "options": {},
                        }
                    ).encode("utf-8"),
                    media_type="application/vnd.verdflow.command+json",
                )
                job = runtime.submit(
                    JobCommandRef(
                        command_type="verdflow.geospatial.prepare-regions",
                        command_version="1",
                        artifact_id=command_artifact.artifact_id,
                    ),
                    idempotency_key="region-source-finalize",
                )
                claimed = runtime.claim_next(
                    "fixture-worker",
                    command_types=("verdflow.geospatial.prepare-regions",),
                )
                assert claimed is not None
                source_manifest = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.region-source-manifest",
                            "version": "1",
                            "source_id": assembly.source_id,
                            "alignment": "user_supplied",
                            "crs_validation": "not_performed",
                            "reprojection": "not_performed",
                            "coordinate_frame": {
                                "name": "User-aligned geospatial source",
                                "handedness": "right",
                                "up_axis": "+Z",
                                "linear_unit": "meter",
                                "crs_kind": "unknown",
                                "crs_definition": None,
                                "local_origin": [0, 0, 0],
                                "offset_applied": False,
                            },
                        }
                    ).encode("utf-8"),
                    media_type="application/json",
                )
                regions = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.regions",
                            "version": "1",
                            "coordinate_space": "source_scene_world",
                            "offset_applied": False,
                            "region_count": 1,
                            "regions": [
                                {
                                    "id": "region_0001_001",
                                    "source_member_id": vector_member_id,
                                    "label": "Planting area",
                                    "tags": "",
                                    "bbox": [0, 0, 0, 10, 10, 0],
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    media_type="application/json",
                )
                metrics = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.region-metrics",
                            "version": "1",
                            "coordinate_space": "source_scene_world",
                            "offset_applied": False,
                            "region_count": 1,
                            "regions": [
                                {
                                    "id": "region_0001_001",
                                    "source_member_id": vector_member_id,
                                    "footprint_area_xy": 100,
                                    "boundary_perimeter_xy": 40,
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    media_type="application/json",
                )
                result = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.geospatial-worker.result",
                            "version": "1",
                            "result_kind": "region_source",
                            "source_id": assembly.source_id,
                            "producer": {"id": "verdflow.geospatial", "version": "1"},
                            "output_artifacts": [
                                {"role": "source_manifest", "artifact_id": source_manifest.artifact_id},
                                {"role": "regions", "artifact_id": regions.artifact_id},
                                {"role": "geometry_metrics", "artifact_id": metrics.artifact_id},
                            ],
                        }
                    ).encode("utf-8"),
                    media_type="application/json",
                )
                runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)

                service = RegionSourceImportService(store)
                scene, region_set = service.finalize(job.job_id)
                repeated_scene, repeated_regions = service.finalize(job.job_id)

                self.assertEqual(scene, repeated_scene)
                self.assertEqual(region_set, repeated_regions)
                self.assertEqual(region_set.scene_revision_id, scene.scene_revision_id)
                self.assertEqual(region_set.region_count, 1)
                workflow = store.project_workflow_state()
                self.assertEqual(workflow.source_id, assembly.source_id)
                self.assertEqual(workflow.region_set_id, region_set.region_set_id)
                self.assertIsNone(workflow.scene_preparation_execution_id)
                self.assertIsNone(workflow.material_flow_execution_id)
                with store.open_artifact(scene.scene_manifest_artifact_id) as stream:
                    manifest = json.load(stream)
                self.assertIn("region_source_manifest_artifact_id", manifest["facts"])
                self.assertNotIn("material_fact_report_artifact_id", manifest["facts"])


def _assembly(store: ProjectStore):
    vector_artifact = store.put_bytes(b"{}", media_type="application/geo+json")
    vector = store.register_source(
        kind=SourceKind.GEOJSON,
        original_name="regions.geojson",
        artifact_id=vector_artifact.artifact_id,
    )
    raster_artifact = store.put_bytes(b"tiff", media_type="image/tiff")
    raster = store.register_source(
        kind=SourceKind.GEOTIFF,
        original_name="elevation.tif",
        artifact_id=raster_artifact.artifact_id,
    )
    assembly = store.create_source_assembly(
        original_name="regions + elevation",
        members=(
            SourceAssemblyMemberSpec(vector.source_id, IDENTITY),
            SourceAssemblyMemberSpec(raster.source_id, IDENTITY),
        ),
    )
    member = store.list_source_assembly_members(assembly.source_id)[0]
    return assembly, member.source_member_id


if __name__ == "__main__":
    unittest.main()
