from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    JobStatus,
    MaterialConfirmationMode,
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStage,
    MaterialFlowExecutionStatus,
    ScenePreparationInfo,
    ScenePreparationStage,
    ScenePreparationStatus,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    ScenePreparationExecutionService,
    ScenePreparationRequest,
)
from verdflow.project_store import ProjectQueryStore, ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _request(mode: MaterialConfirmationMode) -> ScenePreparationRequest:
    return ScenePreparationRequest(
        source_id="source_1",
        analysis_provider_id="model.fixture",
        region_provider_id="region.fixture",
        confirmation_mode=mode,
    )


class ScenePreparationExecutionTests(unittest.TestCase):
    def test_automatic_mode_reaches_region_set(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        material = _MaterialFlows(MaterialFlowExecutionStatus.SUCCEEDED)
        service = ScenePreparationExecutionService(
            store,
            runtime,
            _SucceededStages(runtime),
            material,
        )

        submitted = service.submit(_request(MaterialConfirmationMode.AUTOMATIC))
        while service.get(submitted.execution_id).status not in {
            ScenePreparationStatus.SUCCEEDED,
            ScenePreparationStatus.FAILED,
        }:
            self.assertTrue(service.run_once())

        completed = service.get(submitted.execution_id)
        self.assertEqual(completed.status, ScenePreparationStatus.SUCCEEDED)
        self.assertEqual(completed.stage, ScenePreparationStage.COMPLETED)
        self.assertEqual(completed.scene_revision_id, "scene_1")
        self.assertEqual(completed.region_set_id, "regions_1")

    def test_manual_mode_pauses_and_confirms_child(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        material = _MaterialFlows(
            MaterialFlowExecutionStatus.AWAITING_CONFIRMATION
        )
        service = ScenePreparationExecutionService(
            store,
            runtime,
            _SucceededStages(runtime),
            material,
        )
        submitted = service.submit(_request(MaterialConfirmationMode.MANUAL))
        while service.get(submitted.execution_id).status is not (
            ScenePreparationStatus.AWAITING_CONFIRMATION
        ):
            self.assertTrue(service.run_once())

        resumed = service.confirm(submitted.execution_id)
        self.assertEqual(resumed.status, ScenePreparationStatus.RUNNING)
        self.assertEqual(material.confirmed, 1)
        self.assertTrue(service.run_once())
        self.assertEqual(
            service.get(submitted.execution_id).status,
            ScenePreparationStatus.SUCCEEDED,
        )

    def test_reopen_keeps_submitted_inspection_job(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "resume-preparation.verdflow"
            with ProjectStore.create(project_path, "Resume") as store:
                source_artifact = store.put_bytes(
                    b"fbx",
                    media_type="model/vnd.autodesk.fbx",
                )
                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="fixture.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                runtime = JobRuntime(store)
                stages = _QueuedStages(store, runtime)
                service = ScenePreparationExecutionService(
                    store,
                    runtime,
                    stages,
                    _NeverMaterialFlows(),
                )
                submitted = service.submit(
                    replace(_request(MaterialConfirmationMode.AUTOMATIC), source_id=source.source_id)
                )
                self.assertTrue(service.run_once())
                first_job_id = service.get(submitted.execution_id).inspection_job_id
                self.assertIsNotNone(first_job_id)

            with ProjectStore.open(project_path) as reopened:
                runtime = JobRuntime(reopened)
                stages = _QueuedStages(reopened, runtime)
                service = ScenePreparationExecutionService(
                    reopened,
                    runtime,
                    stages,
                    _NeverMaterialFlows(),
                )
                observed = service.get(submitted.execution_id)
                self.assertEqual(observed.inspection_job_id, first_job_id)
                self.assertFalse(service.run_once())
                self.assertFalse(stages.submitted)
                with ProjectQueryStore.open(project_path) as query:
                    self.assertEqual(
                        query.get_scene_preparation(submitted.execution_id),
                        observed,
                    )


class _MemoryStore:
    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}
        self.executions: dict[str, ScenePreparationInfo] = {}

    def get_source(self, source_id: str):
        if source_id != "source_1":
            raise KeyError(source_id)
        return SimpleNamespace(source_id=source_id)

    def put_bytes(self, data: bytes, **_kwargs):
        artifact_id = f"artifact_{len(self.artifacts) + 1}"
        self.artifacts[artifact_id] = data
        return SimpleNamespace(artifact_id=artifact_id)

    def create_scene_preparation(self, **values):
        info = ScenePreparationInfo(
            execution_id="spe_1",
            idempotency_key=values["idempotency_key"],
            request_artifact_id=values["request_artifact_id"],
            request_hash=values["request_hash"],
            status=ScenePreparationStatus.QUEUED,
            stage=ScenePreparationStage.INSPECTION,
            revision=1,
            source_id=values["source_id"],
            inspection_job_id=None,
            scene_revision_id=None,
            material_flow_execution_id=None,
            region_set_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-12T00:00:00+00:00",
            updated_at="2026-08-12T00:00:00+00:00",
        )
        self.executions[info.execution_id] = info
        return info

    def get_scene_preparation(self, execution_id: str):
        return self.executions[execution_id]

    def list_active_scene_preparations(self, *, limit: int):
        return tuple(
            item
            for item in self.executions.values()
            if item.status in {
                ScenePreparationStatus.QUEUED,
                ScenePreparationStatus.RUNNING,
                ScenePreparationStatus.CANCEL_REQUESTED,
            }
        )[:limit]

    def update_scene_preparation(self, info, *, expected_revision: int):
        current = self.executions[info.execution_id]
        assert current.revision == expected_revision
        updated = replace(info, revision=expected_revision + 1)
        self.executions[updated.execution_id] = updated
        return updated

    @contextmanager
    def open_artifact(self, artifact_id: str):
        yield BytesIO(self.artifacts[artifact_id])


class _SucceededRuntime:
    def __init__(self) -> None:
        self.jobs: dict[str, JobInfo] = {}

    def add(self) -> JobInfo:
        job = JobInfo(
            job_id="job_inspection",
            idempotency_key="inspection",
            command=JobCommandRef("fixture.inspect", "1", "artifact_command"),
            status=JobStatus.SUCCEEDED,
            progress=1.0,
            stage="succeeded",
            max_attempts=1,
            attempt_count=1,
            worker_id="fixture",
            heartbeat_at=None,
            lease_expires_at=None,
            created_at="2026-08-12T00:00:00+00:00",
            started_at="2026-08-12T00:00:00+00:00",
            finished_at="2026-08-12T00:00:01+00:00",
            retention_class=JobRetentionClass.RUN,
            retention_seconds=None,
            purge_after=None,
            result_artifact_id="artifact_result",
            error=None,
        )
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobInfo:
        return self.jobs[job_id]

    def request_cancel(self, job_id: str) -> JobInfo:
        return self.jobs[job_id]


class _SucceededStages:
    def __init__(self, runtime: _SucceededRuntime) -> None:
        self.runtime = runtime

    def submit_scene_inspection(self, _source_id: str, **_kwargs):
        return self.runtime.add()

    def finalize_scene_inspection(self, _job_id: str):
        return SimpleNamespace(scene_revision_id="scene_1")


class _MaterialFlows:
    def __init__(self, status: MaterialFlowExecutionStatus) -> None:
        self.status = status
        self.confirmed = 0
        self.info: MaterialFlowExecutionInfo | None = None

    def submit(self, _request, **_kwargs):
        self.info = self._make(self.status)
        return self.info

    def get(self, _execution_id: str):
        assert self.info is not None
        return self.info

    def confirm(self, _execution_id: str):
        self.confirmed += 1
        self.info = self._make(MaterialFlowExecutionStatus.SUCCEEDED)
        return self.info

    def request_cancel(self, _execution_id: str):
        self.info = self._make(MaterialFlowExecutionStatus.CANCELLED)
        return self.info

    @staticmethod
    def _make(status: MaterialFlowExecutionStatus) -> MaterialFlowExecutionInfo:
        completed = status is MaterialFlowExecutionStatus.SUCCEEDED
        awaiting = status is MaterialFlowExecutionStatus.AWAITING_CONFIRMATION
        return MaterialFlowExecutionInfo(
            execution_id="mfe_1",
            idempotency_key="material",
            request_artifact_id="artifact_material",
            request_hash="sha256:material",
            status=status,
            stage=(
                MaterialFlowExecutionStage.COMPLETED
                if completed
                else MaterialFlowExecutionStage.CONFIRMATION
                if awaiting
                else MaterialFlowExecutionStage.ANALYSIS
            ),
            revision=1,
            analysis_job_id="job_material" if awaiting else None,
            rule_pack_artifact_id=None,
            extraction_job_id=None,
            region_set_id="regions_1" if completed else None,
            error_code=None,
            error_message=None,
            created_at="2026-08-12T00:00:00+00:00",
            updated_at="2026-08-12T00:00:00+00:00",
        )


class _QueuedStages:
    def __init__(self, store: ProjectStore, runtime: JobRuntime) -> None:
        self.store = store
        self.runtime = runtime
        self.submitted = False

    def submit_scene_inspection(self, _source_id: str, **_kwargs):
        self.submitted = True
        artifact = self.store.put_bytes(
            b"{}",
            media_type="application/vnd.verdflow.command+json",
        )
        return self.runtime.submit(
            JobCommandRef("fixture.inspect", "1", artifact.artifact_id),
            idempotency_key="fixture-inspection",
        )

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected stage call: {name}")


class _NeverMaterialFlows:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected Material Flow call: {name}")


if __name__ == "__main__":
    unittest.main()
