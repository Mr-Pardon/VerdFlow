from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "authoring-kits" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "geospatial-worker" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
    str(ROOT / "apps" / "studio-service" / "src"),
    str(ROOT / "apps" / "dts-live-connector" / "src"),
    str(ROOT / "apps" / "local-api" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    InferenceProtocol,
    JobCommandRef,
    JobStatus,
    JobSubmission,
    MaterialClassification,
    MaterialClassificationCandidate,
    MaterialClassificationItem,
    MaterialConfirmationMode,
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStage,
    MaterialFlowExecutionStatus,
    MaterialFactItem,
    MaterialFactsView,
    MaterialSelectionMode,
    MaterialTextureFact,
    ModelInvocationInfo,
    ModelTokenUsage,
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    RegionSetInfo,
    SceneRevisionInfo,
    ScenePreparationInfo,
    ScenePreparationStage,
    ScenePreparationStatus,
    SourceInfo,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    MaterialConfirmationResult, PlantRunExecutionRequest, PlantRunExecutionService,
)
from verdflow.planting import canonical_json_bytes, semantic_hash  # noqa: E402
from verdflow.system_host import MaterialExtractionSubmission  # noqa: E402
from verdflow.studio_service import (  # noqa: E402
    Bounds3D,
    PointLegendItem,
    PointReviewItem,
    PointReviewManifest,
    PointViewportChunk,
    RegionClassificationSummary,
    RegionViewerItem,
    RegionViewerManifest,
    RegionViewportChunk,
    StudioQueryService,
)
from verdflow.system_host import (  # noqa: E402
    ModelProviderModelInfo,
    ModelProviderModelList,
    ModelProviderTestResult,
    ProjectBusinessState,
    ProjectBusinessSummary,
    ProjectTaskInfo,
    ProjectTaskPage,
    RecentProjectInfo,
    RoleBindingProfileDetail,
    RoleBindingProfileSummary,
)
from verdflow.local_api import LocalApiConfig, LocalApiServer  # noqa: E402
from verdflow.dts_live_connector import DtsLiveServiceStatus  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.system_host import SystemHost, SystemHostConfig  # noqa: E402
from verdflow.strategy_worker import (  # noqa: E402
    inspect_strategy_package,
    write_strategy_package_archive,
)


def request_json(
    url: str,
    *,
    method: str = "GET",
    document: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    payload = None
    request_headers = dict(headers or {})
    if document is not None:
        payload = json.dumps(document).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=5.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


class DtsLiveLocalApiTests(unittest.TestCase):
    def test_status_does_not_start_service_and_mutations_require_management(self) -> None:
        token = "m" * 32
        status_view = DtsLiveServiceStatus(
            state="stopped",
            connector_base_url=None,
            connector_session_id=None,
            launch_url=None,
            launch_expires_at=None,
            explorer_executable=None,
            started_at=None,
            webui_connected=False,
            last_error=None,
            events=(),
        )
        lifecycle = Mock()
        lifecycle.status.return_value = status_view
        lifecycle.start.return_value = status_view
        lifecycle.stop.return_value = status_view
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                    dts_live_lifecycle=lifecycle,
                ) as server:
                    self.assertEqual(lifecycle.start.call_count, 0)
                    denied, _ = request_json(
                        server.base_url + "/api/v1/dts-live/status"
                    )
                    self.assertEqual(denied, 403)
                    self.assertEqual(lifecycle.start.call_count, 0)

                    headers = {"Authorization": f"Bearer {token}"}
                    ok, document = request_json(
                        server.base_url + "/api/v1/dts-live/status",
                        headers=headers,
                    )
                    self.assertEqual(ok, 200)
                    self.assertEqual(document["state"], "stopped")
                    self.assertEqual(lifecycle.start.call_count, 0)

                    ok, _ = request_json(
                        server.base_url + "/api/v1/dts-live/start",
                        method="POST",
                        headers=headers,
                    )
                    self.assertEqual(ok, 200)
                    lifecycle.start.assert_called_once_with()


class StrategyExchangeLocalApiTests(unittest.TestCase):
    def test_vfs_and_authoring_kit_routes_require_management_and_write_files(
        self,
    ) -> None:
        token = "s" * 32
        strategy_root = ROOT / "strategy-packs" / "universal-natural-grove"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.vfs"
            exported = root / "exported.vfs"
            kit = root / "vfs-author.zip"
            write_strategy_package_archive(
                inspect_strategy_package(strategy_root),
                source,
            )
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                ) as server:
                    denied, _ = request_json(
                        server.base_url + "/api/v1/strategy-packages/import",
                        method="POST",
                        document={"source_path": str(source)},
                    )
                    self.assertEqual(denied, 403)

                    headers = {"Authorization": f"Bearer {token}"}
                    status, imported = request_json(
                        server.base_url + "/api/v1/strategy-packages/import",
                        method="POST",
                        document={"source_path": str(source)},
                        headers=headers,
                    )
                    self.assertEqual(status, 201)
                    self.assertFalse(imported["already_installed"])
                    package_hash = str(imported["package_hash"])

                    status, result = request_json(
                        server.base_url
                        + f"/api/v1/strategies/{quote(package_hash, safe='')}/export-package",
                        method="POST",
                        document={"destination_path": str(exported)},
                        headers=headers,
                    )
                    self.assertEqual(status, 201)
                    self.assertEqual(result["package_hash"], package_hash)
                    self.assertTrue(exported.is_file())

                    status, result = request_json(
                        server.base_url
                        + "/api/v1/authoring-kits/vfs-author/export",
                        method="POST",
                        document={"destination_path": str(kit)},
                        headers=headers,
                    )
                    self.assertEqual(status, 201)
                    self.assertEqual(result["skill_id"], "vfs-author")
                    self.assertTrue(kit.is_file())


