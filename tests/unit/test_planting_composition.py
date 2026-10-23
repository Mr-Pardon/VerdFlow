from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
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
    AssetAssignmentSummary,
    CoordinateReferenceKind,
    JobCommandRef,
    JobStatus,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    PointRoleSummary,
    RegionSummary,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    CompositionOutputBatchRequest,
    CompositionOutputExecutionRequest,
    CompositionOutputExecutionService,
    CompositionOutputExportInput,
    CompositionOutputExportRequest,
    ExportProviderDefinition,
    JobRuntime,
    OutputExportService,
)
from verdflow.project_store import (  # noqa: E402
    PlantingCompositionConflictError,
    ProjectStore,
)


class PlantingCompositionTests(unittest.TestCase):
    def test_elevation_only_plant_run_can_complete_without_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "elevation-only.verdflow",
                "Elevation only",
            ) as store:
                facts = self._facts(store)
                execution = self._review_execution(
                    store,
                    facts,
                    suffix="elevation",
                )
                elevation_job = self._succeeded_job(
                    store,
                    "test.elevation.only",
                )
                point_artifact = store.put_bytes(
                    json.dumps(
                        {
                            "point_count": 1,
                            "points": [
                                {
                                    "id": "point-elevation",
                                    "position": [1.0, 2.0, 3.0],
                                }
                            ],
                        }
                    ).encode(),
                    media_type="application/vnd.verdflow.points+json",
                    schema_id="verdflow.points",
                    schema_version="2",
                )
                elevated = store.create_elevation_point_set(
                    parent_point_set_id=execution.generated_point_set_id or "",
                    producer_job_id=elevation_job,
                    producer_id="test.elevation",
                    producer_version="1",
                    input_hash="sha256:elevation-only",
                    coordinate_frame_id=facts.frame.coordinate_frame_id,
                    point_artifact_id=point_artifact.artifact_id,
                    resolution_policy="strict",
                    point_count=1,
                    hit_count=1,
                    miss_count=0,
                    bounds=(1.0, 2.0, 3.0, 1.0, 2.0, 3.0),
                    roles=(PointRoleSummary("role", 1),),
                )

                completed = store.update_plant_run_execution(
                    replace(
                        execution,
                        status=PlantRunExecutionStatus.SUCCEEDED,
                        stage=PlantRunExecutionStage.COMPLETED,
                        elevation_job_id=elevation_job,
                        elevation_point_set_id=elevated.point_set_id,
                    ),
                    expected_revision=execution.revision,
                )

                self.assertIsNone(completed.export_id)
                self.assertEqual(
                    completed.elevation_point_set_id,
                    elevated.point_set_id,
                )

    def test_schema_21_migration_recovers_completed_elevation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_path = Path(temporary) / "recover-elevation.verdflow"
            with ProjectStore.create(project_path, "Recover elevation") as store:
                facts = self._facts(store)
                execution = self._review_execution(
                    store,
                    facts,
                    suffix="recover",
                )
                elevation_job = self._succeeded_job(
                    store,
                    "test.elevation.recover",
                )
                point_artifact = store.put_bytes(
                    b'{"point_count":1,"points":[]}',
                    media_type="application/vnd.verdflow.points+json",
                    schema_id="verdflow.points",
                    schema_version="2",
                )
                elevated = store.create_elevation_point_set(
                    parent_point_set_id=execution.generated_point_set_id or "",
                    producer_job_id=elevation_job,
                    producer_id="test.elevation",
                    producer_version="1",
                    input_hash="sha256:elevation-recover",
                    coordinate_frame_id=facts.frame.coordinate_frame_id,
                    point_artifact_id=point_artifact.artifact_id,
                    resolution_policy="strict",
                    point_count=1,
                    hit_count=1,
                    miss_count=0,
                    bounds=(1.0, 2.0, 3.0, 1.0, 2.0, 3.0),
                    roles=(PointRoleSummary("role", 1),),
                )
                failed = store.update_plant_run_execution(
                    replace(
                        execution,
                        status=PlantRunExecutionStatus.FAILED,
                        stage=PlantRunExecutionStage.ELEVATION,
                        elevation_job_id=elevation_job,
                        error_code="IntegrityError",
                        error_message=(
                            "CHECK constraint failed: status != 'succeeded' "
                            "OR (stage = 'completed' AND export_id IS NOT NULL)"
                        ),
                    ),
                    expected_revision=execution.revision,
                )
                with store._database.transaction() as connection:
                    connection.execute(
                        "UPDATE schema_metadata SET value = '21' "
                        "WHERE key = 'schema_version'"
                    )
                    connection.execute(
                        "UPDATE projects SET schema_version = 21"
                    )

            with ProjectStore.open(project_path) as migrated:
                recovered = migrated.get_plant_run_execution(
                    failed.execution_id
                )

            self.assertEqual(
                recovered.status,
                PlantRunExecutionStatus.SUCCEEDED,
            )
            self.assertEqual(
                recovered.stage,
                PlantRunExecutionStage.COMPLETED,
            )
            self.assertEqual(
                recovered.elevation_point_set_id,
                elevated.point_set_id,
            )
            self.assertIsNone(recovered.error_code)
            self.assertIsNone(recovered.export_id)

    def test_deferred_output_reuses_elevation_then_exports_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "deferred-output.verdflow",
                "Deferred output",
            ) as store:
                facts = self._facts(store)
                first = self._review_execution(store, facts, suffix="first")
                store.start_plant_run_workflow(
                    first,
                    region_set_id=facts.region_set_id,
                )
                store.commit_current_planting_batch(
                    plant_run_execution_id=first.execution_id,
                    selected_region_ids=("region-1",),
                    selected_asset_ids=("asset-1",),
                    continue_planting=True,
                )
                second = self._review_execution(store, facts, suffix="second")
                store.start_plant_run_workflow(
                    second,
                    region_set_id=facts.region_set_id,
                )
                composition, _ = store.commit_current_planting_batch(
                    plant_run_execution_id=second.execution_id,
                    selected_region_ids=("region-2",),
                    selected_asset_ids=("asset-1",),
                    continue_planting=True,
                )
                batches = store.list_planting_batches()
                plant_runs = _ImmediateElevationPlantRuns(
                    replace(
                        first,
                        status=PlantRunExecutionStatus.SUCCEEDED,
                        stage=PlantRunExecutionStage.COMPLETED,
                        elevation_point_set_id="elevated-first",
                    ),
                    second,
                )
                runtime = JobRuntime(store)
                stages = _QueuedCompositionOutputStages(store, runtime)
                service = CompositionOutputExecutionService(
                    store,
                    runtime,
                    plant_runs,
                    stages,
                )
                request = CompositionOutputExecutionRequest(
                    composition_id=composition.composition_id,
                    composition_generation=composition.generation,
                    elevation_provider_id="test.elevation",
                    export_provider_id="test.export",
                    offset=(1.0, 2.0, 3.0),
                    batches=tuple(
                        CompositionOutputBatchRequest(batch_id=batch.batch_id)
                        for batch in batches
                    ),
                )

                operation = service.submit(request)
                self.assertEqual(operation.status, JobStatus.QUEUED)
                for _ in range(10):
                    service.run_once()
                    if service.get(
                        operation.execution_id
                    ).export_job_id:
                        break

                completed = service.get(operation.execution_id)
                self.assertEqual(
                    completed.status,
                    JobStatus.RUNNING,
                    completed.error_message,
                )
                self.assertIsNotNone(completed.export_job_id)
                self.assertEqual(len(plant_runs.continuations), 1)
                self.assertTrue(
                    all(
                        item.complete_after_elevation
                        for item in plant_runs.continuations
                    )
                )
                self.assertEqual(len(stages.requests), 1)
                self.assertEqual(
                    [item.batch_id for item in stages.requests[0].inputs],
                    [batch.batch_id for batch in batches],
                )

                with self.assertRaisesRegex(RuntimeError, "already active"):
                    service.submit(request)

    def test_discard_current_preview_keeps_saved_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "discard.verdflow",
                "Discard preview",
            ) as store:
                facts = self._facts(store)
                saved = self._review_execution(store, facts, suffix="saved")
                store.start_plant_run_workflow(
                    saved,
                    region_set_id=facts.region_set_id,
                )
                composition, batch = store.commit_current_planting_batch(
                    plant_run_execution_id=saved.execution_id,
                    selected_region_ids=("region-1",),
                    selected_asset_ids=("asset-1",),
                    continue_planting=True,
                )

                draft = self._review_execution(store, facts, suffix="draft")
                store.start_plant_run_workflow(
                    draft,
                    region_set_id=facts.region_set_id,
                )
                store.discard_current_planting_preview(draft.execution_id)

                self.assertIsNone(
                    store.project_workflow_state().plant_run_execution_id
                )
                self.assertEqual(
                    store.current_planting_composition(),
                    composition,
                )
                self.assertEqual(
                    [item.batch_id for item in store.list_planting_batches()],
                    [batch.batch_id],
                )
                self.assertEqual(
                    store.get_plant_run_execution(draft.execution_id).status,
                    PlantRunExecutionStatus.AWAITING_CONFIRMATION,
                )

    def test_batches_reserve_exact_regions_and_removal_releases_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "composition.verdflow",
                "Composition",
            ) as store:
                facts = self._facts(store)
                first = self._review_execution(
                    store,
                    facts,
                    suffix="first",
                )
                store.start_plant_run_workflow(
                    first,
                    region_set_id=facts.region_set_id,
                )
                composition, batch = store.commit_current_planting_batch(
                    plant_run_execution_id=first.execution_id,
                    selected_region_ids=("region-1",),
                    selected_asset_ids=("asset-1",),
                    continue_planting=True,
                )

                self.assertEqual(composition.batch_count, 1)
                self.assertEqual(batch.selected_region_ids, ("region-1",))
                self.assertIsNone(
                    store.project_workflow_state().plant_run_execution_id
                )

                second = self._review_execution(
                    store,
                    facts,
                    suffix="second",
                )
                store.start_plant_run_workflow(
                    second,
                    region_set_id=facts.region_set_id,
                )
                with self.assertRaisesRegex(
                    PlantingCompositionConflictError,
                    "already committed",
                ):
                    store.commit_current_planting_batch(
                        plant_run_execution_id=second.execution_id,
                        selected_region_ids=("region-1",),
                        selected_asset_ids=None,
                        continue_planting=False,
                    )

                store.remove_planting_batch(batch.batch_id)
                composition, replacement = store.commit_current_planting_batch(
                    plant_run_execution_id=second.execution_id,
                    selected_region_ids=("region-1",),
                    selected_asset_ids=None,
                    continue_planting=False,
                )
                self.assertEqual(composition.batch_count, 1)
                self.assertEqual(replacement.ordinal, 0)

                with self.assertRaisesRegex(
                    PlantingCompositionConflictError,
                    "committed Plant Run",
                ):
                    store.discard_current_planting_preview(
                        replacement.plant_run_execution_id
                    )

    def test_replacing_batch_is_atomic_and_keeps_its_ordinal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "replace.verdflow",
                "Replace batch",
            ) as store:
                facts = self._facts(store)
                first = self._review_execution(store, facts, suffix="first")
                store.start_plant_run_workflow(
                    first,
                    region_set_id=facts.region_set_id,
                )
                composition, original = store.commit_current_planting_batch(
                    plant_run_execution_id=first.execution_id,
                    selected_region_ids=("region-1",),
                    selected_asset_ids=("asset-1",),
                    continue_planting=True,
                )

                replacement = self._review_execution(
                    store,
                    facts,
                    suffix="replacement",
                )
                store.start_plant_run_workflow(
                    replacement,
                    region_set_id=facts.region_set_id,
                )
                replaced_composition, replaced = (
                    store.commit_current_planting_batch(
                        plant_run_execution_id=replacement.execution_id,
                        selected_region_ids=("region-1",),
                        selected_asset_ids=None,
                        continue_planting=True,
                        replacing_batch_id=original.batch_id,
                    )
                )

                self.assertEqual(replaced.ordinal, original.ordinal)
                self.assertNotEqual(replaced.batch_id, original.batch_id)
                self.assertEqual(
                    replaced_composition.generation,
                    composition.generation + 1,
                )
                self.assertEqual(store.list_planting_batches(), (replaced,))

                invalid = self._review_execution(
                    store,
                    facts,
                    suffix="invalid",
                )
                store.start_plant_run_workflow(
                    invalid,
                    region_set_id=facts.region_set_id,
                )
                with self.assertRaisesRegex(
                    PlantingCompositionConflictError,
                    "same Regions",
                ):
                    store.commit_current_planting_batch(
                        plant_run_execution_id=invalid.execution_id,
                        selected_region_ids=("region-2",),
                        selected_asset_ids=None,
                        continue_planting=True,
                        replacing_batch_id=replaced.batch_id,
                    )
                self.assertEqual(store.list_planting_batches(), (replaced,))
                self.assertEqual(
                    store.current_planting_composition(),
                    replaced_composition,
                )

    def test_new_material_flow_discards_current_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "reset.verdflow",
                "Reset",
            ) as store:
                facts = self._facts(store)
                execution = self._review_execution(store, facts, suffix="one")
                store.start_plant_run_workflow(
                    execution,
                    region_set_id=facts.region_set_id,
                )
                store.commit_current_planting_batch(
                    plant_run_execution_id=execution.execution_id,
                    selected_region_ids=("region-1",),
                    selected_asset_ids=None,
                    continue_planting=False,
                )
                self.assertIsNotNone(store.current_planting_composition())

                store.start_material_flow_workflow(
                    SimpleNamespace(
                        execution_id="material-revision",
                        region_set_id=None,
                    )
                )
                self.assertIsNone(store.current_planting_composition())
                self.assertEqual(store.list_planting_batches(), ())

    def test_composition_export_accepts_an_ordered_batch_subset(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with ProjectStore.create(
                Path(temporary) / "export.verdflow",
                "Composition export",
            ) as store:
                facts = self._facts(store)
                elevations = []
                for index, suffix in enumerate(("first", "second"), start=1):
                    execution = self._review_execution(
                        store,
                        facts,
                        suffix=suffix,
                    )
                    store.start_plant_run_workflow(
                        execution,
                        region_set_id=facts.region_set_id,
                    )
                    store.commit_current_planting_batch(
                        plant_run_execution_id=execution.execution_id,
                        selected_region_ids=(f"region-{index}",),
                        selected_asset_ids=("asset-1",),
                        continue_planting=True,
                    )
                    elevation_job = self._succeeded_job(
                        store, f"test.elevation.{suffix}"
                    )
                    elevation_artifact = store.put_bytes(
                        json.dumps(
                            {
                                "point_count": 1,
                                "points": [
                                    {
                                        "id": f"point-{suffix}",
                                        "position": [float(index), 0.0, 1.0],
                                    }
                                ],
                            }
                        ).encode(),
                        media_type="application/vnd.verdflow.points+json",
                        schema_id="verdflow.points",
                        schema_version="2",
                    )
                    elevations.append(
                        store.create_elevation_point_set(
                            parent_point_set_id=(
                                execution.generated_point_set_id or ""
                            ),
                            producer_job_id=elevation_job,
                            producer_id="test.elevation",
                            producer_version="1",
                            input_hash=f"sha256:elevation:{suffix}",
                            coordinate_frame_id=facts.frame.coordinate_frame_id,
                            point_artifact_id=elevation_artifact.artifact_id,
                            resolution_policy="strict",
                            point_count=1,
                            hit_count=1,
                            miss_count=0,
                            bounds=(
                                float(index),
                                0.0,
                                1.0,
                                float(index),
                                0.0,
                                1.0,
                            ),
                            roles=(PointRoleSummary("role", 1),),
                        )
                    )

                composition = store.current_planting_composition()
                batches = store.list_planting_batches()
                assert composition is not None
                runtime = JobRuntime(store)
                service = OutputExportService(
                    store,
                    runtime,
                    (
                        ExportProviderDefinition(
                            provider_id="test.dts",
                            provider_version="1",
                            target_type="dts",
                            command_type="test.export",
                            command_version="1",
                            output_media_type="application/xml",
                            output_schema_id="test.xml",
                            output_schema_version="1",
                            supports_asset_selection=True,
                        ),
                    ),
                )
                job = service.submit_composition(
                    CompositionOutputExportRequest(
                        composition_id=composition.composition_id,
                        composition_generation=composition.generation,
                        provider_id="test.dts",
                        offset=(1.0, 2.0, 3.0),
                        inputs=(
                            CompositionOutputExportInput(
                                batch_id=batches[1].batch_id,
                                point_set_id=elevations[1].point_set_id,
                                asset_assignment_set_id=(
                                    batches[1].asset_assignment_set_id
                                ),
                                selected_asset_ids=batches[1].selected_asset_ids,
                            ),
                        ),
                    )
                )
                with store.open_artifact(job.command.artifact_id) as stream:
                    command = json.load(stream)
                self.assertEqual(command["operation"], "export_composition")
                self.assertEqual(command["offset_xyz"], [1.0, 2.0, 3.0])
                self.assertEqual(
                    [item["batch_id"] for item in command["inputs"]],
                    [batches[1].batch_id],
                )
                claimed = runtime.claim_next(
                    "export-worker", command_types=("test.export",)
                )
                assert claimed is not None
                output = store.put_bytes(
                    b"<ModelPointClass />",
                    media_type="application/xml",
                    schema_id="test.xml",
                    schema_version="1",
                )
                result = store.put_bytes(
                    json.dumps(
                        {
                            "result_kind": "output_export",
                            "producer": {"id": "test.dts", "version": "1"},
                            "record_count": 1,
                            "output_artifacts": [
                                {
                                    "role": "export",
                                    "artifact_id": output.artifact_id,
                                }
                            ],
                        }
                    ).encode(),
                    media_type="application/json",
                )
                runtime.succeed(
                    claimed.lease,
                    result_artifact_id=result.artifact_id,
                )
                export = service.finalize(job.job_id)
                self.assertEqual(export.composition_generation, 2)
                self.assertIsNotNone(
                    store.get_latest_composition_export_record(
                        composition.composition_id
                    )
                )

                store.remove_planting_batch(batches[0].batch_id)
                retained = store.get_latest_composition_export_record(
                    composition.composition_id
                )
                self.assertIsNotNone(retained)
                self.assertEqual(
                    [item.export_id for item in store.list_composition_export_records()],
                    [export.export_id],
                )
                store.delete_composition_export_record(export.export_id)
                self.assertEqual(store.list_composition_export_records(), ())

    @classmethod
    def _facts(cls, store: ProjectStore):
        source_artifact = store.put_bytes(
            b"fbx", media_type="model/vnd.autodesk.fbx"
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        frame = store.create_coordinate_frame(
            name="World",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        manifest = store.put_bytes(b"{}", media_type="application/json")
        scene = store.create_scene_revision(
            source_id=source.source_id,
            importer_id="test",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest.artifact_id,
        )
        region_job = cls._succeeded_job(store, "test.regions")
        region_artifact = store.put_bytes(b"{}", media_type="application/json")
        region_set = store.create_region_set(
            scene_revision_id=scene.scene_revision_id,
            producer_job_id=region_job,
            extractor_id="test",
            extractor_version="1",
            input_hash="sha256:regions",
            coordinate_frame_id=frame.coordinate_frame_id,
            geometry_artifact_id=region_artifact.artifact_id,
            metrics_artifact_id=region_artifact.artifact_id,
            regions=tuple(
                RegionSummary(
                    region_id=f"region-{index}",
                    ordinal=index - 1,
                    label=f"Region {index}",
                    tags="",
                    classification="ordinary",
                    strategy_hint=None,
                    footprint_area_xy=100.0,
                    boundary_perimeter_xy=40.0,
                    min_x=float(index),
                    min_y=0.0,
                    min_z=0.0,
                    max_x=float(index + 1),
                    max_y=1.0,
                    max_z=0.0,
                    metrics_json="{}",
                )
                for index in (1, 2)
            ),
        )
        with store._database.transaction() as connection:
            connection.execute(
                """
                UPDATE project_workflow_state
                SET source_id = ?, region_set_id = ?, revision = revision + 1
                """,
                (source.source_id, region_set.region_set_id),
            )
        return SimpleNamespace(
            scene=scene,
            frame=frame,
            region_set_id=region_set.region_set_id,
        )

    @classmethod
    def _review_execution(cls, store: ProjectStore, facts, *, suffix: str):
        point_job = cls._succeeded_job(store, f"test.points.{suffix}")
        point_artifact = store.put_bytes(
            json.dumps(
                {
                    "point_count": 1,
                    "points": [
                        {
                            "id": f"point-{suffix}",
                            "position": [0.0, 0.0, 0.0],
                        }
                    ],
                }
            ).encode(),
            media_type="application/vnd.verdflow.points+json",
            schema_id="verdflow.points",
            schema_version="2",
        )
        planting_run, point_set = store.create_generated_point_set(
            scene_revision_id=facts.scene.scene_revision_id,
            region_set_id=facts.region_set_id,
            producer_job_id=point_job,
            strategy_id=f"strategy.{suffix}",
            strategy_version="1.0.0",
            strategy_package_hash="sha256:" + suffix[0] * 64,
            generator_id="test",
            generator_version="1",
            random_seed=1,
            input_hash=f"sha256:points:{suffix}",
            coordinate_frame_id=facts.frame.coordinate_frame_id,
            point_artifact_id=point_artifact.artifact_id,
            point_count=1,
            bounds=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            roles=(PointRoleSummary("role", 1),),
        )
        package = store.put_bytes(
            suffix.encode(),
            media_type="application/vnd.verdflow.strategy-package+zip",
            schema_id="verdflow.strategy-package",
            schema_version="1",
        )
        package_snapshot = store.create_strategy_package_snapshot(
            planting_run_id=planting_run.planting_run_id,
            package_artifact_id=package.artifact_id,
        )
        asset_use_artifact = store.put_bytes(
            f'{{"asset_use":"{suffix}"}}'.encode(),
            media_type="application/json",
            schema_id="verdflow.target-asset-use-snapshot",
            schema_version="1",
        )
        asset_use = store.create_target_asset_use_snapshot(
            target_type="dts",
            provider_id="test",
            source_kind="catalog",
            source_id=f"catalog-{suffix}",
            source_generation=1,
            source_hash=f"sha256:catalog:{suffix}",
            selected_asset_count=1,
            semantic_hash=f"sha256:asset-use:{suffix}",
            snapshot_artifact_id=asset_use_artifact.artifact_id,
        )
        binding_artifact = store.put_bytes(
            f'{{"binding":"{suffix}"}}'.encode(),
            media_type="application/json",
            schema_id="verdflow.effective-binding-snapshot",
            schema_version="1",
        )
        binding = store.create_effective_binding_snapshot(
            strategy_package_snapshot_id=(
                package_snapshot.strategy_package_snapshot_id
            ),
            target_asset_use_snapshot_id=(
                asset_use.target_asset_use_snapshot_id
            ),
            target_type="dts",
            source_profile_id=None,
            source_profile_hash=None,
            override_applied=False,
            semantic_hash=f"sha256:binding:{suffix}",
            snapshot_artifact_id=binding_artifact.artifact_id,
        )
        assignment_job = cls._succeeded_job(
            store, f"test.assignments.{suffix}"
        )
        assignment_artifact = store.put_bytes(
            json.dumps(
                {
                    "assignments": [
                        {
                            "point_id": f"point-{suffix}",
                            "asset_id": "asset-1",
                        }
                    ]
                }
            ).encode(),
            media_type="application/json",
            schema_id="verdflow.asset-assignment-set",
            schema_version="1",
        )
        assignment = store.create_asset_assignment_set(
            point_set_id=point_set.point_set_id,
            effective_binding_snapshot_id=(
                binding.effective_binding_snapshot_id
            ),
            producer_job_id=assignment_job,
            target_type="dts",
            resolver_id="test",
            resolver_version="1",
            random_seed=1,
            semantic_hash=f"sha256:assignment:{suffix}",
            assignment_artifact_id=assignment_artifact.artifact_id,
            point_count=1,
            assets=(AssetAssignmentSummary("asset-1", 1),),
        )
        request_artifact = store.put_bytes(
            b"{}", media_type="application/json"
        )
        execution = store.create_plant_run_execution(
            idempotency_key=f"execution:{suffix}",
            request_artifact_id=request_artifact.artifact_id,
            request_hash=f"sha256:execution:{suffix}",
        )
        return store.update_plant_run_execution(
            replace(
                execution,
                status=PlantRunExecutionStatus.AWAITING_CONFIRMATION,
                stage=PlantRunExecutionStage.REVIEW,
                generated_point_set_id=point_set.point_set_id,
                asset_assignment_set_id=assignment.asset_assignment_set_id,
            ),
            expected_revision=execution.revision,
        )

    @staticmethod
    def _succeeded_job(store: ProjectStore, command_type: str) -> str:
        command = store.put_bytes(b"{}", media_type="application/json")
        runtime = JobRuntime(store)
        job = runtime.submit(
            JobCommandRef(command_type, "1", command.artifact_id),
            idempotency_key=command_type,
        )
        claimed = runtime.claim_next("test-worker", command_types=(command_type,))
        assert claimed is not None
        result = store.put_bytes(b"{}", media_type="application/json")
        runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)
        return job.job_id


class _ImmediateElevationPlantRuns:
    def __init__(self, *executions):
        self.executions = {
            execution.execution_id: execution for execution in executions
        }
        self.continuations = []

    def get(self, execution_id: str):
        return self.executions[execution_id]

    def confirm(self, execution_id: str, continuation):
        self.continuations.append(continuation)
        current = self.executions[execution_id]
        updated = replace(
            current,
            status=PlantRunExecutionStatus.SUCCEEDED,
            stage=PlantRunExecutionStage.COMPLETED,
            elevation_point_set_id=f"elevated-{execution_id}",
        )
        self.executions[execution_id] = updated
        return updated


class _QueuedCompositionOutputStages:
    def __init__(self, store: ProjectStore, runtime: JobRuntime):
        self.store = store
        self.runtime = runtime
        self.requests = []

    def submit_composition_output_export(self, request, *, idempotency_key=None):
        self.requests.append(request)
        command = self.store.put_bytes(b"{}", media_type="application/json")
        return self.runtime.submit(
            JobCommandRef(
                "test.composition-output",
                "1",
                command.artifact_id,
            ),
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def finalize_output_export(_job_id: str):
        raise AssertionError("queued export must not finalize")


if __name__ == "__main__":
    unittest.main()
