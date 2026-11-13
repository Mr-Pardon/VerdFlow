from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobStatus,
    SourceAssemblyMemberSpec,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    RegionConfirmationWorker,
    RegionSourceImportService,
    RegionSourceProviderDefinition,
    RegionSourceRequest,
    RegionSourceService,
)
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.core.region_import import RegionSetImportError, RegionSetImportService  # noqa: E402


IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class RegionSourceTests(unittest.TestCase):
    def test_selection_spools_exact_large_boundary_and_cleans_unpublished_output(self) -> None:
        from verdflow.core.region_source_import import _region_json

        rings = [
            [[-0.0, 2, 1e-10], [3.25, 4, 0], [3.25, 2, 0], [-0.0, 2, 1e-10]],
            [[1.125, 2, 0], [1.25, 3, 0], [1.25, 2, 0], [1.125, 2, 0]],
        ]
        big = {"id": "r1", "label": "Region 1", "footprint_boundary_xyz": rings}
        small = {"id": "r2", "footprint_boundary_xyz": []}
        header = {
            "schema": "verdflow.regions", "version": "1",
            "coordinate_space": "source_scene_world", "offset_applied": False,
            "region_count": 2,
        }
        payload = _region_json({**header, "regions": [big, small]}).encode("utf-8")
        with (
            tempfile.TemporaryDirectory() as temporary,
            ProjectStore.create(Path(temporary) / "spooled.verdflow", "Spool") as store,
            patch("verdflow.core.region_import._INLINE_REGION_DOCUMENT_BYTES", 0),
            patch("verdflow.core.region_source_import._INLINE_SELECTION_BYTES", 0),
            patch("verdflow.core.region_source_import._BOUNDARY_SPOOL_DOCUMENT_BYTES", 0),
            patch("verdflow.core.region_boundary_spool._BOUNDARY_PREFIX_EVENTS", 12),
            patch("verdflow.core.region_boundary_spool._COPY_CHARACTERS", 8),
        ):
            importer = RegionSourceImportService(store)

            def put(content):
                return store.put_bytes(content, media_type="application/json")

            source = put(payload)
            for kept in (big, small):
                checks = []
                result = importer._filter_region_artifact(
                    source.artifact_id, "verdflow.regions", {kept["id"]}, lambda: checks.append(1),
                )
                with store.open_artifact(result.artifact_id) as output:
                    self.assertEqual(output.read(), _region_json({
                        **header, "region_count": 1, "regions": [kept],
                    }).encode("utf-8"))
                if kept is big:
                    self.assertGreater(len(checks), 8)
            before = store.artifact_count()
            checks = []

            def cancel_during_copy():
                checks.append(1)
                if len(checks) == 2:
                    raise RuntimeError("cancelled during boundary copy")

            with self.assertRaisesRegex(RuntimeError, "cancelled during boundary copy"):
                importer._filter_region_artifact(source.artifact_id, "verdflow.regions", {"r1"}, cancel_during_copy)
            self.assertEqual(store.artifact_count(), before)
            invalid = put(payload + b"{}")
            before = store.artifact_count()
            with self.assertRaises(RegionSetImportError):
                importer._filter_region_artifact(invalid.artifact_id, "verdflow.regions", {"r1"}, None)
            self.assertEqual(store.artifact_count(), before)
            self.assertFalse(list(store.staging_path.glob("region-selection-*")))

    def test_large_region_artifact_checks_suffix_and_projects_only_summary_fields(self) -> None:
        header = {
            "schema": "verdflow.regions", "version": "1",
            "coordinate_space": "source_scene_world", "offset_applied": False,
            "region_count": 1,
        }
        row = {"id": "r1", "bbox": [-1.125, 2, 0, 3, 4, 0], "footprint_boundary_xyz": [[[0, 0, 0]]]}
        with tempfile.TemporaryDirectory() as temporary, ProjectStore.create(
            Path(temporary) / "region-stream.verdflow", "Region stream",
        ) as store:
            importer = RegionSetImportService(store)
            with patch("verdflow.core.region_import._INLINE_REGION_DOCUMENT_BYTES", 0):
                document = {"regions": [row], **header}
                def read(value):
                    artifact = store.put_bytes(value, media_type="application/json")
                    return list(importer.read_rows(
                        artifact.artifact_id, schema="verdflow.regions", label="Region geometry",
                        fields=frozenset({"id", "bbox"}),
                    ))

                payload = json.dumps(document).encode()
                self.assertEqual(read(payload), [{"id": row["id"], "bbox": row["bbox"]}])
                for invalid, error in (
                    (payload + b"{}", "not valid"),
                    (json.dumps({**document, "region_count": 2}).encode(), "count"),
                    (json.dumps({**document, "offset_applied": True}).encode(), "unshifted"),
                ):
                    with self.subTest(error=error), self.assertRaisesRegex(RegionSetImportError, error):
                        read(invalid)

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
                now = datetime(2026, 1, 1, tzinfo=UTC)
                runtime = JobRuntime(store, clock=SimpleNamespace(now=lambda: now))
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
                with (
                    patch.object(store, "create_region_set", side_effect=RegionSetImportError("fixture failure")),
                    self.assertRaisesRegex(RuntimeError, "fixture failure"),
                ):
                    service.finalize(job.job_id)
                self.assertIsNone(store.project_workflow_state().region_set_id)
                self.assertIsNone(store.get_region_set_by_job(job.job_id))
                self.assertEqual(store.list_scene_revisions(), ())
                def submit(identifier: int):
                    # Deliberately reverse ID order while keeping one clock tick.
                    ids = SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=f"{identifier:032x}"))
                    with patch("verdflow.project_store.jobs.uuid", ids):
                        return service.submit_confirmation(runtime, job.job_id,
                            included_region_ids=("region_0001_001",), expected_region_set_id=None)

                worker = RegionConfirmationWorker(runtime)
                queued = submit(9)
                self.assertIs(queued.status, JobStatus.QUEUED)
                self.assertEqual(submit(8).job_id, queued.job_id)
                self.assertIsNone(store.project_workflow_state().region_set_id)
                with patch.object(store, "create_region_set", side_effect=RegionSetImportError("fixture failure")):
                    self.assertIs(worker.run_once().status, JobStatus.FAILED)
                self.assertIsNone(store.project_workflow_state().region_set_id)
                self.assertEqual(store.list_scene_revisions(), ())
                retry = submit(7)
                self.assertNotEqual(retry.job_id, queued.job_id)
                self.assertEqual(submit(6).job_id, retry.job_id)
                worker.request_cancel(retry.job_id)
                self.assertIs(worker.run_once().status, JobStatus.CANCELLED)
                self.assertIsNone(store.project_workflow_state().region_set_id)
                confirmation = submit(5)
                self.assertNotIn(confirmation.job_id, {queued.job_id, retry.job_id})
                abandoned = runtime.claim_next("stopped-owner", only_job_id=confirmation.job_id)
                self.assertIsNotNone(abandoned)
                now += timedelta(minutes=1)
                self.assertEqual(runtime.recover_expired().requeued_count, 1)
                self.assertEqual(worker.run_once().job_id, confirmation.job_id)
                self.assertIs(runtime.get(confirmation.job_id).status, JobStatus.SUCCEEDED)
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
                self.assertEqual(manifest["facts"]["region_confirmation_job_id"], confirmation.job_id)
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
