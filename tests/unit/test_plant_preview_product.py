from __future__ import annotations

import json
from io import BytesIO
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
    str(ROOT / "apps" / "studio-service" / "src"),
    str(ROOT / "apps" / "dts-live-connector" / "src"),
    str(ROOT / "apps" / "local-api" / "src"),
]

from verdflow.blender_worker import BLENDER_WORKER_ADAPTER_ID  # noqa: E402
from verdflow.contracts import (  # noqa: E402
    CompositionOutputExecutionStage,
    JobStatus,
    MaterialFlowExecutionStatus,
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    PlantRunReviewPolicy,
)
from verdflow.dts_adapter import DTS_ADAPTER_ID  # noqa: E402
from verdflow.core import PlantRunExecutionRequest, PlantRunExecutionService  # noqa: E402
from verdflow.planting import canonical_json_bytes, semantic_hash  # noqa: E402
from verdflow.local_api import LocalApiServer  # noqa: E402
from verdflow.system_host import (  # noqa: E402
    ExportDownloadInfo,
    PlantExportRequest,
    PlantExportSubmission,
    PlantPreviewOutputRequest,
    PlantPreviewReexportInfo,
    PlantPreviewRequest,
    PlantPreviewSubmission,
    PlantingBatchCommitRequest,
    PlantingBatchSummary,
    PlantingCompositionExportBatchRequest,
    PlantingCompositionExportRequest,
    PlantingCompositionSummary,
    CompositionExportOperationInfo,
    ProjectXmlOutputInfo,
    SystemHost,
    SystemHostConfig,
    SystemHostError,
)
from verdflow.system_host.planting_routes import (  # noqa: E402
    resolve_plant_output_route,
    resolve_region_extraction_provider,
)


