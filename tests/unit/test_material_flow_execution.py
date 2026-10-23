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
    MaterialClassification,
    MaterialSelectionItem,
    MaterialSelectionMode,
    MaterialSelectionRequest,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    MaterialFlowExecutionRequest,
    MaterialFlowExecutionService,
)
from verdflow.project_store import ProjectQueryStore, ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _request(
    mode: MaterialConfirmationMode,
) -> MaterialFlowExecutionRequest:
    return MaterialFlowExecutionRequest(
        scene_revision_id="scene_1",
        analysis_provider_id="model.fixture",
        region_provider_id="region.fixture",
        confirmation_mode=mode,
    )


class MaterialFlowExecutionTests(unittest.TestCase):
    def test_project_store_persists_execution_for_read_only_queries(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "material-flow.verdflow"
            with ProjectStore.create(project_path, "Material Flow") as store:
                artifact = store.put_bytes(
                    b"{}",
                    media_type="application/vnd.verdflow.request+json",
                )
                created = store.create_material_flow_execution(
                    idempotency_key="flow:one",
                    request_artifact_id=artifact.artifact_id,
                    request_hash="sha256:test",
                )
                running = store.update_material_flow_execution(
                    replace(
                        created,
                        status=MaterialFlowExecutionStatus.RUNNING,
                    ),
                    expected_revision=created.revision,
                )
                with ProjectQueryStore.open(project_path) as query:
                    observed = query.get_material_flow_execution(
                        running.execution_id
                    )
                self.assertEqual(observed, running)
                self.assertEqual(observed.revision, 2)

    def test_automatic_mode_reaches_region_set(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(store, runtime)
        service = MaterialFlowExecutionService(store, runtime, stages)

        submitted = service.submit(_request(MaterialConfirmationMode.AUTOMATIC))
        while service.get(submitted.execution_id).status not in {
            MaterialFlowExecutionStatus.SUCCEEDED,
            MaterialFlowExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())

        completed = service.get(submitted.execution_id)
        self.assertEqual(completed.status, MaterialFlowExecutionStatus.SUCCEEDED)
        self.assertEqual(completed.stage, MaterialFlowExecutionStage.COMPLETED)
        self.assertEqual(completed.region_set_id, "regions_1")
        self.assertEqual(
            stages.calls,
            ["analyze", "load", "confirm:False", "extract", "finalize"],
        )

    def test_manual_mode_pauses_without_blocking_other_flows(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(store, runtime)
        service = MaterialFlowExecutionService(store, runtime, stages)
        manual = service.submit(
            _request(MaterialConfirmationMode.MANUAL),
            idempotency_key="flow:manual",
        )
        automatic = service.submit(
            _request(MaterialConfirmationMode.AUTOMATIC),
            idempotency_key="flow:automatic",
        )

        while service.get(manual.execution_id).status not in {
            MaterialFlowExecutionStatus.AWAITING_CONFIRMATION,
            MaterialFlowExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())
        self.assertEqual(
            service.get(manual.execution_id).status,
            MaterialFlowExecutionStatus.AWAITING_CONFIRMATION,
        )

        while service.get(automatic.execution_id).status not in {
            MaterialFlowExecutionStatus.SUCCEEDED,
            MaterialFlowExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())
        self.assertEqual(
            service.get(automatic.execution_id).status,
            MaterialFlowExecutionStatus.SUCCEEDED,
        )

        resumed = service.confirm(manual.execution_id)
        self.assertEqual(resumed.stage, MaterialFlowExecutionStage.EXTRACTION)
        self.assertEqual(service.confirm(manual.execution_id), resumed)
        while service.get(manual.execution_id).status not in {
            MaterialFlowExecutionStatus.SUCCEEDED,
            MaterialFlowExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())
        self.assertEqual(
            service.get(manual.execution_id).status,
            MaterialFlowExecutionStatus.SUCCEEDED,
        )

    def test_explicit_selection_pauses_without_submitting_analysis(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(store, runtime)
        service = MaterialFlowExecutionService(store, runtime, stages)
        submitted = service.submit(
            MaterialFlowExecutionRequest(
                scene_revision_id="scene_1",
                analysis_provider_id=None,
                region_provider_id="region.fixture",
                confirmation_mode=MaterialConfirmationMode.MANUAL,
                selection_mode=MaterialSelectionMode.MANUAL,
            )
        )

        self.assertTrue(service.run_once())
        paused = service.get(submitted.execution_id)
        self.assertEqual(
            paused.status,
            MaterialFlowExecutionStatus.AWAITING_CONFIRMATION,
        )
        self.assertIsNone(paused.analysis_job_id)
        self.assertEqual(stages.calls, [])

        selection = MaterialSelectionRequest(
            materials=(
                MaterialSelectionItem(
                    material_name="Grass",
                    classification=MaterialClassification.GENERAL_GREEN,
                ),
            )
        )
        resumed = service.confirm(submitted.execution_id, selection)
        self.assertEqual(resumed.stage, MaterialFlowExecutionStage.EXTRACTION)
        self.assertEqual(stages.calls, ["select:manual:None"])

    def test_initial_selection_skips_analysis_and_confirmation_pause(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(store, runtime)
        service = MaterialFlowExecutionService(store, runtime, stages)
        selection = MaterialSelectionRequest(
            materials=(
                MaterialSelectionItem(
                    material_name="Grass",
                    classification=MaterialClassification.GENERAL_GREEN,
                ),
            )
        )
        submitted = service.submit(
            MaterialFlowExecutionRequest(
                scene_revision_id="scene_1",
                analysis_provider_id=None,
                region_provider_id="region.fixture",
                confirmation_mode=MaterialConfirmationMode.MANUAL,
                selection_mode=MaterialSelectionMode.MANUAL,
                initial_selection=selection,
            )
        )

        self.assertTrue(service.run_once())
        running = service.get(submitted.execution_id)
        self.assertEqual(running.status, MaterialFlowExecutionStatus.RUNNING)
        self.assertEqual(running.stage, MaterialFlowExecutionStage.EXTRACTION)
        self.assertEqual(stages.calls, ["select:manual:None"])

    def test_confirmed_selection_is_reconstructed_from_rule_pack(self) -> None:
        store = _MemoryStore()
        store.artifacts["manifest"] = (
            b'{"facts":{"material_fact_report_artifact_id":"facts"}}'
        )
        store.artifacts["facts"] = (
            b'{"materials":['
            b'{"name":"Grass","face_count":12,"object_count":1,'
            b'"objects":["Ground"],"diffuse_color":[],"textures":[]},'
            b'{"name":"Concrete","face_count":20,"object_count":1,'
            b'"objects":["Road"],"diffuse_color":[],"textures":[]}'
            b"]}"
        )
        runtime = _SucceededRuntime()
        service = MaterialFlowExecutionService(
            store,
            runtime,
            _SucceededStages(store, runtime),
        )
        submitted = service.submit(
            MaterialFlowExecutionRequest(
                scene_revision_id="scene_1",
                analysis_provider_id=None,
                region_provider_id="region.fixture",
                confirmation_mode=MaterialConfirmationMode.MANUAL,
                selection_mode=MaterialSelectionMode.MANUAL,
                initial_selection=MaterialSelectionRequest(
                    materials=(
                        MaterialSelectionItem(
                            "Grass",
                            MaterialClassification.GENERAL_GREEN,
                        ),
                        MaterialSelectionItem(
                            "Concrete",
                            MaterialClassification.NOT_GREEN,
                        ),
                    )
                ),
            )
        )

        self.assertTrue(service.run_once())
        self.assertEqual(
            service.get_selection(submitted.execution_id),
            MaterialSelectionRequest(
                materials=(
                    MaterialSelectionItem(
                        "Grass",
                        MaterialClassification.GENERAL_GREEN,
                    ),
                    MaterialSelectionItem(
                        "Concrete",
                        MaterialClassification.NOT_GREEN,
                    ),
                )
            ),
        )

    def test_cancel_while_awaiting_confirmation_is_immediate(self) -> None:
        store = _MemoryStore()
        runtime = _SucceededRuntime()
        service = MaterialFlowExecutionService(
            store,
            runtime,
            _SucceededStages(store, runtime),
        )
        submitted = service.submit(_request(MaterialConfirmationMode.MANUAL))
        while service.get(submitted.execution_id).status is not (
            MaterialFlowExecutionStatus.AWAITING_CONFIRMATION
        ):
            self.assertTrue(service.run_once())
        cancelled = service.request_cancel(submitted.execution_id)
        self.assertEqual(cancelled.status, MaterialFlowExecutionStatus.CANCELLED)

    def test_reopen_keeps_the_submitted_analysis_job(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "resume-material-flow.verdflow"
            with ProjectStore.create(project_path, "Resume Material Flow") as store:
                runtime = JobRuntime(store)
                service = MaterialFlowExecutionService(
                    store,
                    runtime,
                    _QueuedStages(store, runtime),
                )
                submitted = service.submit(
                    _request(MaterialConfirmationMode.AUTOMATIC)
                )
                self.assertTrue(service.run_once())
                running = service.get(submitted.execution_id)
                first_job_id = running.analysis_job_id
                self.assertIsNotNone(first_job_id)

            with ProjectStore.open(project_path) as reopened:
                runtime = JobRuntime(reopened)
                stages = _QueuedStages(reopened, runtime)
                service = MaterialFlowExecutionService(reopened, runtime, stages)
                observed = service.get(submitted.execution_id)
                self.assertEqual(observed.analysis_job_id, first_job_id)
                self.assertFalse(service.run_once())
                self.assertFalse(stages.submitted)


class _MemoryStore:
    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}
        self.executions: dict[str, MaterialFlowExecutionInfo] = {}
        self.idempotency: dict[str, str] = {}

    def put_bytes(self, data: bytes, **_kwargs):
        artifact_id = f"artifact_{len(self.artifacts) + 1}"
        self.artifacts[artifact_id] = data
        return SimpleNamespace(artifact_id=artifact_id)

    def create_material_flow_execution(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
    ) -> MaterialFlowExecutionInfo:
        existing_id = self.idempotency.get(idempotency_key)
        if existing_id is not None:
            return self.executions[existing_id]
        execution_id = f"mfe_{len(self.executions) + 1}"
        info = MaterialFlowExecutionInfo(
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            request_artifact_id=request_artifact_id,
            request_hash=request_hash,
            status=MaterialFlowExecutionStatus.QUEUED,
            stage=MaterialFlowExecutionStage.ANALYSIS,
            revision=1,
            analysis_job_id=None,
            rule_pack_artifact_id=None,
            extraction_job_id=None,
            region_set_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-12T00:00:00+00:00",
            updated_at="2026-08-12T00:00:00+00:00",
        )
        self.executions[execution_id] = info
        self.idempotency[idempotency_key] = execution_id
        return info

    def get_material_flow_execution(self, execution_id: str):
        return self.executions[execution_id]

    def get_scene_revision(self, scene_revision_id: str):
        if scene_revision_id != "scene_1":
            raise KeyError(scene_revision_id)
        return SimpleNamespace(scene_manifest_artifact_id="manifest")

    def list_active_material_flow_executions(self, *, limit: int):
        active = tuple(
            info
            for info in self.executions.values()
            if info.status in {
                MaterialFlowExecutionStatus.QUEUED,
                MaterialFlowExecutionStatus.RUNNING,
                MaterialFlowExecutionStatus.CANCEL_REQUESTED,
            }
        )
        return active[:limit]

    def update_material_flow_execution(self, info, *, expected_revision: int):
        current = self.executions[info.execution_id]
        assert current.revision == expected_revision
        updated = replace(info, revision=expected_revision + 1)
        self.executions[info.execution_id] = updated
        return updated

    @contextmanager
    def open_artifact(self, artifact_id: str):
        yield BytesIO(self.artifacts[artifact_id])


class _SucceededRuntime:
    def __init__(self) -> None:
        self.jobs: dict[str, JobInfo] = {}

    def add(self, name: str) -> JobInfo:
        suffix = len(self.jobs) + 1
        job = JobInfo(
            job_id=f"job_{name}_{suffix}",
            idempotency_key=f"key_{name}_{suffix}",
            command=JobCommandRef(name, "1", "artifact_command"),
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
    def __init__(self, store: _MemoryStore, runtime: _SucceededRuntime) -> None:
        self.store = store
        self.runtime = runtime
        self.calls: list[str] = []

    def submit_material_analysis(self, _request, **_kwargs):
        self.calls.append("analyze")
        return self.runtime.add("analysis")

    def load_material_candidate(self, _job_id: str):
        self.calls.append("load")
        return SimpleNamespace(task_id="candidate")

    def finalize_material_candidate(self, _job_id: str, *, user_confirmed: bool):
        self.calls.append(f"confirm:{user_confirmed}")
        artifact = self.store.put_bytes(
            b'{"schema":"verdflow.region-rule-pack","version":"1","rules":[{"id":"green"}]}',
            media_type="application/vnd.verdflow.rule-pack+json",
        )
        return SimpleNamespace(
            rule_pack_artifact_id=artifact.artifact_id,
            rule_pack={"rules": [{"id": "green"}]},
        )

    def finalize_material_selection(
        self,
        _scene_revision_id,
        request,
        *,
        selection_mode,
        candidate_job_id=None,
    ):
        self.calls.append(
            f"select:{selection_mode.value}:{candidate_job_id}"
        )
        names = [
            item.material_name
            for item in request.materials
            if item.classification is MaterialClassification.GENERAL_GREEN
        ]
        document = (
            '{"schema":"verdflow.region-rule-pack","version":"1",'
            '"rules":[{"greenbelt_type":"general_greenbelt",'
            f'"match":{{"material_names":{names!r}}}}}]}}'
        ).replace("'", '"')
        artifact = self.store.put_bytes(
            document.encode("utf-8"),
            media_type="application/vnd.verdflow.rule-pack+json",
        )
        return SimpleNamespace(rule_pack_artifact_id=artifact.artifact_id)

    def submit_region_extraction(self, _request, **_kwargs):
        self.calls.append("extract")
        return self.runtime.add("extraction")

    def finalize_region_extraction(self, _job_id: str):
        self.calls.append("finalize")
        return SimpleNamespace(region_set_id="regions_1")


class _QueuedStages:
    def __init__(self, store: ProjectStore, runtime: JobRuntime) -> None:
        self.store = store
        self.runtime = runtime
        self.submitted = False

    def submit_material_analysis(self, _request, **_kwargs):
        self.submitted = True
        artifact = self.store.put_bytes(
            b"{}",
            media_type="application/vnd.verdflow.command+json",
        )
        return self.runtime.submit(
            JobCommandRef(
                "fixture.analyze-materials",
                "1",
                artifact.artifact_id,
            ),
            idempotency_key="fixture-material-analysis",
        )

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected stage call: {name}")


if __name__ == "__main__":
    unittest.main()
