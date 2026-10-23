from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.contracts import JobCommandRef, JobStatus, JobSubmission  # noqa: E402
from verdflow.project_store import ProjectQueryStore, ProjectStore  # noqa: E402


class ProjectQueryStoreTests(unittest.TestCase):
    def test_reads_committed_job_updates_while_writer_remains_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "query.verdflow"
            with ProjectStore.create(root, "Query") as writer:
                command = writer.put_bytes(
                    b"{}",
                    media_type="application/json",
                    schema_id="verdflow.test-command",
                    schema_version="1",
                )
                now = datetime.now(UTC)
                job = writer.create_job(
                    JobSubmission(
                        command=JobCommandRef(
                            command_type="test.query",
                            command_version="1",
                            artifact_id=command.artifact_id,
                        ),
                        idempotency_key="query-store-test",
                    ),
                    now.isoformat(),
                )

                with ProjectQueryStore.open(root) as reader:
                    self.assertEqual(reader.project_info().name, "Query")
                    self.assertEqual(reader.get_job(job.job_id).status, JobStatus.QUEUED)
                    self.assertEqual(len(reader.list_job_events(job.job_id)), 1)
                    self.assertEqual(reader.get_artifact(command.artifact_id), command)
                    self.assertEqual(
                        reader.list_composition_export_records(),
                        (),
                    )
                    self.assertEqual(
                        reader.list_active_composition_output_executions(),
                        (),
                    )
                    self.assertIsNone(
                        reader.get_active_composition_output_execution()
                    )
                    with reader.open_artifact(command.artifact_id) as stream:
                        self.assertEqual(stream.read(), b"{}")
                    self.assertEqual(
                        reader.read_artifact_chunk(
                            command.artifact_id,
                            offset=1,
                            limit=1,
                        ),
                        b"}",
                    )

                    claimed = writer.claim_next_job(
                        worker_id="test-worker",
                        now=(now + timedelta(seconds=1)).isoformat(),
                        lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
                    )
                    self.assertIsNotNone(claimed)
                    self.assertEqual(
                        reader.get_job(job.job_id).status,
                        JobStatus.RUNNING,
                    )
                    self.assertEqual(len(reader.list_job_attempts(job.job_id)), 1)
                    self.assertEqual(len(reader.list_job_events(job.job_id)), 2)

    def test_job_pages_use_stable_created_at_and_id_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "pages.verdflow"
            with ProjectStore.create(root, "Pages") as writer:
                command = writer.put_bytes(b"{}", media_type="application/json")
                jobs = tuple(
                    writer.create_job(
                        JobSubmission(
                            command=JobCommandRef("fixture.task", "1", command.artifact_id),
                            idempotency_key=f"task:{index}",
                        ),
                        "2026-08-13T00:00:00+00:00",
                    )
                    for index in range(5)
                )
                with ProjectQueryStore.open(root) as reader:
                    first = reader.list_jobs(limit=2)
                    second = reader.list_jobs(before=first.next_cursor, limit=2)
                    third = reader.list_jobs(before=second.next_cursor, limit=2)

                observed = tuple(
                    item.job_id
                    for page in (first, second, third)
                    for item in page.items
                )
                self.assertEqual(len(observed), 5)
                self.assertEqual(set(observed), {item.job_id for item in jobs})
                self.assertEqual(len(observed), len(set(observed)))
                self.assertIsNone(third.next_cursor)


if __name__ == "__main__":
    unittest.main()