class PlantPreviewProductTests(unittest.TestCase):
    def test_preview_forces_review_and_freezes_a_retry_stable_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                execution = _execution()
                request = _preview_request()
                with patch.object(
                    system,
                    "_submit_current_plant_run_execution",
                    return_value=execution,
                ) as submit:
                    first = system.submit_plant_preview(
                        "session_1",
                        request,
                        idempotency_key="preview-request-1",
                    )
                    second = system.submit_plant_preview(
                        "session_1",
                        request,
                        idempotency_key="preview-request-1",
                    )
                self.assertEqual(first.random_seed, second.random_seed)
                submitted = submit.call_args_list[0].args[1]
                self.assertIs(
                    submitted.review_policy,
                    PlantRunReviewPolicy.REQUIRED,
                )
                self.assertIsNone(submitted.elevation_provider_id)
                self.assertIsNone(submitted.export_provider_id)
                self.assertIsNone(submitted.offset)

    def test_preview_input_restores_temporary_binding_overrides(self) -> None:
        request = replace(
            PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=0,
                target_type="dts",
                catalog_id="catalog_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
            ),
            random_seed=17,
            generation_args={
                "strategy_parameters": {
                    "overall_density": 0.26,
                    "canopy_density": 3.6,
                }
            },
            binding_overrides={
                "tree_accent": {
                    "candidates": [{"asset_id": "SM_Oak_1D"}],
                }
            },
        )
        document = PlantRunExecutionService._document(request)
        runtime = _QueryRuntime(SimpleNamespace(
            get_plant_run_execution=lambda _execution_id: replace(
                _execution(), request_hash=semantic_hash(document),
            ),
            open_artifact=lambda _artifact_id: BytesIO(canonical_json_bytes(document)),
        ))
        runtime.call = Mock(side_effect=AssertionError("Input reads must not use the owner"))
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    restored = system.get_plant_preview_input(
                        "session_1",
                        "plant_run_1",
                    )
                finally:
                    system._projects.pop("session_1", None)

        self.assertIsNone(restored.profile_id)
        self.assertEqual(restored.random_seed, 17)
        self.assertEqual(restored.generation_args, request.generation_args)
        self.assertEqual(restored.binding_overrides, request.binding_overrides)

    def test_output_route_comes_from_scene_lineage_and_assignment_target(
        self,
    ) -> None:
        route = resolve_plant_output_route(
            scene_importer_id=BLENDER_WORKER_ADAPTER_ID,
            target_type="dts",
        )
        self.assertEqual(route.elevation_provider_id, BLENDER_WORKER_ADAPTER_ID)
        self.assertEqual(route.export_provider_id, DTS_ADAPTER_ID)
        geospatial = resolve_plant_output_route(
            scene_importer_id="verdflow.geospatial",
            target_type="dts",
        )
        self.assertEqual(
            geospatial.elevation_provider_id,
            "verdflow.geospatial",
        )
        self.assertEqual(geospatial.export_provider_id, DTS_ADAPTER_ID)
        with self.assertRaisesRegex(ValueError, "scene importer"):
            resolve_plant_output_route(
                scene_importer_id="unknown",
                target_type="dts",
            )

    def test_region_extraction_route_comes_from_scene_lineage(self) -> None:
        provider_id = resolve_region_extraction_provider(
            scene_importer_id=BLENDER_WORKER_ADAPTER_ID,
        )
        self.assertEqual(provider_id, BLENDER_WORKER_ADAPTER_ID)
        with self.assertRaisesRegex(ValueError, "scene importer"):
            resolve_region_extraction_provider(
                scene_importer_id="unknown",
            )

    def test_batch_commit_freezes_regions_and_continues_existing_run(
        self,
    ) -> None:
        execution = _execution(
            generated_point_set_id="points_1",
            asset_assignment_set_id="assignments_1",
        )
        store = SimpleNamespace(
            commit_current_planting_batch=Mock(),
            get_scene_revision=lambda _scene_id: SimpleNamespace(
                importer_id=BLENDER_WORKER_ADAPTER_ID
            ),
        )
        core = SimpleNamespace(
            store=store,
            get_plant_run_execution=lambda _execution_id: execution,
            get_plant_run_execution_request=lambda _execution_id: (
                SimpleNamespace(
                    selected_region_ids=("region_1",),
                    scene_revision_id="scene_1",
                    target_type="dts",
                )
            ),
            confirm_plant_run_execution=Mock(),
        )
        summary = SimpleNamespace(composition_id="composition_1")
        runtime = _QueryRuntime(core)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    with patch.object(
                        system,
                        "get_current_planting_composition",
                        return_value=summary,
                    ):
                        result = system.commit_plant_preview_batch(
                            "session_1",
                            execution.execution_id,
                            PlantingBatchCommitRequest(
                                selected_asset_ids=("asset_1",),
                                continue_planting=True,
                            ),
                        )
                finally:
                    system._projects.pop("session_1", None)

        self.assertIs(result, summary)
        store.commit_current_planting_batch.assert_called_once_with(
            plant_run_execution_id=execution.execution_id,
            selected_region_ids=("region_1",),
            selected_asset_ids=("asset_1",),
            continue_planting=True,
            replacing_batch_id=None,
        )
        core.confirm_plant_run_execution.assert_not_called()

    def test_discard_preview_cancels_only_the_current_uncommitted_run(
        self,
    ) -> None:
        execution = _execution(
            generated_point_set_id="points_1",
            asset_assignment_set_id="assignments_1",
        )
        store = SimpleNamespace(
            project_workflow_state=lambda: SimpleNamespace(
                plant_run_execution_id=execution.execution_id
            ),
            list_planting_batches=lambda: (),
            discard_current_planting_preview=Mock(),
        )
        core = SimpleNamespace(
            store=store,
            get_plant_run_execution=lambda _execution_id: execution,
            cancel_plant_run_execution=Mock(),
        )
        summary = SimpleNamespace(composition_id="composition_1")
        runtime = _QueryRuntime(core)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    with patch.object(
                        system,
                        "get_current_planting_composition",
                        return_value=summary,
                    ):
                        result = system.discard_plant_preview(
                            "session_1",
                            execution.execution_id,
                        )
                finally:
                    system._projects.pop("session_1", None)

        self.assertIs(result, summary)
        core.cancel_plant_run_execution.assert_called_once_with(
            execution.execution_id
        )
        store.discard_current_planting_preview.assert_called_once_with(
            execution.execution_id
        )

    def test_batch_commit_can_atomically_replace_a_saved_batch(self) -> None:
        execution = _execution(
            generated_point_set_id="points_1",
            asset_assignment_set_id="assignments_1",
        )
        replaced = SimpleNamespace(
            batch_id="batch_old",
            plant_run_execution_id="plant_run_old",
        )
        store = SimpleNamespace(
            list_planting_batches=lambda: (replaced,),
            commit_current_planting_batch=Mock(),
        )
        core = SimpleNamespace(
            store=store,
            get_plant_run_execution=lambda execution_id: (
                execution
                if execution_id == execution.execution_id
                else SimpleNamespace(status=PlantRunExecutionStatus.SUCCEEDED)
            ),
            get_plant_run_execution_request=lambda _execution_id: (
                SimpleNamespace(selected_region_ids=("region_1",))
            ),
        )
        summary = SimpleNamespace(composition_id="composition_1")
        runtime = _QueryRuntime(core)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    with patch.object(
                        system,
                        "get_current_planting_composition",
                        return_value=summary,
                    ):
                        result = system.commit_plant_preview_batch(
                            "session_1",
                            execution.execution_id,
                            PlantingBatchCommitRequest(
                                replacing_batch_id=replaced.batch_id,
                            ),
                        )
                finally:
                    system._projects.pop("session_1", None)

        self.assertIs(result, summary)
        store.commit_current_planting_batch.assert_called_once_with(
            plant_run_execution_id=execution.execution_id,
            selected_region_ids=("region_1",),
            selected_asset_ids=None,
            continue_planting=False,
            replacing_batch_id="batch_old",
        )

    def test_composition_export_resolves_route_for_selected_batches(
        self,
    ) -> None:
        composition = SimpleNamespace(
            composition_id="composition_1",
            generation=2,
            scene_revision_id="scene_1",
        )
        batches = (
            SimpleNamespace(
                batch_id="batch_1",
                plant_run_execution_id="run_1",
                asset_assignment_set_id="assignments_1",
                selected_asset_ids=("asset_1",),
            ),
            SimpleNamespace(
                batch_id="batch_2",
                plant_run_execution_id="run_2",
                asset_assignment_set_id="assignments_2",
                selected_asset_ids=None,
            ),
        )
        store = SimpleNamespace(
            current_planting_composition=lambda: composition,
            list_planting_batches=lambda: batches,
            get_scene_revision=lambda _scene_id: SimpleNamespace(
                importer_id=BLENDER_WORKER_ADAPTER_ID
            ),
            get_asset_assignment_set=lambda _assignment_id: SimpleNamespace(
                target_type="dts"
            ),
        )
        operation = SimpleNamespace(
            execution_id="composition_output_1",
            composition_id="composition_1",
            composition_generation=2,
            stage=CompositionOutputExecutionStage.ELEVATION,
            export_job_id=None,
            status=JobStatus.QUEUED,
            export_id=None,
            error_message=None,
        )
        core = SimpleNamespace(
            store=store,
            submit_composition_output_execution=Mock(return_value=operation),
        )
        runtime = _QueryRuntime(core)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    result = system.submit_planting_composition_export(
                        "session_1",
                        PlantingCompositionExportRequest(
                            offset=(1.0, 2.0, 3.0),
                            batches=(
                                PlantingCompositionExportBatchRequest(
                                    batch_id="batch_2",
                                ),
                            ),
                        ),
                    )
                finally:
                    system._projects.pop("session_1", None)

        self.assertEqual(result.composition_generation, 2)
        self.assertEqual(result.operation_id, "composition_output_1")
        request = core.submit_composition_output_execution.call_args.args[0]
        self.assertEqual(request.elevation_provider_id, BLENDER_WORKER_ADAPTER_ID)
        self.assertEqual(request.export_provider_id, DTS_ADAPTER_ID)
        self.assertEqual(request.offset, (1.0, 2.0, 3.0))
        self.assertEqual(
            [item.batch_id for item in request.batches],
            ["batch_2"],
        )

    def test_composition_export_poll_reads_durable_operation(self) -> None:
        operation = SimpleNamespace(
            execution_id="composition_output_1",
            composition_id="composition_1",
            composition_generation=4,
            stage=CompositionOutputExecutionStage.EXPORT,
            export_job_id="job_composition",
            status=JobStatus.RUNNING,
            export_id=None,
            error_message=None,
        )
        core = SimpleNamespace(
            get_composition_output_execution=Mock(return_value=operation),
        )
        runtime = _QueryRuntime(core)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    result = system.get_planting_composition_export(
                        "session_1",
                        "composition_output_1",
                    )
                    self.assertEqual(result.composition_generation, 4)
                    self.assertEqual(result.stage, "export")
                    core.get_composition_output_execution.assert_called_once_with(
                        "composition_output_1"
                    )
                finally:
                    system._projects.pop("session_1", None)

    def test_material_revision_rejects_an_active_material_flow(self) -> None:
        workflow = SimpleNamespace(
            source_id="source_1",
            material_flow_execution_id="material_flow_1",
            plant_run_execution_id=None,
        )
        store = SimpleNamespace(
            project_workflow_state=lambda: workflow,
            get_material_flow_execution=lambda _execution_id: SimpleNamespace(
                status=MaterialFlowExecutionStatus.RUNNING,
            ),
        )
        runtime = _QueryRuntime(SimpleNamespace(store=store))
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    with self.assertRaisesRegex(
                        SystemHostError,
                        "active material flow",
                    ):
                        system.revise_current_materials(
                            "session_1",
                            SimpleNamespace(),
                        )
                finally:
                    system._projects.pop("session_1", None)

    def test_direct_export_forces_automatic_execution_and_product_routes(
        self,
    ) -> None:
        execution = _execution()
        runtime = _QueryRuntime(_PreviewStore(execution))
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    with patch.object(
                        system,
                        "_submit_current_plant_run_execution",
                        return_value=execution,
                    ) as submit:
                        result = system.submit_plant_export(
                            "session_1",
                            PlantExportRequest(
                                planting=_preview_request(),
                                offset=(1.0, 2.0, 3.0),
                            ),
                            idempotency_key="direct-export-1",
                        )
                finally:
                    system._projects.pop("session_1", None)
        submitted = submit.call_args.args[1]
        self.assertEqual(result.random_seed, submitted.random_seed)
        self.assertIs(
            submitted.review_policy,
            PlantRunReviewPolicy.AUTOMATIC,
        )
        self.assertEqual(
            submitted.elevation_provider_id,
            BLENDER_WORKER_ADAPTER_ID,
        )
        self.assertEqual(submitted.export_provider_id, DTS_ADAPTER_ID)
        self.assertEqual(submitted.offset, (1.0, 2.0, 3.0))

    def test_export_retry_keeps_the_same_execution_and_resolved_providers(
        self,
    ) -> None:
        execution = replace(
            _execution(
                generated_point_set_id="points_1",
                asset_assignment_set_id="assignments_1",
            ),
            continuation_artifact_id="artifact_continuation",
            continuation_hash="sha256:" + "3" * 64,
            status=PlantRunExecutionStatus.QUEUED,
            stage=PlantRunExecutionStage.ELEVATION,
        )
        store = _PreviewStore(execution)
        runtime = _QueryRuntime(store)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    with patch.object(
                        system,
                        "confirm_plant_run_execution",
                        return_value=execution,
                    ) as confirm:
                        result = system.export_plant_preview(
                            "session_1",
                            execution.execution_id,
                            PlantPreviewOutputRequest(offset=(1.0, 2.0, 3.0), selected_asset_ids=("asset_1",)),
                        )
                finally:
                    system._projects.pop("session_1", None)
        self.assertEqual(result.execution_id, execution.execution_id)
        continuation = confirm.call_args.args[2]
        self.assertEqual(
            continuation.elevation_provider_id,
            BLENDER_WORKER_ADAPTER_ID,
        )
        self.assertEqual(continuation.export_provider_id, DTS_ADAPTER_ID)
        self.assertEqual(continuation.offset, (1.0, 2.0, 3.0))
        self.assertEqual(continuation.selected_asset_ids, ("asset_1",))

    def test_completed_preview_reexport_reuses_resolved_points_only(self) -> None:
        execution = replace(
            _execution(
                generated_point_set_id="points_generated",
                asset_assignment_set_id="assignments_1",
            ),
            status=PlantRunExecutionStatus.SUCCEEDED,
            stage=PlantRunExecutionStage.COMPLETED,
            elevation_point_set_id="points_elevation_resolved",
            export_job_id="job_initial_export",
            export_id="export_initial",
        )
        host = _ReexportHost(execution)
        runtime = _QueryRuntime(host)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    submitted = system.submit_plant_preview_reexport(
                        "session_1",
                        execution.execution_id,
                        PlantPreviewOutputRequest(offset=(4.0, 5.0, 6.0), selected_asset_ids=("asset_1",)),
                    )
                    host.job.status = JobStatus.SUCCEEDED
                    completed = system.get_plant_preview_reexport(
                        "session_1",
                        execution.execution_id,
                        host.job.job_id,
                    )
                    latest = system.get_plant_preview_export(
                        "session_1",
                        execution.execution_id,
                    )
                finally:
                    system._projects.pop("session_1", None)

        self.assertEqual(submitted.status, JobStatus.QUEUED)
        self.assertEqual(completed.export_id, "export_reexport")
        self.assertEqual(latest.export_id, "export_reexport")
        self.assertEqual(latest.offset, (4.0, 5.0, 6.0))
        self.assertEqual(host.submitted_request.selected_asset_ids, ("asset_1",))
        self.assertEqual(latest.selected_asset_ids, ("asset_1",))
        self.assertEqual(host.submitted_request.point_set_id, "points_elevation_resolved")
        self.assertEqual(
            host.submitted_request.asset_assignment_set_id,
            "assignments_1",
        )
        self.assertEqual(host.submitted_request.provider_id, DTS_ADAPTER_ID)
        self.assertEqual(host.submitted_request.offset, (4.0, 5.0, 6.0))

    def test_local_api_separates_preview_and_output_fields(self) -> None:
        execution = _execution()
        submission = PlantPreviewSubmission(execution=execution, random_seed=42)
        reexport = PlantPreviewReexportInfo(
            execution_id=execution.execution_id,
            export_job_id="job_reexport",
            status=JobStatus.QUEUED,
            export_id=None,
            error_message=None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "submit_plant_preview",
                        return_value=submission,
                    ) as submit,
                    patch.object(
                        system,
                        "get_plant_preview",
                        return_value=execution,
                    ),
                    patch.object(
                        system,
                        "retry_plant_run_assignment",
                        return_value=execution,
                    ) as retry_assignment,
                    patch.object(
                        system,
                        "export_plant_preview",
                        return_value=execution,
                    ) as export,
                    patch.object(
                        system,
                        "get_plant_preview_export",
                        return_value=ExportDownloadInfo(
                            export_id="export_latest",
                            artifact_id="artifact_latest",
                            media_type="application/xml",
                            byte_size=12,
                            suggested_filename="verdflow-export_latest.xml",
                            offset=(4.0, 5.0, 6.0),
                        ),
                    ) as latest_export,
                    patch.object(
                        system,
                        "submit_plant_preview_reexport",
                        return_value=reexport,
                    ) as submit_reexport,
                    patch.object(
                        system,
                        "get_plant_preview_reexport",
                        return_value=reexport,
                    ) as get_reexport,
                    LocalApiServer(system) as server,
                ):
                    base = (
                        server.base_url
                        + "/api/v1/projects/session_1/plant-previews"
                    )
                    status, submitted = _request_json(
                        base,
                        method="POST",
                        document={
                            "scene_revision_id": "scene_1",
                            "region_set_id": "regions_1",
                            "selected_region_ids": ["region_1"],
                            "strategy_package_hash": "sha256:" + "1" * 64,
                            "target_type": "dts",
                            "catalog_id": "catalog_1",
                            "idempotency_key": "preview-request-1",
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(submitted["random_seed"], 42)
                    self.assertIsNone(submit.call_args.args[1].random_seed)

                    status, _ = _request_json(
                        base + "/" + execution.execution_id
                    )
                    self.assertEqual(status, 200)

                    status, retried = _request_json(
                        base
                        + "/"
                        + execution.execution_id
                        + "/retry-assignment",
                        method="POST",
                        document={},
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(
                        retried["execution_id"],
                        execution.execution_id,
                    )
                    retry_assignment.assert_called_once_with(
                        "session_1",
                        execution.execution_id,
                    )

                    status, _ = _request_json(
                        base + "/" + execution.execution_id + "/export",
                        method="POST",
                        document={},
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(
                        export.call_args.args[2].offset,
                        (0.0, 0.0, 0.0),
                    )

                    status, latest = _request_json(
                        base + "/" + execution.execution_id + "/export"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(latest["export_id"], "export_latest")
                    latest_export.assert_called_once_with(
                        "session_1",
                        execution.execution_id,
                    )

                    status, operation = _request_json(
                        base + "/" + execution.execution_id + "/re-export",
                        method="POST",
                        document={"offset": [4, 5, 6]},
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(operation["export_job_id"], "job_reexport")
                    self.assertEqual(
                        submit_reexport.call_args.args[2].offset,
                        (4.0, 5.0, 6.0),
                    )

                    status, _ = _request_json(
                        base
                        + "/"
                        + execution.execution_id
                        + "/re-export?export_job_id=job_reexport"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        get_reexport.call_args.args[2],
                        "job_reexport",
                    )

                    status, invalid = _request_json(
                        base,
                        method="POST",
                        document={
                            "scene_revision_id": "scene_1",
                            "region_set_id": "regions_1",
                            "selected_region_ids": ["region_1"],
                            "strategy_package_hash": "sha256:" + "1" * 64,
                            "target_type": "dts",
                            "catalog_id": "catalog_1",
                            "offset": [0, 0, 0],
                        },
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(invalid["error"]["code"], "invalid_request")

    def test_local_api_exposes_direct_export_without_provider_controls(
        self,
    ) -> None:
        execution = _execution()
        submission = PlantExportSubmission(
            execution=execution,
            random_seed=84,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "submit_plant_export",
                        return_value=submission,
                    ) as submit,
                    patch.object(
                        system,
                        "get_plant_export",
                        return_value=execution,
                    ),
                    LocalApiServer(system) as server,
                ):
                    base = (
                        server.base_url
                        + "/api/v1/projects/session_1/plant-exports"
                    )
                    document = {
                        "scene_revision_id": "scene_1",
                        "region_set_id": "regions_1",
                        "selected_region_ids": ["region_1"],
                        "strategy_package_hash": "sha256:" + "1" * 64,
                        "target_type": "dts",
                        "catalog_id": "catalog_1",
                        "offset": [1, 2, 3],
                        "idempotency_key": "direct-export-1",
                    }
                    status, submitted = _request_json(
                        base,
                        method="POST",
                        document=document,
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(submitted["random_seed"], 84)
                    request = submit.call_args.args[1]
                    self.assertEqual(request.offset, (1.0, 2.0, 3.0))
                    self.assertIsNone(request.planting.random_seed)

                    status, _ = _request_json(
                        base + "/" + execution.execution_id
                    )
                    self.assertEqual(status, 200)

                    invalid_document = dict(document)
                    invalid_document["elevation_provider_id"] = "unsafe"
                    status, invalid = _request_json(
                        base,
                        method="POST",
                        document=invalid_document,
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(
                        invalid["error"]["code"],
                        "invalid_request",
                    )

    def test_local_api_exposes_planting_composition_workflow(self) -> None:
        batch = PlantingBatchSummary(
            batch_id="batch_1",
            ordinal=0,
            plant_run_execution_id="plant_run_1",
            generated_point_set_id="points_1",
            asset_assignment_set_id="assignments_1",
            selected_region_ids=("region_1",),
            selected_asset_ids=("asset_1",),
            strategy_id="strategy_1",
            strategy_version="1.0.0",
            point_count=12,
            status=PlantRunExecutionStatus.SUCCEEDED,
            stage=PlantRunExecutionStage.COMPLETED,
            export_id="export_1",
            error_message=None,
            created_at="2026-09-11T00:00:00+00:00",
        )
        composition = PlantingCompositionSummary(
            composition_id="composition_1",
            scene_revision_id="scene_1",
            region_set_id="regions_1",
            generation=1,
            occupied_region_ids=("region_1",),
            batches=(batch,),
            updated_at="2026-09-11T00:00:00+00:00",
        )
        operation = CompositionExportOperationInfo(
            operation_id="composition_output_1",
            composition_id="composition_1",
            composition_generation=1,
            stage="elevation",
            export_job_id="job_composition_export",
            status=JobStatus.QUEUED,
            export_id=None,
            error_message=None,
        )
        xml_output = ProjectXmlOutputInfo(
            export_id="composition_export_1",
            suggested_filename="verdflow-composition_export_1.xml",
            media_type="application/xml",
            byte_size=128,
            record_count=12,
            offset=(1.0, 2.0, 3.0),
            created_at="2026-09-14T00:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "get_current_planting_composition",
                        return_value=composition,
                    ),
                    patch.object(
                        system,
                        "commit_plant_preview_batch",
                        return_value=composition,
                    ) as commit,
                    patch.object(
                        system,
                        "remove_planting_batch",
                        return_value=None,
                    ) as remove,
                    patch.object(
                        system,
                        "discard_plant_preview",
                        return_value=composition,
                    ) as discard,
                    patch.object(
                        system,
                        "submit_planting_composition_export",
                        return_value=operation,
                    ) as submit_export,
                    patch.object(
                        system,
                        "get_planting_composition_export",
                        return_value=operation,
                    ) as get_export,
                    patch.object(
                        system,
                        "get_active_planting_composition_export",
                        return_value=operation,
                    ),
                    patch.object(
                        system,
                        "list_project_xml_outputs",
                        return_value=(xml_output,),
                    ),
                    patch.object(
                        system,
                        "delete_project_xml_output",
                        return_value=xml_output,
                    ) as delete_xml,
                    LocalApiServer(system) as server,
                ):
                    project = server.base_url + "/api/v1/projects/session_1"
                    status, current = _request_json(
                        project + "/planting-composition"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        current["composition"]["composition_id"],
                        "composition_1",
                    )

                    status, committed = _request_json(
                        project + "/plant-previews/plant_run_1/commit",
                        method="POST",
                        document={
                            "selected_asset_ids": ["asset_1"],
                            "continue_planting": True,
                            "replacing_batch_id": "batch_previous",
                        },
                    )
                    self.assertEqual(status, 200, committed)
                    self.assertEqual(committed["batches"][0]["batch_id"], "batch_1")
                    request = commit.call_args.args[2]
                    self.assertTrue(request.continue_planting)
                    self.assertEqual(request.selected_asset_ids, ("asset_1",))
                    self.assertEqual(
                        request.replacing_batch_id,
                        "batch_previous",
                    )

                    status, removed = _request_json(
                        project + "/planting-batches/batch_1",
                        method="DELETE",
                    )
                    self.assertEqual(status, 200)
                    self.assertIsNone(removed["composition"])
                    remove.assert_called_once_with("session_1", "batch_1")

                    status, discarded = _request_json(
                        project + "/plant-previews/plant_run_1",
                        method="DELETE",
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        discarded["composition"]["composition_id"],
                        "composition_1",
                    )
                    discard.assert_called_once_with(
                        "session_1", "plant_run_1"
                    )

                    status, submitted = _request_json(
                        project + "/planting-composition-exports",
                        method="POST",
                        document={
                            "offset": [1, 2, 3],
                            "batches": [
                                {
                                    "batch_id": "batch_1",
                                    "selected_asset_ids": ["asset_1"],
                                }
                            ],
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(
                        submitted["operation_id"],
                        "composition_output_1",
                    )
                    self.assertEqual(
                        submit_export.call_args.args[1].offset,
                        (1.0, 2.0, 3.0),
                    )
                    self.assertEqual(
                        submit_export.call_args.args[1].batches[0].batch_id,
                        "batch_1",
                    )

                    status, polled = _request_json(
                        project
                        + "/planting-composition-exports/composition_output_1"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(polled["status"], "queued")
                    get_export.assert_called_once_with(
                        "session_1",
                        "composition_output_1",
                    )

                    status, active = _request_json(
                        project + "/planting-composition-exports/active"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        active["operation"]["operation_id"],
                        "composition_output_1",
                    )

                    status, output_page = _request_json(
                        project + "/xml-outputs"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        output_page["items"][0]["export_id"],
                        "composition_export_1",
                    )

                    status, deleted = _request_json(
                        project + "/xml-outputs/composition_export_1",
                        method="DELETE",
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        deleted["export_id"],
                        "composition_export_1",
                    )
                    delete_xml.assert_called_once_with(
                        "session_1",
                        "composition_export_1",
                    )

    def test_export_download_is_resolved_by_export_record_and_streamed(
        self,
    ) -> None:
        payload = b"<ModelPoints><ModelPoint /></ModelPoints>"
        runtime = _QueryRuntime(_ExportStore(payload))
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    info = system.get_export_download("session_1", "export_1")
                    chunk = system.read_export_chunk(
                        "session_1",
                        "export_1",
                        offset=5,
                        limit=8,
                    )
                finally:
                    system._projects.pop("session_1", None)
        self.assertEqual(info.artifact_id, "artifact_xml")
        self.assertEqual(info.media_type, "application/xml")
        self.assertEqual(chunk, payload[5:13])

        download = ExportDownloadInfo(
            export_id="export_1",
            artifact_id="artifact_xml",
            media_type="application/xml",
            byte_size=len(payload),
            suggested_filename="verdflow-export_1.xml",
            offset=(1.0, 2.0, 3.0),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "get_export_download",
                        return_value=download,
                    ),
                    patch.object(
                        system,
                        "read_export_chunk",
                        side_effect=lambda _session, _export, offset, limit: (
                            payload[offset : offset + limit]
                        ),
                    ),
                    LocalApiServer(system) as server,
                ):
                    response = urlopen(
                        server.base_url
                        + "/api/v1/projects/session_1/exports/export_1/content",
                        timeout=10,
                    )
                    with response:
                        streamed = response.read()
                        disposition = response.headers["Content-Disposition"]
        self.assertEqual(streamed, payload)
        self.assertIn("verdflow-export_1.xml", disposition)

    def test_project_xml_output_delete_runs_on_owner_store(self) -> None:
        store = _CompositionOutputStore()
        runtime = _OwnerRuntime(SimpleNamespace(store=store))
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                system._projects["session_1"] = runtime
                try:
                    removed = system.delete_project_xml_output(
                        "session_1",
                        "composition_export_1",
                    )
                finally:
                    system._projects.pop("session_1", None)

        self.assertEqual(removed.export_id, "composition_export_1")
        self.assertEqual(store.deleted_export_id, "composition_export_1")


class _QueryRuntime:
    def __init__(self, store: object) -> None:
        self.store = store

    def query(self, callback, *, timeout_seconds: float):
        return callback(self.store)

    def call(self, callback, *, timeout_seconds: float):
        return callback(self.store)


class _OwnerRuntime:
    def __init__(self, host: object) -> None:
        self.host = host

    def call(self, callback, *, timeout_seconds: float):
        return callback(self.host)


class _CompositionOutputStore:
    def __init__(self) -> None:
        self.deleted_export_id: str | None = None

    def get_composition_export_record(self, export_id: str):
        return SimpleNamespace(
            export_id=export_id,
            output_artifact_id="artifact_xml",
            offset_x=1.0,
            offset_y=2.0,
            offset_z=3.0,
            record_count=42,
            created_at="2026-09-23T00:00:00+00:00",
        )

    def get_artifact(self, artifact_id: str):
        return SimpleNamespace(
            media_type="application/xml",
            byte_size=256,
        )

    def delete_composition_export_record(self, export_id: str):
        self.deleted_export_id = export_id


class _PreviewStore:
    def __init__(self, execution: PlantRunExecutionInfo) -> None:
        self.execution = execution

    def get_plant_run_execution(self, execution_id: str):
        return self.execution

    def get_point_set(self, point_set_id: str):
        return SimpleNamespace(planting_run_id="planting_1")

    def get_planting_run(self, planting_run_id: str):
        return SimpleNamespace(scene_revision_id="scene_1")

    def get_scene_revision(self, scene_revision_id: str):
        return SimpleNamespace(importer_id=BLENDER_WORKER_ADAPTER_ID)

    def get_assignment_set(self, assignment_set_id: str):
        return SimpleNamespace(target_type="dts")


class _ReexportHost:
    def __init__(self, execution: PlantRunExecutionInfo) -> None:
        self.execution = execution
        self.store = self
        self.job = SimpleNamespace(
            job_id="job_reexport",
            command=SimpleNamespace(artifact_id="command"),
            status=JobStatus.QUEUED,
            error=None,
        )
        self.submitted_request = None
        self.export_record = SimpleNamespace(
            export_id="export_reexport",
            producer_job_id="job_reexport",
            point_set_id="points_elevation_resolved",
            asset_assignment_set_id="assignments_1",
            output_artifact_id="artifact_reexport",
            offset_x=4.0,
            offset_y=5.0,
            offset_z=6.0,
        )

    def get_plant_run_execution(self, execution_id: str):
        return self.execution

    def get_point_set(self, point_set_id: str):
        return SimpleNamespace(planting_run_id="planting_1")

    def get_planting_run(self, planting_run_id: str):
        return SimpleNamespace(scene_revision_id="scene_1")

    def get_scene_revision(self, scene_revision_id: str):
        return SimpleNamespace(importer_id=BLENDER_WORKER_ADAPTER_ID)

    def get_asset_assignment_set(self, assignment_set_id: str):
        return SimpleNamespace(target_type="dts")

    def get_job(self, job_id: str):
        return self.job

    def get_latest_export_record(
        self,
        point_set_id: str,
        asset_assignment_set_id: str,
    ):
        return self.export_record

    def get_export_record(self, export_id: str):
        return self.export_record

    def get_artifact(self, artifact_id: str):
        return SimpleNamespace(
            artifact_id=artifact_id,
            media_type="application/xml",
            byte_size=128,
        )

    def submit_output_export(self, request):
        self.submitted_request = request
        return self.job

    def read_artifact_chunk(self, artifact_id: str, *, offset: int, limit: int):
        return json.dumps({"selected_asset_ids": self.submitted_request.selected_asset_ids}).encode()

    def finalize_output_export(self, job_id: str):
        return self.export_record


class _ExportStore:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def get_export_record(self, export_id: str):
        return SimpleNamespace(
            export_id=export_id,
            producer_job_id="job_export",
            output_artifact_id="artifact_xml",
            offset_x=1.0,
            offset_y=2.0,
            offset_z=3.0,
        )

    def get_artifact(self, artifact_id: str):
        return SimpleNamespace(
            artifact_id=artifact_id,
            media_type="application/xml",
            byte_size=len(self.payload),
        )

    def read_artifact_chunk(self, artifact_id: str, *, offset: int, limit: int):
        if artifact_id == "command":
            return b'{}'
        return self.payload[offset : offset + limit]

    def get_job(self, job_id: str):
        return SimpleNamespace(command=SimpleNamespace(artifact_id="command"))


def _preview_request() -> PlantPreviewRequest:
    return PlantPreviewRequest(
        scene_revision_id="scene_1",
        region_set_id="regions_1",
        selected_region_ids=("region_1",),
        strategy_package_hash="sha256:" + "1" * 64,
        target_type="dts",
        catalog_id="catalog_1",
    )


def _execution(
    *,
    generated_point_set_id: str | None = None,
    asset_assignment_set_id: str | None = None,
) -> PlantRunExecutionInfo:
    return PlantRunExecutionInfo(
        execution_id="plant_run_1",
        idempotency_key="preview-request-1",
        request_artifact_id="artifact_request",
        request_hash="sha256:" + "2" * 64,
        continuation_artifact_id=None,
        continuation_hash=None,
        status=PlantRunExecutionStatus.AWAITING_CONFIRMATION,
        stage=PlantRunExecutionStage.REVIEW,
        revision=2,
        generation_job_id="job_generate",
        generated_point_set_id=generated_point_set_id,
        assignment_job_id="job_assign",
        asset_assignment_set_id=asset_assignment_set_id,
        elevation_job_id=None,
        elevation_point_set_id=None,
        export_job_id=None,
        export_id=None,
        error_code=None,
        error_message=None,
        created_at="2026-08-14T00:00:00+00:00",
        updated_at="2026-08-14T00:01:00+00:00",
    )


def _request_json(
    url: str,
    *,
    method: str = "GET",
    document: dict | None = None,
) -> tuple[int, dict]:
    payload = None
    headers = {"Accept": "application/json"}
    if document is not None:
        payload = json.dumps(document).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


if __name__ == "__main__":
    unittest.main()
