from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch


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
]

from verdflow.contracts import (  # noqa: E402
    JobCommandRef, JobSubmission, PlantRunExecutionStage, PlantRunReviewPolicy,
)
from verdflow.core import PlantRunExecutionRequest, PlantRunExecutionService  # noqa: E402
from verdflow.core_host import CoreHostConfig  # noqa: E402
from verdflow.planting import canonical_json_bytes, semantic_hash  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.system_host import (  # noqa: E402
    ProjectResourceNotFoundError,
    SystemHost,
    SystemHostConfig,
)
from verdflow.system_host.runtime import ProjectRuntime, _plant_run_stage_job_id  # noqa: E402


class BlockingRegistry:
    def __init__(self, release: Event) -> None:
        self.release = release
        self.cancelled_job_id: str | None = None

    def request_cancel(self, job: object) -> bool:
        self.cancelled_job_id = str(getattr(job, "job_id"))
        self.release.set()
        return True


class BlockingServiceLoop:
    def __init__(self, entered: Event, release: Event) -> None:
        self.entered = entered
        self.release = release
        self.registry = BlockingRegistry(release)
        self._blocked_once = False

    def run_once(self) -> None:
        if self._blocked_once:
            return
        self._blocked_once = True
        self.entered.set()
        if not self.release.wait(5.0):
            raise TimeoutError("test worker was not released")


class FakeApplication:
    def __init__(self, project: object) -> None:
        self._project = project

    def project_info(self) -> object:
        return self._project


class FakeCoreHost:
    project: object
    service: BlockingServiceLoop
    execution_cancelled = Event()
    material_flow_cancelled = Event()
    scene_preparation_cancelled = Event()

    def __init__(self) -> None:
        self.application = FakeApplication(type(self).project)
        self.service_loop = type(self).service

    @classmethod
    def open(cls, *_: object, **__: object) -> FakeCoreHost:
        return cls()

    @classmethod
    def create(cls, *_: object, **__: object) -> FakeCoreHost:
        return cls()

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def cancel_plant_run_execution(self, _execution_id: str) -> object:
        type(self).execution_cancelled.set()
        return object()

    def cancel_material_flow_execution(self, _execution_id: str) -> object:
        type(self).material_flow_cancelled.set()
        return object()

    def cancel_scene_preparation(self, _execution_id: str) -> object:
        type(self).scene_preparation_cancelled.set()
        return object()


