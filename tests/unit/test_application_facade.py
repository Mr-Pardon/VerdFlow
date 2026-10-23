from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    JobInfo,
    JobStatus,
    SourceAssemblyMemberSpec,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    ApplicationFacade,
    CommandDocument,
    JobRuntime,
    SourceRegistration,
)
from verdflow.project_store import ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class CancellationRecorder:
    def __init__(self) -> None:
        self.jobs: list[JobInfo] = []

    def request_cancel(self, job: JobInfo) -> bool:
        self.jobs.append(job)
        return True


class ApplicationFacadeTests(unittest.TestCase):
    def test_registers_sources_and_assemblies_through_use_cases(self) -> None:
        with self._store() as store:
            facade = ApplicationFacade(store, JobRuntime(store))
            artifact = store.put_bytes(
                b"fixture",
                media_type="model/vnd.autodesk.fbx",
            )

            source = facade.register_source(
                SourceRegistration(SourceKind.FBX, "fixture.fbx", artifact.artifact_id)
            )
            assembly = facade.create_source_assembly(
                original_name="two members",
                members=(
                    SourceAssemblyMemberSpec(source.source_id, IDENTITY),
                    SourceAssemblyMemberSpec(source.source_id, IDENTITY),
                ),
            )

            self.assertEqual(facade.get_source(source.source_id), source)
            self.assertEqual(assembly.kind, SourceKind.ASSEMBLY)
            self.assertEqual(len(facade.source_assembly_members(assembly.source_id)), 2)

    def test_submits_command_bytes_and_routes_running_cancellation(self) -> None:
        with self._store() as store:
            runtime = JobRuntime(store)
            cancellation = CancellationRecorder()
            facade = ApplicationFacade(
                store,
                runtime,
                cancellation_port=cancellation,
            )
            job = facade.submit_command(
                CommandDocument(
                    command_type="verdflow.test.command",
                    command_version="1",
                    content=b'{"schema":"test"}',
                ),
                idempotency_key="facade-command",
                max_attempts=2,
            )
            claimed = runtime.claim_next(
                "test-worker",
                command_types=("verdflow.test.command",),
            )
            assert claimed is not None

            cancelling = facade.cancel_job(job.job_id)

            self.assertEqual(cancelling.status, JobStatus.CANCELLING)
            self.assertEqual([row.job_id for row in cancellation.jobs], [job.job_id])
            with store.open_artifact(job.command.artifact_id) as stream:
                self.assertEqual(stream.read(), b'{"schema":"test"}')

    def _store(self) -> ProjectStore:
        temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.addCleanup(temporary.cleanup)
        return ProjectStore.create(Path(temporary.name) / "test.verdflow", "Test")


if __name__ == "__main__":
    unittest.main()
