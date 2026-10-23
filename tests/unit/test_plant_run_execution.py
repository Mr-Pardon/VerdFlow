from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import json
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
    PlantRunExecutionInfo,
    PlantRunReviewPolicy,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    PlantRunContinuationRequest,
    PlantRunExecutionRequest,
    PlantRunExecutionService,
    load_plant_run_execution_request,
)
from verdflow.planting import canonical_json_bytes, semantic_hash, profile_generation_args  # noqa: E402
from verdflow.project_store import ProjectQueryStore, ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class PlantRunExecutionTests(unittest.TestCase):
    def test_profile_parameters_are_frozen_before_generation(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(runtime)
        profile = {"strategy": {"strategy_version": "2.0.0"}, "strategy_parameters": {"density": 4, "spacing": 2}}
        stages.freeze_generation_args = lambda _id, args: profile_generation_args(profile, args)
        stages.freeze_profile = lambda _id: json.loads(json.dumps(profile))
        service = PlantRunExecutionService(store, runtime, stages)
        submitted = service.submit(PlantRunExecutionRequest(
            scene_revision_id="scene_1", region_set_id="regions_1", selected_region_ids=("region_1",),
            strategy_package_hash="sha256:" + "1" * 64, random_seed=10,
            target_type="dts", catalog_id=None, profile_id="profile_1",
            review_policy=PlantRunReviewPolicy.REQUIRED,
            generation_args={"strategy_parameters": {"density": 8}},
        ))
        profile["strategy_parameters"]["spacing"] = 20
        profile["strategy"]["strategy_version"] = "2.1.0"
        frozen = service.get_request(submitted.execution_id)
        self.assertEqual(frozen.generation_args, {"strategy_parameters": {"density": 8, "spacing": 2}})
        self.assertEqual(frozen.random_seed, 10)
        self.assertEqual(frozen.profile_snapshot["strategy"]["strategy_version"], "2.0.0")
        self.assertEqual(frozen.profile_snapshot["strategy_parameters"]["spacing"], 2)
        for _ in range(8):
            if not service.run_once():
                break
        self.assertEqual(stages.assignment_request.profile_snapshot, frozen.profile_snapshot)

    def test_read_only_request_loader_validates_hash_and_schema(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "execution.verdflow"
            request = PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                target_type="dts",
                catalog_id="catalog_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
                generation_args={"strategy_parameters": {"density": 0.3}},
                binding_overrides={"tree": {"enabled": False}},
            )
            document = PlantRunExecutionService._document(request)
            with ProjectStore.create(project_path, "Execution") as store:
                for key, value, request_hash in (
                    ("valid", document, semantic_hash(document)),
                    ("bad-hash", document, "sha256:" + "0" * 64),
                    ("bad-schema", {**document, "schema": "unknown"},
                     semantic_hash({**document, "schema": "unknown"})),
                ):
                    artifact = store.put_bytes(
                        canonical_json_bytes(value), media_type="application/json",
                    )
                    execution = store.create_plant_run_execution(
                        idempotency_key=key,
                        request_artifact_id=artifact.artifact_id,
                        request_hash=request_hash,
                    )
                    with ProjectQueryStore.open(project_path) as query:
                        if key == "valid":
                            self.assertEqual(
                                load_plant_run_execution_request(query, execution.execution_id),
                                request,
                            )
                        else:
                            with self.assertRaises(ValueError):
                                load_plant_run_execution_request(query, execution.execution_id)

    def test_project_store_persists_execution_for_read_only_queries(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "execution.verdflow"
            with ProjectStore.create(project_path, "Execution") as store:
                artifact = store.put_bytes(
                    b"{}",
                    media_type="application/vnd.verdflow.request+json",
                )
                created = store.create_plant_run_execution(
                    idempotency_key="run:one",
                    request_artifact_id=artifact.artifact_id,
                    request_hash="sha256:test",
                )
                repeated = store.create_plant_run_execution(
                    idempotency_key="run:one",
                    request_artifact_id=artifact.artifact_id,
                    request_hash="sha256:test",
                )
                self.assertEqual(created, repeated)
                running = store.update_plant_run_execution(
                    replace(created, status=PlantRunExecutionStatus.RUNNING),
                    expected_revision=created.revision,
                )
                with ProjectQueryStore.open(project_path) as query:
                    observed = query.get_plant_run_execution(
                        running.execution_id
                    )
                self.assertEqual(observed, running)
                self.assertEqual(observed.revision, 2)

    def test_coordinator_composes_assignment_before_elevation(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(runtime)
        service = PlantRunExecutionService(store, runtime, stages)
        request = PlantRunExecutionRequest(
            scene_revision_id="scene_1",
            region_set_id="regions_1",
            selected_region_ids=("region_1",),
            strategy_package_hash="sha256:" + "1" * 64,
            random_seed=10,
            elevation_provider_id="elevation.fixture",
            target_type="dts",
            catalog_id="catalog_1",
            export_provider_id="dts.xml",
            offset=(0.0, 0.0, 0.0),
        )

        submitted = service.submit(request)
        while service.get(submitted.execution_id).status not in {
            PlantRunExecutionStatus.SUCCEEDED,
            PlantRunExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())

        completed = service.get(submitted.execution_id)
        self.assertEqual(completed.status, PlantRunExecutionStatus.SUCCEEDED)
        self.assertEqual(completed.stage, PlantRunExecutionStage.COMPLETED)
        self.assertEqual(
            stages.calls,
            ["generate", "assign:points_generated", "elevate:points_generated", "export:points_elevated:assignments_1"],
        )

    def test_cancel_before_first_stage_is_durable(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(runtime)
        service = PlantRunExecutionService(store, runtime, stages)
        submitted = service.submit(
            PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                elevation_provider_id="elevation.fixture",
                target_type="dts",
                catalog_id="catalog_1",
                export_provider_id="dts.xml",
                offset=(0.0, 0.0, 0.0),
            )
        )
        cancelled = service.request_cancel(submitted.execution_id)
        self.assertEqual(cancelled.status, PlantRunExecutionStatus.CANCELLED)
        self.assertFalse(stages.calls)

    def test_required_review_stops_then_continues_without_regeneration(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(runtime)
        service = PlantRunExecutionService(store, runtime, stages)
        submitted = service.submit(
            PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                target_type="dts",
                catalog_id="catalog_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
            )
        )

        while service.run_once():
            pass
        awaiting = service.get(submitted.execution_id)
        self.assertEqual(
            awaiting.status,
            PlantRunExecutionStatus.AWAITING_CONFIRMATION,
        )
        self.assertEqual(awaiting.stage, PlantRunExecutionStage.REVIEW)
        self.assertEqual(
            stages.calls,
            ["generate", "assign:points_generated"],
        )

        continuation = PlantRunContinuationRequest(
            elevation_provider_id="elevation.fixture",
            export_provider_id="dts.xml",
            offset=(0.0, 0.0, 0.0),
            selected_asset_ids=("asset_1",),
        )
        with self.assertRaisesRegex(ValueError, "unknown assets"):
            service.confirm(submitted.execution_id, replace(continuation, selected_asset_ids=("missing",)))
        self.assertEqual(service.get(submitted.execution_id), awaiting)
        confirmed = service.confirm(submitted.execution_id, continuation)
        repeated = service.confirm(submitted.execution_id, continuation)
        self.assertEqual(confirmed, repeated)
        with self.assertRaisesRegex(RuntimeError, "different output parameters"):
            service.confirm(submitted.execution_id, replace(continuation, selected_asset_ids=None))

        while service.get(submitted.execution_id).status not in {
            PlantRunExecutionStatus.SUCCEEDED,
            PlantRunExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())
        self.assertEqual(stages.output_request.selected_asset_ids, ("asset_1",))
        self.assertEqual(
            stages.calls,
            [
                "generate",
                "assign:points_generated",
                "elevate:points_generated",
                "export:points_elevated:assignments_1",
            ],
        )

    def test_review_can_complete_after_elevation_without_single_export(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(runtime)
        service = PlantRunExecutionService(store, runtime, stages)
        submitted = service.submit(
            PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                target_type="dts",
                catalog_id="catalog_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
            )
        )
        while service.run_once():
            pass

        service.confirm(
            submitted.execution_id,
            PlantRunContinuationRequest(
                elevation_provider_id="elevation.fixture",
                export_provider_id="dts.xml",
                offset=(0.0, 0.0, 0.0),
                complete_after_elevation=True,
            ),
        )
        while service.get(submitted.execution_id).status not in {
            PlantRunExecutionStatus.SUCCEEDED,
            PlantRunExecutionStatus.FAILED,
        }:
            self.assertTrue(service.run_once())

        completed = service.get(submitted.execution_id)
        self.assertEqual(completed.status, PlantRunExecutionStatus.SUCCEEDED)
        self.assertEqual(completed.elevation_point_set_id, "points_elevated")
        self.assertIsNone(completed.export_job_id)
        self.assertIsNone(completed.export_id)
        self.assertEqual(
            stages.calls,
            [
                "generate",
                "assign:points_generated",
                "elevate:points_generated",
            ],
        )

    def test_review_rejects_conflicting_second_confirmation(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        service = PlantRunExecutionService(
            store,
            runtime,
            _SucceededStages(runtime),
        )
        submitted = service.submit(
            PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                target_type="dts",
                catalog_id="catalog_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
            )
        )
        while service.run_once():
            pass
        service.confirm(
            submitted.execution_id,
            PlantRunContinuationRequest(
                elevation_provider_id="elevation.fixture",
                export_provider_id="dts.xml",
                offset=(0.0, 0.0, 0.0),
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "different output"):
            service.confirm(
                submitted.execution_id,
                PlantRunContinuationRequest(
                    elevation_provider_id="elevation.fixture",
                    export_provider_id="dts.xml",
                    offset=(1.0, 0.0, 0.0),
                ),
            )

    def test_reopen_keeps_the_submitted_stage_job(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "resume.verdflow"
            request = PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                elevation_provider_id="elevation.fixture",
                target_type="dts",
                catalog_id="catalog_1",
                export_provider_id="dts.xml",
                offset=(0.0, 0.0, 0.0),
            )
            with ProjectStore.create(project_path, "Resume") as store:
                runtime = JobRuntime(store)
                service = PlantRunExecutionService(
                    store,
                    runtime,
                    _QueuedStages(store, runtime),
                )
                submitted = service.submit(request)
                self.assertTrue(service.run_once())
                running = service.get(submitted.execution_id)
                first_job_id = running.generation_job_id
                self.assertIsNotNone(first_job_id)

            with ProjectStore.open(project_path) as reopened:
                runtime = JobRuntime(reopened)
                stages = _QueuedStages(reopened, runtime)
                service = PlantRunExecutionService(reopened, runtime, stages)
                observed = service.get(submitted.execution_id)
                self.assertEqual(observed.generation_job_id, first_job_id)
                self.assertFalse(service.run_once())
                self.assertFalse(stages.submitted)

    def test_retry_assignment_reuses_generated_point_set(self) -> None:
        store = _MemoryExecutionStore()
        runtime = _SucceededRuntime()
        stages = _SucceededStages(runtime)
        service = PlantRunExecutionService(store, runtime, stages)
        submitted = service.submit(
            PlantRunExecutionRequest(
                scene_revision_id="scene_1",
                region_set_id="regions_1",
                selected_region_ids=("region_1",),
                strategy_package_hash="sha256:" + "1" * 64,
                random_seed=10,
                target_type="dts",
                catalog_id="catalog_1",
                review_policy=PlantRunReviewPolicy.REQUIRED,
            )
        )
        previous = runtime.add("interrupted-assignment")
        interrupted = replace(
            previous,
            status=JobStatus.INTERRUPTED,
            stage="interrupted",
            result_artifact_id=None,
        )
        runtime.jobs[interrupted.job_id] = interrupted
        store.info = replace(
            submitted,
            status=PlantRunExecutionStatus.FAILED,
            stage=PlantRunExecutionStage.ASSIGNMENT,
            generation_job_id="job_generation",
            generated_point_set_id="points_generated",
            assignment_job_id=interrupted.job_id,
            error_code="stage_failed",
            error_message="interrupted",
        )

        retried = service.retry_assignment(submitted.execution_id)

        self.assertEqual(retried.status, PlantRunExecutionStatus.QUEUED)
        self.assertEqual(retried.generated_point_set_id, "points_generated")
        self.assertEqual(retried.assignment_job_id, "job_assignment")
        self.assertIsNone(retried.error_code)
        self.assertEqual(stages.calls, ["assign:points_generated"])



class _MemoryExecutionStore:
    def list_asset_assignment_summaries(self, assignment_set_id):
        return (SimpleNamespace(asset_id="asset_1", point_count=1),)

    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}
        self.info: PlantRunExecutionInfo | None = None

    def put_bytes(self, data: bytes, **_kwargs):
        artifact_id = f"artifact_{len(self.artifacts) + 1}"
        self.artifacts[artifact_id] = data
        return SimpleNamespace(artifact_id=artifact_id)

    def create_plant_run_execution(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
    ) -> PlantRunExecutionInfo:
        if self.info is not None:
            return self.info
        self.info = PlantRunExecutionInfo(
            execution_id="pre_1",
            idempotency_key=idempotency_key,
            request_artifact_id=request_artifact_id,
            request_hash=request_hash,
            continuation_artifact_id=None,
            continuation_hash=None,
            status=PlantRunExecutionStatus.QUEUED,
            stage=PlantRunExecutionStage.GENERATION,
            revision=1,
            generation_job_id=None,
            generated_point_set_id=None,
            assignment_job_id=None,
            asset_assignment_set_id=None,
            elevation_job_id=None,
            elevation_point_set_id=None,
            export_job_id=None,
            export_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-11T00:00:00+00:00",
            updated_at="2026-08-11T00:00:00+00:00",
        )
        return self.info

    def get_plant_run_execution(self, execution_id: str) -> PlantRunExecutionInfo:
        assert self.info is not None and self.info.execution_id == execution_id
        return self.info

    def list_active_plant_run_executions(self, *, limit: int):
        del limit
        if self.info is None or self.info.status in {
            PlantRunExecutionStatus.CANCELLED,
            PlantRunExecutionStatus.FAILED,
            PlantRunExecutionStatus.SUCCEEDED,
        }:
            return ()
        return (self.info,)

    def update_plant_run_execution(self, info, *, expected_revision: int):
        assert self.info is not None
        assert self.info.revision == expected_revision
        self.info = replace(info, revision=expected_revision + 1)
        return self.info

    @contextmanager
    def open_artifact(self, artifact_id: str):
        yield BytesIO(self.artifacts[artifact_id])


class _SucceededRuntime:
    def __init__(self) -> None:
        self.jobs: dict[str, JobInfo] = {}

    def add(self, name: str) -> JobInfo:
        job = JobInfo(
            job_id=f"job_{name}",
            idempotency_key=f"key_{name}",
            command=JobCommandRef(name, "1", "artifact_command"),
            status=JobStatus.SUCCEEDED,
            progress=1.0,
            stage="succeeded",
            max_attempts=1,
            attempt_count=1,
            worker_id="fixture",
            heartbeat_at=None,
            lease_expires_at=None,
            created_at="2026-08-11T00:00:00+00:00",
            started_at="2026-08-11T00:00:00+00:00",
            finished_at="2026-08-11T00:00:01+00:00",
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
    def freeze_profile(self, _profile_id):
        return None

    def freeze_generation_args(self, _profile_id, args):
        return args

    def freeze_role_assignments(self, _profile_id, overrides):
        return overrides

    def __init__(self, runtime: _SucceededRuntime) -> None:
        self.runtime = runtime
        self.calls: list[str] = []

    def submit_planting_generation(self, request, **_kwargs):
        del request
        self.calls.append("generate")
        return self.runtime.add("generation")

    def finalize_generated_points(self, _job_id: str):
        return SimpleNamespace(point_set_id="points_generated")

    def submit_asset_assignment(self, request, **_kwargs):
        self.assignment_request = request
        self.calls.append(f"assign:{request.point_set_id}")
        return self.runtime.add("assignment")

    def finalize_asset_assignment(self, _job_id: str):
        return SimpleNamespace(asset_assignment_set_id="assignments_1")

    def submit_elevation_sampling(self, request, **_kwargs):
        self.calls.append(f"elevate:{request.parent_point_set_id}")
        return self.runtime.add("elevation")

    def finalize_elevation(self, _job_id: str):
        return SimpleNamespace(point_set_id="points_elevated")

    def submit_output_export(self, request, **_kwargs):
        self.output_request = request
        self.calls.append(
            f"export:{request.point_set_id}:{request.asset_assignment_set_id}"
        )
        return self.runtime.add("export")

    def finalize_output_export(self, _job_id: str):
        return SimpleNamespace(export_id="export_1")


class _QueuedStages:
    def freeze_profile(self, _profile_id):
        return None

    def freeze_generation_args(self, _profile_id, args):
        return args

    def freeze_role_assignments(self, _profile_id, overrides):
        return overrides

    def __init__(self, store: ProjectStore, runtime: JobRuntime) -> None:
        self.store = store
        self.runtime = runtime
        self.submitted = False

    def submit_planting_generation(self, _request, **_kwargs):
        self.submitted = True
        artifact = self.store.put_bytes(
            b"{}",
            media_type="application/vnd.verdflow.command+json",
        )
        return self.runtime.submit(
            JobCommandRef(
                "fixture.generate",
                "1",
                artifact.artifact_id,
            ),
            idempotency_key="fixture-generation",
        )

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected stage call: {name}")


if __name__ == "__main__":
    unittest.main()