class ProjectRuntimeChannelTests(unittest.TestCase):
    def test_preview_input_bypasses_a_blocked_execution_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            request = PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1", "region_2"),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=17,
                target_type="dts",
                profile_id="profile_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
                generation_args={"strategy_parameters": {"density": 0.5}},
                binding_overrides={"tree": {"enabled": True}},
            )
            document = PlantRunExecutionService._document(request)
            with ProjectStore.create(project_path, "Blocked") as store:
                project = store.project_info()
                artifact = store.put_bytes(
                    canonical_json_bytes(document),
                    media_type="application/vnd.verdflow.request+json",
                )
                execution = store.create_plant_run_execution(
                    idempotency_key="blocked-preview",
                    request_artifact_id=artifact.artifact_id,
                    request_hash=semantic_hash(document),
                )

            entered, release = Event(), Event()
            FakeCoreHost.project = project
            FakeCoreHost.service = BlockingServiceLoop(entered, release)
            runtime = ProjectRuntime(
                CoreHostConfig(project_path=project_path, enable_blender_worker=False),
                library_path=root / "library",
                create_name=None,
                idle_cycle_seconds=0.01,
            )
            with patch("verdflow.system_host.runtime.CoreHost", FakeCoreHost):
                runtime.start(5.0)
                try:
                    self.assertTrue(entered.wait(5.0))
                    with SystemHost(SystemHostConfig(
                        library_path=root / "library",
                        project_call_timeout_seconds=0.5,
                    )) as system:
                        system._projects["session_1"] = runtime
                        try:
                            restored = system.get_plant_preview_input(
                                "session_1", execution.execution_id,
                            )
                            with self.assertRaises(ProjectResourceNotFoundError):
                                system.get_plant_preview_input("session_1", "missing")
                        finally:
                            system._projects.pop("session_1")
                    self.assertFalse(release.is_set())
                    self.assertEqual(restored.selected_region_ids, request.selected_region_ids)
                    self.assertEqual(restored.random_seed, request.random_seed)
                    self.assertEqual(restored.profile_id, request.profile_id)
                    self.assertEqual(restored.generation_args, request.generation_args)
                    self.assertEqual(restored.binding_overrides, request.binding_overrides)
                finally:
                    release.set()
                    runtime.close(5.0)

    def test_query_and_cancel_bypass_a_blocked_owner_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            with ProjectStore.create(project_path, "Blocked") as store:
                project = store.project_info()
                artifact = store.put_bytes(
                    b"{}",
                    media_type="application/json",
                    schema_id="verdflow.test-command",
                    schema_version="1",
                )
                job = store.create_job(
                    JobSubmission(
                        command=JobCommandRef(
                            command_type="test.blocking",
                            command_version="1",
                            artifact_id=artifact.artifact_id,
                        ),
                        idempotency_key="blocked-owner",
                    ),
                    "2026-08-11T00:00:00+00:00",
                )
                request = store.put_bytes(
                    b"{}",
                    media_type="application/vnd.verdflow.request+json",
                )
                execution = store.create_plant_run_execution(
                    idempotency_key="blocked-execution",
                    request_artifact_id=request.artifact_id,
                    request_hash="sha256:blocked-execution",
                )
                execution = store.update_plant_run_execution(
                    replace(execution, stage=PlantRunExecutionStage.REVIEW),
                    expected_revision=execution.revision,
                )
                for stage in PlantRunExecutionStage:
                    with self.subTest(stage=stage):
                        self.assertIsNone(_plant_run_stage_job_id(replace(execution, stage=stage)))
                material_flow = store.create_material_flow_execution(
                    idempotency_key="blocked-material-flow",
                    request_artifact_id=request.artifact_id,
                    request_hash="sha256:blocked-material-flow",
                )
                source_artifact = store.put_bytes(
                    b"source",
                    media_type="model/vnd.autodesk.fbx",
                )
                source = store.register_source(
                    kind="fbx",
                    original_name="blocked.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                preparation = store.create_scene_preparation(
                    idempotency_key="blocked-preparation",
                    request_artifact_id=request.artifact_id,
                    request_hash="sha256:blocked-preparation",
                    source_id=source.source_id,
                )

            entered = Event()
            release = Event()
            service = BlockingServiceLoop(entered, release)
            FakeCoreHost.project = project
            FakeCoreHost.service = service
            FakeCoreHost.execution_cancelled = Event()
            FakeCoreHost.material_flow_cancelled = Event()
            FakeCoreHost.scene_preparation_cancelled = Event()
            runtime = ProjectRuntime(
                CoreHostConfig(
                    project_path=project_path,
                    enable_blender_worker=False,
                ),
                library_path=root / "library",
                create_name=None,
                idle_cycle_seconds=0.01,
            )

            with patch(
                "verdflow.system_host.runtime.CoreHost",
                FakeCoreHost,
            ):
                runtime.start(5.0)
                try:
                    self.assertTrue(entered.wait(5.0))
                    observed = runtime.query(
                        lambda store: store.get_job(job.job_id),
                        timeout_seconds=1.0,
                    )
                    self.assertEqual(observed.job_id, job.job_id)

                    cancellation_job, accepted = runtime.request_cancel(
                        job.job_id,
                        timeout_seconds=1.0,
                    )
                    self.assertTrue(accepted)
                    self.assertEqual(cancellation_job.job_id, job.job_id)
                    self.assertEqual(
                        service.registry.cancelled_job_id,
                        job.job_id,
                    )

                    observed_execution, accepted, worker_signal_sent = (
                        runtime.request_plant_run_cancel(
                            execution.execution_id,
                            timeout_seconds=1.0,
                        )
                    )
                    self.assertTrue(accepted)
                    self.assertFalse(worker_signal_sent)
                    self.assertEqual(
                        observed_execution.execution_id,
                        execution.execution_id,
                    )

                    observed_flow = runtime.query(
                        lambda store: store.get_material_flow_execution(
                            material_flow.execution_id
                        ),
                        timeout_seconds=1.0,
                    )
                    self.assertEqual(
                        observed_flow.execution_id,
                        material_flow.execution_id,
                    )
                    observed_preparation, accepted, worker_signal_sent = (
                        runtime.request_scene_preparation_cancel(
                            preparation.execution_id,
                            timeout_seconds=1.0,
                        )
                    )
                    self.assertTrue(accepted)
                    self.assertFalse(worker_signal_sent)
                    self.assertEqual(
                        observed_preparation.execution_id,
                        preparation.execution_id,
                    )
                    observed_flow, accepted, worker_signal_sent = (
                        runtime.request_material_flow_cancel(
                            material_flow.execution_id,
                            timeout_seconds=1.0,
                        )
                    )
                    self.assertTrue(accepted)
                    self.assertFalse(worker_signal_sent)
                    self.assertEqual(
                        observed_flow.execution_id,
                        material_flow.execution_id,
                    )
                finally:
                    release.set()
                    self.assertTrue(
                        FakeCoreHost.execution_cancelled.wait(2.0)
                    )
                    self.assertTrue(
                        FakeCoreHost.material_flow_cancelled.wait(2.0)
                    )
                    self.assertTrue(
                        FakeCoreHost.scene_preparation_cancelled.wait(2.0)
                    )
                    runtime.close(5.0)


if __name__ == "__main__":
    unittest.main()
