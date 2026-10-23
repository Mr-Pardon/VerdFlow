from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    JobCommandRef,
    SourceAssemblyMemberSpec,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    RegionExtractionImportError,
    RegionExtractionImportService,
    RegionExtractionProviderDefinition,
    RegionExtractionRequest,
    RegionExtractionService,
)
from verdflow.project_store import ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class RegionExtractionImportServiceTests(unittest.TestCase):
    def test_submits_exact_rule_pack_with_stable_idempotency(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "extract-request.verdflow",
                "Region extraction request",
            ) as store:
                source_artifact = store.put_bytes(
                    b"fake fbx",
                    media_type="model/vnd.autodesk.fbx",
                )
                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="scene.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                frame = store.create_coordinate_frame(
                    name="Source scene world",
                    handedness="right-handed",
                    up_axis="+Z",
                    linear_unit="meter",
                    crs_kind=CoordinateReferenceKind.UNKNOWN,
                )
                manifest = store.put_bytes(
                    b'{"schema":"verdflow.scene-manifest","version":"1"}',
                    media_type="application/vnd.verdflow.scene-manifest+json",
                    schema_id="verdflow.scene-manifest",
                    schema_version="1",
                )
                scene = store.create_scene_revision(
                    source_id=source.source_id,
                    importer_id="test-importer",
                    importer_version="1",
                    coordinate_frame_id=frame.coordinate_frame_id,
                    scene_manifest_artifact_id=manifest.artifact_id,
                )
                runtime = JobRuntime(store)
                service = RegionExtractionService(
                    store,
                    runtime,
                    (
                        RegionExtractionProviderDefinition(
                            provider_id="test-blender",
                            provider_version="1",
                            command_type="verdflow.blender.extract-regions",
                            command_version="1",
                        ),
                    ),
                )
                request = RegionExtractionRequest(
                    scene_revision_id=scene.scene_revision_id,
                    provider_id="test-blender",
                    rule_pack={
                        "schema": "verdflow.region-rule-pack",
                        "version": "1",
                        "rules": [
                            {
                                "id": "grass-exact",
                                "label": "Grass",
                                "name_prefix": "grass",
                                "match": {"material_names": ["Grass_Test"]},
                            }
                        ],
                    },
                )

                first = service.submit(request)
                repeated = service.submit(request)
                with store.open_artifact(first.command.artifact_id) as stream:
                    command = json.load(stream)

                self.assertEqual(first.job_id, repeated.job_id)
                self.assertEqual(command["scene_revision_id"], scene.scene_revision_id)
                self.assertEqual(command["source_id"], source.source_id)
                self.assertEqual(command["operation"], "extract_regions")
                self.assertEqual(
                    store.get_artifact(command["rule_pack_artifact_id"]).schema_id,
                    "verdflow.region-rule-pack",
                )

                with self.assertRaisesRegex(ValueError, "local path"):
                    service.submit(
                        RegionExtractionRequest(
                            scene_revision_id=scene.scene_revision_id,
                            provider_id="test-blender",
                            rule_pack=request.rule_pack,
                            import_settings={"cache_path": "C:/unsafe"},
                        )
                    )

    def test_finalizes_queryable_region_set_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "regions.verdflow",
                "Regions",
            ) as store:
                job_id = self._completed_extraction(store)
                service = RegionExtractionImportService(store)

                first = service.finalize(job_id)
                second = service.finalize(job_id)
                regions = store.list_regions(first.region_set_id)

                self.assertEqual(first.region_set_id, second.region_set_id)
                self.assertEqual(first.region_count, 2)
                self.assertEqual(
                    [region.region_id for region in regions],
                    ["grass_0001", "grass_0002"],
                )
                self.assertEqual(regions[0].footprint_area_xy, 2.0)
                self.assertEqual(regions[1].classification, "street_tree")
                self.assertEqual(
                    store.get_region_set(first.region_set_id),
                    first,
                )

    def test_rejects_shifted_region_geometry(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "shifted-regions.verdflow",
                "Shifted regions",
            ) as store:
                job_id = self._completed_extraction(
                    store,
                    offset_applied=True,
                )

                with self.assertRaisesRegex(
                    RegionExtractionImportError,
                    "unshifted coordinates",
                ):
                    RegionExtractionImportService(store).finalize(job_id)

    def test_rejects_region_without_positive_xy_footprint(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "zero-area-regions.verdflow",
                "Zero area regions",
            ) as store:
                job_id = self._completed_extraction(
                    store,
                    first_footprint_area=0.0,
                )

                with self.assertRaisesRegex(
                    RegionExtractionImportError,
                    "footprint_area_xy must be positive",
                ):
                    RegionExtractionImportService(store).finalize(job_id)

    def test_preserves_assembly_member_provenance(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "assembly-regions.verdflow",
                "Assembly regions",
            ) as store:
                job_id = self._completed_extraction(store, assembly=True)

                region_set = RegionExtractionImportService(store).finalize(
                    job_id
                )
                regions = store.list_regions(region_set.region_set_id)

                self.assertEqual(len(regions), 2)
                self.assertTrue(
                    all(region.source_member_id for region in regions)
                )

    def test_rejects_assembly_region_without_member_provenance(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "missing-member.verdflow",
                "Missing member",
            ) as store:
                job_id = self._completed_extraction(
                    store,
                    assembly=True,
                    include_source_member=False,
                )

                with self.assertRaisesRegex(
                    RegionExtractionImportError,
                    "lacks source_member_id",
                ):
                    RegionExtractionImportService(store).finalize(job_id)

    @staticmethod
    def _completed_extraction(
        store: ProjectStore,
        *,
        offset_applied: bool = False,
        assembly: bool = False,
        include_source_member: bool = True,
        first_footprint_area: float = 2.0,
    ) -> str:
        source_artifact = store.put_bytes(
            b"fake fbx",
            media_type="model/vnd.autodesk.fbx",
        )
        leaf_source = store.register_source(
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        source = leaf_source
        source_member_id = None
        if assembly:
            source = store.create_source_assembly(
                original_name="scene assembly",
                members=(
                    SourceAssemblyMemberSpec(
                        source_id=leaf_source.source_id,
                        source_to_scene_matrix_row_major=(
                            1.0, 0.0, 0.0, 0.0,
                            0.0, 1.0, 0.0, 0.0,
                            0.0, 0.0, 1.0, 0.0,
                            0.0, 0.0, 0.0, 1.0,
                        ),
                    ),
                ),
            )
            if include_source_member:
                source_member_id = store.list_source_assembly_members(
                    source.source_id
                )[0].source_member_id
        frame = store.create_coordinate_frame(
            name="Source scene world",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        scene_manifest = store.put_bytes(
            b'{"schema":"verdflow.scene-manifest","version":"1"}',
            media_type="application/vnd.verdflow.scene-manifest+json",
            schema_id="verdflow.scene-manifest",
            schema_version="1",
        )
        scene = store.create_scene_revision(
            source_id=source.source_id,
            importer_id="test-importer",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=scene_manifest.artifact_id,
        )
        rule_pack = store.put_bytes(
            b'{"rules":[{"id":"grass"}]}',
            media_type="application/json",
        )
        command = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.blender-worker.command",
                    "version": "1",
                    "operation": "extract_regions",
                    "source_id": source.source_id,
                    "source_artifact_id": source.artifact_id,
                    "scene_revision_id": scene.scene_revision_id,
                    "rule_pack_artifact_id": rule_pack.artifact_id,
                },
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.command+json",
        )
        runtime = JobRuntime(store)
        job = runtime.submit(
            JobCommandRef(
                command_type="verdflow.blender.extract-regions",
                command_version="1",
                artifact_id=command.artifact_id,
            ),
            idempotency_key=(
                f"region-import-{offset_applied}-{assembly}-"
                f"{include_source_member}"
            ),
        )
        claimed = runtime.claim_next(
            "test-worker",
            command_types=("verdflow.blender.extract-regions",),
        )
        assert claimed is not None
        geometry = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.regions",
                    "version": "1",
                    "coordinate_space": "source_scene_world",
                    "offset_applied": offset_applied,
                    "region_count": 2,
                    "regions": [
                        {
                            "id": "grass_0001",
                            "label": "Grass",
                            "tags": "",
                            "bbox": [0, 0, 0, 2, 1, 0],
                            **(
                                {"source_member_id": source_member_id}
                                if source_member_id
                                else {}
                            ),
                        },
                        {
                            "id": "grass_0002",
                            "label": "Street",
                            "tags": "street",
                            "greenbelt_type": "street_tree",
                            "strategy_hint": "universal_street_tree",
                            "bbox": [4, 0, 0, 5, 1, 0],
                            **(
                                {"source_member_id": source_member_id}
                                if source_member_id
                                else {}
                            ),
                        },
                    ],
                }
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.regions+json",
            schema_id="verdflow.regions",
            schema_version="1",
        )
        metrics = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.region-metrics",
                    "version": "1",
                    "coordinate_space": "source_scene_world",
                    "offset_applied": False,
                    "region_count": 2,
                    "regions": [
                        {
                            "id": "grass_0001",
                            "footprint_area_xy": first_footprint_area,
                            "boundary_perimeter_xy": 6.0,
                            **(
                                {"source_member_id": source_member_id}
                                if source_member_id
                                else {}
                            ),
                        },
                        {
                            "id": "grass_0002",
                            "footprint_area_xy": 1.0,
                            "boundary_perimeter_xy": 4.0,
                            **(
                                {"source_member_id": source_member_id}
                                if source_member_id
                                else {}
                            ),
                        },
                    ],
                }
            ).encode("utf-8"),
            media_type="application/json",
            schema_id="verdflow.region-metrics",
            schema_version="1",
        )
        result = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.blender-worker.result",
                    "version": "1",
                    "status": "succeeded",
                    "result_kind": "region_extraction",
                    "source_id": source.source_id,
                    "producer": {
                        "id": "test-extractor",
                        "version": "1",
                    },
                    "output_artifacts": [
                        {
                            "role": "regions",
                            "artifact_id": geometry.artifact_id,
                        },
                        {
                            "role": "geometry_metrics",
                            "artifact_id": metrics.artifact_id,
                        },
                    ],
                }
            ).encode("utf-8"),
            media_type="application/json",
            schema_id="verdflow.blender-worker.result",
            schema_version="1",
        )
        runtime.succeed(
            claimed.lease,
            result_artifact_id=result.artifact_id,
        )
        return job.job_id


if __name__ == "__main__":
    unittest.main()