class LocalApiTests(unittest.TestCase):
    def test_cancel_after_preview_transition_does_not_return_internal_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            with ProjectStore.create(project_path, "Review race") as store:
                document = PlantRunExecutionService._document(PlantRunExecutionRequest(
                    scene_revision_id="scene", region_set_id="regions",
                    selected_region_ids=("region",), strategy_package_hash="sha256:" + "1" * 64,
                    random_seed=17, target_type="dts",
                ))
                artifact = store.put_bytes(canonical_json_bytes(document), media_type="application/json")
                execution = store.create_plant_run_execution(
                    idempotency_key="review-race",
                    request_artifact_id=artifact.artifact_id,
                    request_hash=semantic_hash(document),
                )
                execution = store.update_plant_run_execution(
                    replace(execution, status=PlantRunExecutionStatus.RUNNING,
                            stage=PlantRunExecutionStage.REVIEW),
                    expected_revision=execution.revision,
                )
            token = "c" * 32
            with SystemHost(SystemHostConfig(library_path=root / "library")) as system:
                session = system.open_project(project_path, enable_blender_worker=False)
                with LocalApiServer(system, LocalApiConfig(management_token=token)) as server:
                    url = (server.base_url + f"/api/v1/projects/{session.session_id}"
                           + f"/plant-runs/{execution.execution_id}/cancel")
                    headers = {"Authorization": f"Bearer {token}"}
                    status, cancellation = request_json(url, method="POST", headers=headers)
                    self.assertEqual(status, 202)
                    self.assertTrue(cancellation["accepted"])
                    self.assertFalse(cancellation["worker_signal_sent"])
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        current = system.get_plant_run_execution(session.session_id, execution.execution_id)
                        if current.status is PlantRunExecutionStatus.CANCELLED:
                            break
                        time.sleep(0.01)
                    self.assertEqual(current.status, PlantRunExecutionStatus.CANCELLED)
                    status, repeated = request_json(url, method="POST", headers=headers)
                    self.assertEqual(status, 200)
                    self.assertFalse(repeated["accepted"])

    def test_viewer_queries_are_bounded_and_typed(self) -> None:
        bounds = Bounds3D(0, 0, 0, 10, 10, 2)
        region_manifest = RegionViewerManifest(
            region_set_id="regions_1",
            scene_revision_id="scene_1",
            coordinate_frame_id="frame_1",
            region_count=1,
            bounds=bounds,
            classifications=(
                RegionClassificationSummary("general_green", 1, 100),
            ),
        )
        region_chunk = RegionViewportChunk(
            items=(
                RegionViewerItem(
                    region_id="region_1",
                    ordinal=0,
                    label="Grass",
                    classification="general_green",
                    strategy_hint="universal_natural_grove",
                    footprint_area_xy=100,
                    bounds=bounds,
                    footprint_boundary_xyz=(
                        ((0, 0, 0), (10, 0, 0), (0, 0, 0)),
                    ),
                ),
            ),
            next_after_ordinal=None,
            truncated=False,
        )
        point_manifest = PointReviewManifest(
            point_set_id="points_1",
            assignment_set_id="assignments_1",
            planting_run_id="run_1",
            region_set_id="regions_1",
            coordinate_frame_id="frame_1",
            kind="generated",
            target_type="dts",
            point_count=1,
            bounds=bounds,
            roles=(PointLegendItem("tree", 1),),
            assets=(PointLegendItem("oak", 1),),
        )
        point_chunk = PointViewportChunk(
            items=(
                PointReviewItem(
                    point_id="point_1",
                    ordinal=0,
                    region_id="region_1",
                    role_key="tree",
                    position=(1, 2, 0),
                    asset_id="oak",
                    target_scale_xyz=(0.8, 0.8, 0.8),
                ),
            ),
            next_after_ordinal=None,
            truncated=False,
        )
        with tempfile.TemporaryDirectory() as temporary:
            studio_queries = Mock(spec=StudioQueryService)
            studio_queries.get_region_manifest.return_value = region_manifest
            studio_queries.get_region_chunk.return_value = region_chunk
            studio_queries.get_point_manifest.return_value = point_manifest
            studio_queries.get_point_chunk.return_value = point_chunk
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "get_project",
                        return_value=Mock(
                            project_path=str(Path(temporary) / "project")
                        ),
                    ),
                    LocalApiServer(
                        system,
                        studio_queries=studio_queries,
                    ) as server,
                ):
                    prefix = server.base_url + "/api/v1/projects/session_1"
                    status, manifest = request_json(
                        prefix + "/region-sets/regions_1/viewer"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(manifest["region_count"], 1)

                    status, regions = request_json(
                        prefix
                        + "/region-sets/regions_1/geometry"
                        + "?min_x=0&min_y=0&max_x=10&max_y=10"
                        + "&classification=general_green,street_tree&limit=50"
                        + "&simplification_tolerance=0.5"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(regions["items"][0]["region_id"], "region_1")
                    self.assertEqual(
                        studio_queries.get_region_chunk.call_args.kwargs[
                            "classifications"
                        ],
                        ("general_green", "street_tree"),
                    )
                    self.assertEqual(
                        studio_queries.get_region_chunk.call_args.kwargs[
                            "simplification_tolerance"
                        ],
                        0.5,
                    )

                    status, review = request_json(
                        prefix
                        + "/point-sets/points_1/review"
                        + "?assignment_set_id=assignments_1"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(review["assets"][0]["key"], "oak")

                    status, points = request_json(
                        prefix
                        + "/point-sets/points_1/points"
                        + "?assignment_set_id=assignments_1"
                        + "&min_x=0&min_y=0&max_x=10&max_y=10"
                        + "&role=tree&asset=oak&after_ordinal=4&limit=100"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(points["items"][0]["asset_id"], "oak")
                    point_options = studio_queries.get_point_chunk.call_args.kwargs
                    self.assertEqual(point_options["roles"], ("tree",))
                    self.assertEqual(point_options["assets"], ("oak",))
                    self.assertEqual(point_options["after_ordinal"], 4)

    def test_recent_projects_tasks_and_summary_are_product_queries(self) -> None:
        recent = RecentProjectInfo(
            registry_id="recent_1",
            project_id="project_1",
            name="Project",
            project_path="D:/projects/one",
            last_opened_at="2026-08-13T00:00:00+00:00",
            last_workspace="dts",
            availability="available",
            active_session_id="session_1",
        )
        task = ProjectTaskInfo(
            job_id="job_1",
            business_name="生成种植点位",
            status="running",
            stage="sampling",
            progress=0.5,
            attempt_count=1,
            worker_id="worker_1",
            heartbeat_at="2026-08-13T00:00:02+00:00",
            created_at="2026-08-13T00:00:00+00:00",
            started_at="2026-08-13T00:00:01+00:00",
            finished_at=None,
            error_code=None,
            error_message=None,
        )
        summary = ProjectBusinessSummary(
            project_id="project_1",
            state=ProjectBusinessState.RUNNING,
            execution_kind="plant_run",
            execution_id="pre_1",
            execution_status="running",
            execution_stage="generation",
            updated_at="2026-08-13T00:00:02+00:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "list_recent_projects",
                        return_value=(recent,),
                    ),
                    patch.object(
                        system,
                        "list_project_tasks",
                        return_value=ProjectTaskPage(
                            items=(task,),
                            next_cursor="next",
                        ),
                    ) as list_tasks,
                    patch.object(
                        system,
                        "get_project_business_summary",
                        return_value=summary,
                    ),
                    LocalApiServer(system) as server,
                ):
                    status, projects = request_json(
                        server.base_url + "/api/v1/recent-projects"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(projects["items"][0]["registry_id"], "recent_1")

                    status, tasks = request_json(
                        server.base_url
                        + "/api/v1/projects/session_1/tasks"
                        + "?status=queued,running&limit=20&before=cursor"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(tasks["items"][0]["business_name"], "生成种植点位")
                    self.assertEqual(tasks["next_cursor"], "next")
                    self.assertEqual(
                        list_tasks.call_args.kwargs["statuses"],
                        (JobStatus.QUEUED, JobStatus.RUNNING),
                    )
                    self.assertEqual(list_tasks.call_args.kwargs["before"], "cursor")
                    self.assertEqual(list_tasks.call_args.kwargs["limit"], 20)

                    status, observed = request_json(
                        server.base_url
                        + "/api/v1/projects/session_1/summary"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(observed["state"], "running")

    def test_plant_run_review_confirmation_is_a_typed_continuation(self) -> None:
        execution = PlantRunExecutionInfo(
            execution_id="pre_1",
            idempotency_key="plant:key",
            request_artifact_id="artifact_request",
            request_hash="sha256:" + "1" * 64,
            continuation_artifact_id="artifact_continuation",
            continuation_hash="sha256:" + "2" * 64,
            status=PlantRunExecutionStatus.QUEUED,
            stage=PlantRunExecutionStage.ELEVATION,
            revision=5,
            generation_job_id="job_generation",
            generated_point_set_id="points_generated",
            assignment_job_id="job_assignment",
            asset_assignment_set_id="assignments_1",
            elevation_job_id=None,
            elevation_point_set_id=None,
            export_job_id=None,
            export_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-13T00:00:00+00:00",
            updated_at="2026-08-13T00:00:01+00:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "confirm_plant_run_execution",
                        return_value=execution,
                    ) as confirm,
                    LocalApiServer(system) as server,
                ):
                    status, document = request_json(
                        server.base_url
                        + "/api/v1/projects/session_1/plant-runs/pre_1/confirm",
                        method="POST",
                        document={
                            "elevation_provider_id": "blender.elevation",
                            "export_provider_id": "dts.xml",
                            "offset": [0.0, 0.0, 0.0],
                            "elevation_options": {"ray_distance": 1000.0},
                        },
                    )
        self.assertEqual(status, 202)
        self.assertEqual(document["execution_id"], "pre_1")
        request = confirm.call_args.args[2]
        self.assertEqual(request.elevation_provider_id, "blender.elevation")
        self.assertEqual(request.export_provider_id, "dts.xml")
        self.assertEqual(request.offset, (0.0, 0.0, 0.0))
        self.assertEqual(request.elevation_options, {"ray_distance": 1000.0})

    def test_source_import_and_scene_preparation_are_product_operations(self) -> None:
        token = "s" * 32
        source = SourceInfo(
            source_id="source_1",
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id="artifact_source",
            content_hash="sha256:" + "1" * 64,
            imported_at="2026-08-12T00:00:00+00:00",
        )
        assembly = SourceInfo(
            source_id="source_assembly",
            kind=SourceKind.ASSEMBLY,
            original_name="regions + elevation",
            artifact_id="artifact_assembly",
            content_hash="sha256:" + "3" * 64,
            imported_at="2026-08-12T00:00:00+00:00",
        )
        execution = ScenePreparationInfo(
            execution_id="spe_1",
            idempotency_key="prepare:key",
            request_artifact_id="artifact_request",
            request_hash="sha256:" + "2" * 64,
            status=ScenePreparationStatus.AWAITING_CONFIRMATION,
            stage=ScenePreparationStage.MATERIAL_FLOW,
            revision=4,
            source_id="source_1",
            inspection_job_id="job_inspection",
            scene_revision_id="scene_1",
            material_flow_execution_id="mfe_1",
            region_set_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-12T00:00:00+00:00",
            updated_at="2026-08-12T00:00:01+00:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "add_source_file",
                        return_value=source,
                    ) as add_source,
                    patch.object(
                        system,
                        "create_identity_source_assembly",
                        return_value=assembly,
                    ) as create_assembly,
                    patch.object(
                        system,
                        "submit_scene_preparation",
                        return_value=execution,
                    ) as submit,
                    patch.object(
                        system,
                        "get_scene_preparation",
                        return_value=execution,
                    ),
                    patch.object(
                        system,
                        "confirm_scene_preparation",
                        return_value=execution,
                    ) as confirm,
                    LocalApiServer(
                        system,
                        config=LocalApiConfig(management_token=token),
                    ) as server,
                ):
                    base = server.base_url + "/api/v1/projects/session_1"
                    source_document = {
                        "source_path": str(root / "scene.fbx"),
                        "kind": "fbx",
                    }
                    denied, _ = request_json(
                        base + "/sources",
                        method="POST",
                        document=source_document,
                    )
                    self.assertEqual(denied, 403)
                    status, imported = request_json(
                        base + "/sources",
                        method="POST",
                        document=source_document,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(status, 201)
                    self.assertEqual(imported["source_id"], "source_1")
                    self.assertEqual(
                        add_source.call_args.args[1],
                        root / "scene.fbx",
                    )

                    status, assembled = request_json(
                        base + "/source-assemblies",
                        method="POST",
                        document={
                            "original_name": "regions + elevation",
                            "source_ids": ["vector_1", "elevation_1"],
                        },
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(status, 201)
                    self.assertEqual(assembled["source_id"], "source_assembly")
                    self.assertEqual(
                        create_assembly.call_args.kwargs["source_ids"],
                        ("vector_1", "elevation_1"),
                    )

                    status, prepared = request_json(
                        base + "/scene-preparations",
                        method="POST",
                        document={
                            "source_id": "source_1",
                            "analysis_provider_id": "model.test",
                            "region_provider_id": "blender",
                            "confirmation_mode": "manual",
                            "idempotency_key": "prepare:key",
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(prepared["execution_id"], "spe_1")
                    request = submit.call_args.args[1]
                    self.assertEqual(request.source_id, "source_1")
                    self.assertEqual(
                        submit.call_args.kwargs["idempotency_key"],
                        "prepare:key",
                    )

                    status, observed = request_json(
                        base + "/scene-preparations/spe_1"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(observed["status"], "awaiting_confirmation")

                    status, _ = request_json(
                        base + "/scene-preparations/spe_1/confirm",
                        method="POST",
                        document={},
                    )
                    self.assertEqual(status, 202)
                    confirm.assert_called_once_with("session_1", "spe_1")

    def test_material_flow_resources_are_thin_execution_operations(self) -> None:
        token = "m" * 32
        execution = MaterialFlowExecutionInfo(
            execution_id="mfe_1",
            idempotency_key="material-flow:key",
            request_artifact_id="artifact_request",
            request_hash="sha256:" + "1" * 64,
            status=MaterialFlowExecutionStatus.AWAITING_CONFIRMATION,
            stage=MaterialFlowExecutionStage.CONFIRMATION,
            revision=3,
            analysis_job_id="job_analysis",
            rule_pack_artifact_id=None,
            extraction_job_id=None,
            region_set_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-12T00:00:00+00:00",
            updated_at="2026-08-12T00:00:01+00:00",
        )
        facts = MaterialFactsView(
            scene_revision_id="scene_1",
            materials=(
                MaterialFactItem(
                    material_name="Grass",
                    face_count=12,
                    object_count=1,
                    object_names=("Ground",),
                    diffuse_color=(0.1, 0.7, 0.2, 1.0),
                    textures=(
                        MaterialTextureFact(
                            image_name="grass.png",
                            source_relative_path="textures/grass.png",
                        ),
                    ),
                ),
                MaterialFactItem(
                    material_name="Concrete",
                    face_count=20,
                    object_count=1,
                    object_names=("Road",),
                    diffuse_color=(),
                    textures=(),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "submit_material_flow_execution",
                        return_value=execution,
                    ) as submit,
                    patch.object(
                        system,
                        "get_material_flow_execution",
                        return_value=execution,
                    ),
                    patch.object(
                        system,
                        "confirm_material_flow_execution",
                        return_value=execution,
                    ) as confirm,
                    patch.object(
                        system,
                        "get_material_facts",
                        return_value=facts,
                    ),
                    LocalApiServer(
                        system,
                        config=LocalApiConfig(management_token=token),
                    ) as server,
                ):
                    base = server.base_url + "/api/v1/projects/session_1/material-flows"
                    status, submitted = request_json(
                        base,
                        method="POST",
                        document={
                            "scene_revision_id": "scene_1",
                            "region_provider_id": "blender",
                            "confirmation_mode": "manual",
                            "selection_mode": "manual",
                            "idempotency_key": "client-flow-key",
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(submitted["execution_id"], "mfe_1")
                    request = submit.call_args.args[1]
                    self.assertEqual(request.scene_revision_id, "scene_1")
                    self.assertEqual(request.region_provider_id, "blender")
                    self.assertIsNone(request.analysis_provider_id)
                    self.assertEqual(
                        request.selection_mode,
                        MaterialSelectionMode.MANUAL,
                    )
                    self.assertEqual(
                        submit.call_args.kwargs["idempotency_key"],
                        "client-flow-key",
                    )

                    status, observed = request_json(base + "/mfe_1")
                    self.assertEqual(status, 200)
                    self.assertEqual(observed["status"], "awaiting_confirmation")

                    status, material_facts = request_json(
                        server.base_url
                        + "/api/v1/projects/session_1/scene-revisions/"
                        + "scene_1/material-facts"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        material_facts["materials"][0]["material_name"],
                        "Grass",
                    )

                    status, confirmed = request_json(
                        base + "/mfe_1/confirm",
                        method="POST",
                        document={
                            "materials": [
                                {
                                    "material_name": "Grass",
                                    "classification": "general_green",
                                },
                                {
                                    "material_name": "Concrete",
                                    "classification": "not_green",
                                },
                            ]
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(confirmed["execution_id"], "mfe_1")
                    self.assertEqual(confirm.call_args.args[:2], ("session_1", "mfe_1"))
                    selection = confirm.call_args.args[2]
                    self.assertEqual(
                        selection.materials[0].classification,
                        MaterialClassification.GENERAL_GREEN,
                    )

                    status, denied = request_json(
                        base + "/mfe_1/cancel",
                        method="POST",
                        document={},
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(
                        denied["error"]["code"],
                        "management_auth_required",
                    )

    def test_material_and_region_resources_are_short_job_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with ProjectStore.create(root / "project", "Analysis") as store:
                artifact = store.put_bytes(b"{}", media_type="application/json")
                analysis_job = store.create_job(
                    JobSubmission(
                        command=JobCommandRef(
                            "fixture.analysis",
                            "1",
                            artifact.artifact_id,
                        ),
                        idempotency_key="analysis-key",
                    ),
                    "2026-08-12T00:00:00+00:00",
                )
                extraction_job = store.create_job(
                    JobSubmission(
                        command=JobCommandRef(
                            "fixture.extract",
                            "1",
                            artifact.artifact_id,
                        ),
                        idempotency_key="extract-key",
                    ),
                    "2026-08-12T00:00:01+00:00",
                )
            candidate = MaterialClassificationCandidate(
                task_id="analysis_task",
                task_type="material_classification.v1",
                input_ref="artifact_facts",
                invocation=ModelInvocationInfo(
                    provider_id="model.test",
                    provider_version="1",
                    protocol=InferenceProtocol.OPENAI_CHAT_COMPLETIONS,
                    model="test-model",
                    prompt_policy_version="1",
                    request_id=None,
                    usage=ModelTokenUsage(),
                ),
                materials=(
                    MaterialClassificationItem(
                        material_name="grass",
                        classification=MaterialClassification.GENERAL_GREEN,
                        confidence=0.99,
                        evidence=("name",),
                    ),
                ),
                warnings=(),
            )
            rule_pack = {
                "schema": "verdflow.region-rule-pack",
                "version": "1",
                "rules": [{"id": "grass"}],
            }
            confirmation = MaterialConfirmationResult(
                candidate_job_id=analysis_job.job_id,
                confirmation_mode=MaterialConfirmationMode.MANUAL,
                rule_pack_artifact_id="artifact_rule_pack",
                rule_pack=rule_pack,
            )
            submission = MaterialExtractionSubmission(
                analysis_job,
                candidate,
                confirmation,
                extraction_job,
            )
            region_set = RegionSetInfo(
                region_set_id="regions_1",
                scene_revision_id="scene_1",
                producer_job_id=extraction_job.job_id,
                extractor_id="blender",
                extractor_version="1",
                input_hash="sha256:" + "1" * 64,
                coordinate_frame_id="frame_1",
                geometry_artifact_id="geometry_1",
                metrics_artifact_id="metrics_1",
                region_count=3,
                created_at="2026-08-12T00:00:02+00:00",
            )
            scene = SceneRevisionInfo(
                scene_revision_id="scene_1",
                source_id="source_assembly",
                parent_revision_id=None,
                importer_id="verdflow.geospatial",
                importer_version="1",
                coordinate_frame_id="frame_1",
                scene_manifest_artifact_id="scene_manifest_1",
                created_at="2026-08-12T00:00:02+00:00",
            )

            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with (
                    patch.object(
                        system,
                        "submit_material_analysis",
                        return_value=analysis_job,
                    ) as submit_analysis,
                    patch.object(
                        system,
                        "get_material_candidate",
                        return_value=candidate,
                    ),
                    patch.object(
                        system,
                        "confirm_materials_and_submit_extraction",
                        return_value=submission,
                    ) as confirm,
                    patch.object(
                        system,
                        "submit_region_extraction",
                        return_value=extraction_job,
                    ) as submit_regions,
                    patch.object(
                        system,
                        "finalize_region_extraction",
                        return_value=region_set,
                    ),
                    patch.object(
                        system,
                        "submit_region_source",
                        return_value=extraction_job,
                    ) as submit_region_source,
                    patch.object(
                        system,
                        "finalize_region_source",
                        return_value=(scene, region_set),
                    ),
                    LocalApiServer(system) as server,
                ):
                    base = server.base_url + "/api/v1/projects/session_1"
                    status, submitted = request_json(
                        base + "/material-analyses",
                        method="POST",
                        document={
                            "scene_revision_id": "scene_1",
                            "provider_id": "model.test",
                            "confirmation_mode": "manual",
                            "idempotency_key": "client-analysis-key",
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(submitted["job_id"], analysis_job.job_id)
                    self.assertEqual(
                        submit_analysis.call_args.kwargs["idempotency_key"],
                        "client-analysis-key",
                    )

                    status, candidate_document = request_json(
                        base
                        + f"/material-analyses/{analysis_job.job_id}/candidate"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(candidate_document["task_id"], "analysis_task")

                    status, confirmed = request_json(
                        base
                        + f"/material-analyses/{analysis_job.job_id}/confirm",
                        method="POST",
                        document={"region_provider_id": "blender"},
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(
                        confirmed["extraction_job"]["job_id"],
                        extraction_job.job_id,
                    )
                    self.assertEqual(
                        confirm.call_args.kwargs["region_provider_id"],
                        "blender",
                    )

                    status, direct = request_json(
                        base + "/region-extractions",
                        method="POST",
                        document={
                            "scene_revision_id": "scene_1",
                            "provider_id": "blender",
                            "rule_pack": rule_pack,
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(direct["job_id"], extraction_job.job_id)
                    self.assertEqual(
                        submit_regions.call_args.args[1].rule_pack,
                        rule_pack,
                    )

                    status, finalized = request_json(
                        base
                        + f"/region-extractions/{extraction_job.job_id}/finalize",
                        method="POST",
                        document={},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(finalized["region_set_id"], "regions_1")

                    status, source_job = request_json(
                        base + "/region-sources",
                        method="POST",
                        document={
                            "source_id": "source_assembly",
                            "provider_id": "verdflow.geospatial",
                            "idempotency_key": "region-source-key",
                        },
                    )
                    self.assertEqual(status, 202)
                    self.assertEqual(source_job["job_id"], extraction_job.job_id)
                    source_request = submit_region_source.call_args.args[1]
                    self.assertEqual(source_request.source_id, "source_assembly")
                    self.assertEqual(
                        submit_region_source.call_args.kwargs["idempotency_key"],
                        "region-source-key",
                    )

                    status, source_result = request_json(
                        base
                        + f"/region-sources/{extraction_job.job_id}/finalize",
                        method="POST",
                        document={},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        source_result["scene_revision"]["scene_revision_id"],
                        "scene_1",
                    )
                    self.assertEqual(
                        source_result["region_set"]["region_set_id"],
                        "regions_1",
                    )

    def test_project_job_queries_and_cancel_control_use_runtime_boundaries(
        self,
    ) -> None:
        from verdflow.local_api import LocalApiConfig

        token = "management-token-for-project-control"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            with ProjectStore.create(project_path, "Monitored") as store:
                artifact = store.put_bytes(
                    b"{}",
                    media_type="application/json",
                    schema_id="verdflow.test-command",
                    schema_version="1",
                )
                job = store.create_job(
                    JobSubmission(
                        command=JobCommandRef(
                            command_type="test.unowned",
                            command_version="1",
                            artifact_id=artifact.artifact_id,
                        ),
                        idempotency_key="local-api-query",
                    ),
                    "2026-08-11T00:00:00+00:00",
                )

            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                session = system.open_project(
                    project_path,
                    enable_blender_worker=False,
                )
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                ) as server:
                    base = (
                        server.base_url
                        + f"/api/v1/projects/{session.session_id}"
                    )
                    status, job_document = request_json(
                        base + f"/jobs/{job.job_id}"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(job_document["status"], "queued")

                    status, events = request_json(
                        base
                        + f"/jobs/{job.job_id}/events"
                        + "?after_sequence=0&limit=10"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(len(events["items"]), 1)

                    status, attempts = request_json(
                        base + f"/jobs/{job.job_id}/attempts"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(attempts["items"], [])

                    status, artifact_document = request_json(
                        base + f"/artifacts/{artifact.artifact_id}"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        artifact_document["content_hash"],
                        artifact.content_hash,
                    )

                    status, denied = request_json(
                        base + f"/jobs/{job.job_id}/cancel",
                        method="POST",
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(
                        denied["error"]["code"],
                        "management_auth_required",
                    )

                    status, cancellation = request_json(
                        base + f"/jobs/{job.job_id}/cancel",
                        method="POST",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(status, 200)
                    self.assertFalse(cancellation["accepted"])

                    status, invalid = request_json(
                        base + f"/jobs/{job.job_id}/events?limit=1001"
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(
                        invalid["error"]["code"],
                        "invalid_request",
                    )

                    status, missing = request_json(base + "/jobs/missing")
                    self.assertEqual(status, 404)
                    self.assertEqual(
                        missing["error"]["code"],
                        "resource_not_found",
                    )

    def test_model_provider_management_requires_token_and_never_returns_secret(
        self,
    ) -> None:
        from verdflow.local_api import LocalApiConfig
        from verdflow.system_host import InMemorySecretStore

        token = "management-token-for-local-api-tests"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = InMemorySecretStore()
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                ),
                secret_store=secrets,
            ) as system:
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                ) as server:
                    status, providers = request_json(
                        server.base_url + "/api/v1/model-providers"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(len(providers["items"]), 3)

                    provider_url = (
                        server.base_url
                        + "/api/v1/model-providers/team-gateway"
                    )
                    provider = {
                        "display_name": "Team Gateway",
                        "protocol": "openai-chat-completions",
                        "endpoint": (
                            "https://models.example.com/v1/chat/completions"
                        ),
                        "default_model": "material-v1",
                    }
                    status, denied = request_json(
                        provider_url,
                        method="PUT",
                        document=provider,
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(
                        denied["error"]["code"],
                        "management_auth_required",
                    )

                    auth = {"Authorization": f"Bearer {token}"}
                    status, created = request_json(
                        provider_url,
                        method="PUT",
                        document=provider,
                        headers=auth,
                    )
                    self.assertEqual(status, 200)
                    self.assertFalse(created["secret_configured"])

                    status, configured = request_json(
                        provider_url + "/secret",
                        method="PUT",
                        document={"api_key": "local-api-secret-value"},
                        headers=auth,
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(configured["secret_configured"])
                    self.assertNotIn(
                        "local-api-secret-value",
                        json.dumps(configured),
                    )

                    status, selected = request_json(
                        provider_url + "/model",
                        method="PUT",
                        document={"model": "material-v2"},
                        headers=auth,
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(selected["default_model"], "material-v2")

                    with patch.object(
                        system,
                        "list_model_provider_models",
                        return_value=ModelProviderModelList(
                            provider_id="team-gateway",
                            items=(
                                ModelProviderModelInfo(
                                    model_id="material-v2",
                                    display_name="Material V2",
                                    owned_by="team",
                                ),
                            ),
                        ),
                    ):
                        status, models = request_json(
                            provider_url + "/models",
                            method="POST",
                            document={},
                            headers=auth,
                        )
                    self.assertEqual(status, 200)
                    self.assertEqual(models["items"][0]["model_id"], "material-v2")

                    with patch.object(
                        system,
                        "test_model_provider",
                        return_value=ModelProviderTestResult(
                            provider_id="team-gateway",
                            model="material-v1",
                            succeeded=True,
                            duration_ms=12,
                            message="Provider connection is valid",
                        ),
                    ) as test_provider:
                        status, tested = request_json(
                            provider_url + "/test",
                            method="POST",
                            document={},
                            headers=auth,
                        )
                    self.assertEqual(status, 200)
                    self.assertTrue(tested["succeeded"])
                    test_provider.assert_called_once_with(
                        "team-gateway",
                        model=None,
                    )

                    status, _ = request_json(
                        provider_url + "/secret",
                        method="DELETE",
                        headers=auth,
                    )
                    self.assertEqual(status, 200)
                    status, _ = request_json(
                        provider_url,
                        method="DELETE",
                        headers=auth,
                    )
                    self.assertEqual(status, 200)

    def test_multi_catalog_search_is_authenticated_read_only_and_paginated(self) -> None:
        from verdflow.system_host import AssetCatalogSaveRequest
        from tests.unit.test_asset_search import catalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(SystemHostConfig(library_path=root / "library", system_path=root / "system")) as system:
                for key in ("a", "b"):
                    system.save_asset_catalog(AssetCatalogSaveRequest(document=catalog(key), expected_generation=0))
                with LocalApiServer(system, LocalApiConfig(management_token="asset-search-management-test-token")) as server:
                    url = server.base_url + "/api/v1/asset-search"
                    body = {"target_type": "dts", "provider_id": "dts.static-mesh", "catalog_ids": ["a", "b"], "limit": 5}
                    status, _ = request_json(url, method="POST", document=body)
                    self.assertIn(status, (401, 403))
                    status, page = request_json(url, method="POST", document=body, headers={"Authorization": "Bearer asset-search-management-test-token"})
                    self.assertEqual(status, 200)
                    self.assertEqual((len(page["items"]), page["total_count"], page["next_offset"]), (5, 12, 5))
                    self.assertEqual([item.generation for item in system.list_asset_catalogs("dts")], [1, 1])

    def test_empty_catalog_can_be_created_edited_and_deleted(self) -> None:
        from verdflow.local_api import LocalApiConfig

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = "catalog-management-token-for-tests"
            headers = {"Authorization": f"Bearer {token}"}
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                ) as server:
                    catalog_url = (
                        server.base_url
                        + "/api/v1/asset-catalogs/dts/dts_vegetation"
                    )
                    status, created = request_json(
                        catalog_url,
                        method="PUT",
                        headers=headers,
                        document={
                            "expected_generation": 0,
                            "document": {
                                "schema": "verdflow.target-asset-catalog",
                                "version": "1",
                                "catalog_id": "dts_vegetation",
                                "target_type": "dts",
                                "provider_id": "dts.static-mesh",
                                "display_name": "DTS Vegetation",
                                "assets": [],
                                "collections": [],
                            },
                        },
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(created["info"]["generation"], 1)

                    status, detail = request_json(catalog_url)
                    self.assertEqual(status, 200)
                    self.assertEqual(detail["document"]["assets"], [])

                    status, removal = request_json(
                        catalog_url,
                        method="DELETE",
                        headers=headers,
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(removal["removed"])

    def test_profile_catalog_metadata_sync_requires_management(self) -> None:
        token = "profile-sync-management-token-for-tests"
        headers = {"Authorization": f"Bearer {token}"}
        summary = RoleBindingProfileSummary(
            profile_entry_id="entry_sync",
            profile_id="profile_sync",
            display_name="Sync",
            strategy_id="strategy",
            strategy_version="1.0.0",
            target_type="dts",
            provider_id="dts.static-mesh",
            catalog_id="catalog",
            generation=3,
            semantic_hash="sha256:" + "a" * 64,
            updated_at="2026-09-11T00:00:00Z",
        )
        detail = RoleBindingProfileDetail(
            summary=summary,
            role_count=1,
            assignments={"tree": {"enabled": True, "candidates": []}},
            catalog_drift={},
            drift_count=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with patch.object(
                    system,
                    "sync_role_binding_profile_assets",
                    return_value=detail,
                ) as sync:
                    with LocalApiServer(
                        system,
                        LocalApiConfig(management_token=token),
                    ) as server:
                        url = (
                            server.base_url
                            + "/api/v1/role-binding-profiles/profile_sync/"
                            + "sync-catalog-metadata"
                        )
                        denied, _ = request_json(
                            url,
                            method="POST",
                            document={"expected_generation": 2},
                        )
                        self.assertEqual(denied, 403)
                        status, synced = request_json(
                            url,
                            method="POST",
                            document={"expected_generation": 2},
                            headers=headers,
                        )

        self.assertEqual(status, 200)
        self.assertEqual(synced["summary"]["generation"], 3)
        sync.assert_called_once_with(
            "profile_sync",
            expected_generation=2,
        )

    def test_profile_save_transports_optional_parameter_replacement(self) -> None:
        token = "profile-preset-management-token-for-tests"
        summary = RoleBindingProfileSummary(profile_entry_id="entry", profile_id="profile", display_name="Preset",
            strategy_id="strategy", strategy_version="1.0.0", target_type="dts", provider_id="dts.static-mesh",
            catalog_id=None, generation=1, semantic_hash="sha256:" + "a" * 64, updated_at="2026-10-13T00:00:00Z")
        detail = RoleBindingProfileDetail(summary=summary, role_count=1, assignments={}, catalog_drift={}, drift_count=0)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(SystemHostConfig(library_path=Path(temporary) / "library")) as system:
                with patch.object(system, "save_role_binding_profile", return_value=detail) as save:
                    with LocalApiServer(system, LocalApiConfig(management_token=token)) as server:
                        for parameters in ({}, {"strategy_parameters": None}, {"strategy_parameters": {}},
                                           {"strategy_parameters": {"spacing": 4}}):
                            status, _ = request_json(server.base_url + "/api/v1/role-binding-profiles",
                                method="POST", headers={"Authorization": f"Bearer {token}"},
                                document={"profile_id": "profile", "display_name": "Preset", "strategy_id": "strategy",
                                    "strategy_version": "1.0.0", "target_type": "dts", "expected_generation": 1,
                                    "assignments": {"tree": {"candidates": [{"asset_id": "tree", "weight": 1}]}},
                                    **parameters})
                            self.assertEqual(status, 201)
                            saved = save.call_args.args[0]
                            self.assertEqual(saved.replace_strategy_parameters, "strategy_parameters" in parameters)
                            self.assertEqual(saved.strategy_parameters, parameters.get("strategy_parameters"))

    def test_health_and_project_lifecycle_use_system_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                with LocalApiServer(system) as server:
                    status, health = request_json(server.base_url + "/api/v1/health")
                    self.assertEqual(status, 200)
                    self.assertEqual(health["state"], "running")

                    status, created = request_json(
                        server.base_url + "/api/v1/projects",
                        method="POST",
                        document={
                            "action": "create",
                            "project_path": str(root / "project"),
                            "project_name": "API Project",
                            "enable_blender_worker": False,
                            "enable_geospatial_worker": True,
                        },
                    )
                    self.assertEqual(status, 201)
                    self.assertEqual(created["state"], "active")
                    self.assertTrue(
                        (
                            root
                            / "project"
                            / "runtime"
                            / "geospatial-worker"
                        ).is_dir()
                    )

                    status, projects = request_json(
                        server.base_url + "/api/v1/projects"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(len(projects["items"]), 1)

                    session_id = str(created["session_id"])
                    run_url = (
                        server.base_url
                        + f"/api/v1/projects/{session_id}/plant-runs"
                    )
                    status, submitted = request_json(
                        run_url,
                        method="POST",
                        document={
                            "scene_revision_id": "scene_missing",
                            "region_set_id": "regions_missing",
                            "selected_region_ids": ["region_1"],
                            "strategy_package_hash": "sha256:" + "1" * 64,
                            "random_seed": 10,
                            "elevation_provider_id": "elevation.fixture",
                            "target_type": "dts",
                            "catalog_id": "catalog_1",
                            "export_provider_id": "dts.xml",
                            "offset": [0.0, 0.0, 0.0],
                        },
                    )
                    self.assertEqual(status, 202)
                    execution_id = str(submitted["execution_id"])
                    status, observed = request_json(
                        run_url + f"/{execution_id}"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(observed["execution_id"], execution_id)

                    status, closed = request_json(
                        server.base_url + f"/api/v1/projects/{session_id}",
                        method="DELETE",
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(closed["state"], "closed")

                    status, missing = request_json(
                        server.base_url + f"/api/v1/projects/{session_id}",
                        method="DELETE",
                    )
                    self.assertEqual(status, 404)
                    self.assertEqual(missing["error"]["code"], "project_not_found")

    def test_delete_recent_project_removes_registered_directory(self) -> None:
        token = "p" * 32
        headers = {"Authorization": f"Bearer {token}"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "delete-me"
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                )
            ) as system:
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                ) as server:
                    status, _created = request_json(
                        server.base_url + "/api/v1/projects",
                        method="POST",
                        document={
                            "action": "create",
                            "project_path": str(project_path),
                            "project_name": "Delete me",
                            "enable_blender_worker": False,
                        },
                    )
                    self.assertEqual(status, 201)
                    status, recent = request_json(
                        server.base_url + "/api/v1/recent-projects"
                    )
                    registry_id = str(recent["items"][0]["registry_id"])

                    status, removal = request_json(
                        server.base_url
                        + "/api/v1/recent-projects/"
                        + registry_id,
                        method="DELETE",
                        headers=headers,
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(removal["removed"])
                    self.assertTrue(removal["files_removed"])
                    self.assertFalse(project_path.exists())

                    status, recent = request_json(
                        server.base_url + "/api/v1/recent-projects"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(recent["items"], [])

    def test_rejects_non_loopback_binding_and_invalid_payload(self) -> None:
        from verdflow.local_api import LocalApiConfig

        with self.assertRaises(ValueError):
            LocalApiConfig(host="0.0.0.0").validate()

        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with LocalApiServer(system) as server:
                    status, response = request_json(
                        server.base_url + "/api/v1/projects",
                        method="POST",
                        document={"action": "create"},
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(response["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
