from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    JobCommandRef,
    JobRetentionClass,
    MaterialFlowExecutionStatus,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRetentionPolicy,
    JobRetentionSweeper,
    JobRuntime,
)
from verdflow.project_store import (  # noqa: E402
    ArtifactNotFoundError,
    JobNotFoundError,
    ProjectStore,
)
from verdflow.project_store.retention import (  # noqa: E402
    ARTIFACT_REFERENCE_COLUMNS,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class ManualClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class RetentionTests(unittest.TestCase):
    def test_material_flow_protects_expired_candidate_job(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "material-flow-retention.verdflow"
            with ProjectStore.create(project_path, "Material Flow Retention") as store:
                clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    retention_policy=JobRetentionPolicy(
                        candidate=timedelta(seconds=5),
                    ),
                )
                command = store.put_bytes(
                    b"candidate-command",
                    media_type="application/json",
                )
                job = runtime.submit(
                    JobCommandRef("test.candidate", "1", command.artifact_id),
                    idempotency_key="material-flow-candidate",
                    retention_class=JobRetentionClass.CANDIDATE,
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                runtime.succeed(claimed.lease)
                request = store.put_bytes(
                    b"{}",
                    media_type="application/vnd.verdflow.request+json",
                )
                flow = store.create_material_flow_execution(
                    idempotency_key="material-flow-retention",
                    request_artifact_id=request.artifact_id,
                    request_hash="sha256:material-flow-retention",
                )
                store.update_material_flow_execution(
                    replace(
                        flow,
                        status=MaterialFlowExecutionStatus.RUNNING,
                        analysis_job_id=job.job_id,
                    ),
                    expected_revision=flow.revision,
                )

                clock.advance(timedelta(seconds=6))
                result = JobRetentionSweeper(
                    store,
                    clock=clock,
                    orphan_grace=timedelta(seconds=1),
                ).sweep_once()

                self.assertEqual(result.removed_job_count, 0)
                self.assertEqual(result.protected_job_count, 1)
                protected = runtime.get(job.job_id)
                self.assertEqual(protected.retention_class, JobRetentionClass.AUDIT)
                self.assertIsNone(protected.purge_after)

    def test_expired_temporary_job_and_objects_are_collected_idempotently(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "temporary.verdflow"
            with ProjectStore.create(project_path, "Temporary") as store:
                clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    retention_policy=JobRetentionPolicy(
                        temporary=timedelta(seconds=5),
                    ),
                )
                command = store.put_bytes(
                    b"temporary-command",
                    media_type="application/json",
                )
                job = runtime.submit(
                    JobCommandRef("test.temporary", "1", command.artifact_id),
                    idempotency_key="temporary-job",
                    retention_class=JobRetentionClass.TEMPORARY,
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                result = store.put_bytes(
                    b"temporary-result",
                    media_type="application/json",
                )
                completed = runtime.succeed(
                    claimed.lease,
                    result_artifact_id=result.artifact_id,
                )
                self.assertIsNotNone(completed.purge_after)

                clock.advance(timedelta(seconds=6))
                sweeper = JobRetentionSweeper(
                    store,
                    clock=clock,
                    orphan_grace=timedelta(seconds=1),
                )
                first = sweeper.sweep_once()
                self.assertEqual(first.removed_job_count, 1)
                self.assertEqual(first.removed_artifact_count, 2)
                self.assertEqual(first.removed_object_count, 2)
                with self.assertRaises(JobNotFoundError):
                    runtime.get(job.job_id)

                repeated = sweeper.sweep_once()
                self.assertEqual(repeated.removed_job_count, 0)
                self.assertEqual(repeated.removed_artifact_count, 0)
                self.assertEqual(repeated.removed_object_count, 0)

    def test_object_gc_failure_leaves_only_recoverable_orphan_blobs(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "gc-failure.verdflow"
            with ProjectStore.create(project_path, "GC failure") as store:
                clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    retention_policy=JobRetentionPolicy(
                        temporary=timedelta(seconds=5),
                    ),
                )
                command = store.put_bytes(
                    b"gc-failure-command",
                    media_type="application/json",
                )
                job = runtime.submit(
                    JobCommandRef("test.gc-failure", "1", command.artifact_id),
                    idempotency_key="gc-failure",
                    retention_class=JobRetentionClass.TEMPORARY,
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                runtime.succeed(claimed.lease)
                clock.advance(timedelta(seconds=6))
                sweeper = JobRetentionSweeper(
                    store,
                    clock=clock,
                    orphan_grace=timedelta(seconds=1),
                )

                with patch.object(
                    store,
                    "collect_garbage",
                    side_effect=RuntimeError("injected GC failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        sweeper.sweep_once()

                with self.assertRaises(JobNotFoundError):
                    runtime.get(job.job_id)
                self.assertEqual(store.artifact_count(), 0)
                self.assertEqual(store.object_count(), 1)

                recovered = sweeper.sweep_once()
                self.assertEqual(recovered.removed_job_count, 0)
                self.assertEqual(recovered.removed_object_count, 1)
                self.assertEqual(store.object_count(), 0)

    def test_run_job_and_its_artifacts_are_never_expired(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "run.verdflow"
            with ProjectStore.create(project_path, "Run") as store:
                clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))
                runtime = JobRuntime(store, clock=clock)
                command = store.put_bytes(b"run", media_type="application/json")
                job = runtime.submit(
                    JobCommandRef("test.run", "1", command.artifact_id),
                    idempotency_key="run-job",
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                runtime.succeed(claimed.lease)
                clock.advance(timedelta(days=3650))

                result = JobRetentionSweeper(
                    store,
                    clock=clock,
                    orphan_grace=timedelta(seconds=1),
                ).sweep_once()

                self.assertEqual(result.removed_job_count, 0)
                self.assertEqual(runtime.get(job.job_id).job_id, job.job_id)
                self.assertEqual(store.artifact_count(), 1)

    def test_scene_manifest_child_artifacts_survive_orphan_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "scene-facts.verdflow"
            with ProjectStore.create(project_path, "Scene facts") as store:
                source_artifact = store.put_bytes(
                    b"fbx",
                    media_type="application/octet-stream",
                )
                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="scene.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                frame = store.create_coordinate_frame(
                    name="Scene",
                    handedness="right-handed",
                    up_axis="+Z",
                    linear_unit="meter",
                    crs_kind=CoordinateReferenceKind.UNKNOWN,
                )
                inspection = store.put_bytes(
                    b'{"schema":"verdflow.scene-inspection-manifest"}',
                    media_type="application/json",
                )
                material_facts = store.put_bytes(
                    b'{"materials":[{"name":"Grass"}]}',
                    media_type="application/json",
                )
                manifest = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.scene-manifest",
                            "facts": {
                                "scene_inspection_manifest_artifact_id": (
                                    inspection.artifact_id
                                ),
                                "material_fact_report_artifact_id": (
                                    material_facts.artifact_id
                                ),
                            },
                        },
                        sort_keys=True,
                    ).encode("utf-8"),
                    media_type="application/json",
                )
                store.create_scene_revision(
                    source_id=source.source_id,
                    importer_id="test",
                    importer_version="1",
                    coordinate_frame_id=frame.coordinate_frame_id,
                    scene_manifest_artifact_id=manifest.artifact_id,
                )
                orphan = store.put_bytes(
                    b"orphan",
                    media_type="application/octet-stream",
                )

                result = JobRetentionSweeper(
                    store,
                    clock=ManualClock(datetime(2030, 1, 1, tzinfo=UTC)),
                    orphan_grace=timedelta(seconds=1),
                ).sweep_once()

                self.assertEqual(result.removed_artifact_count, 1)
                self.assertEqual(
                    store.get_artifact(inspection.artifact_id).artifact_id,
                    inspection.artifact_id,
                )
                self.assertEqual(
                    store.get_artifact(material_facts.artifact_id).artifact_id,
                    material_facts.artifact_id,
                )
                with self.assertRaises(ArtifactNotFoundError):
                    store.get_artifact(orphan.artifact_id)

    def test_artifact_reference_registry_covers_every_foreign_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "registry.verdflow"
            with ProjectStore.create(project_path, "Registry"):
                connection = sqlite3.connect(project_path / "project.db")
                try:
                    tables = tuple(
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    )
                    references = {
                        (table_name, str(row[3]))
                        for table_name in tables
                        for row in connection.execute(
                            f"PRAGMA foreign_key_list({table_name})"
                        )
                        if str(row[2]) == "artifacts"
                        and str(row[4]) == "artifact_id"
                    }
                finally:
                    connection.close()

                self.assertEqual(references, set(ARTIFACT_REFERENCE_COLUMNS))


if __name__ == "__main__":
    unittest.main()
