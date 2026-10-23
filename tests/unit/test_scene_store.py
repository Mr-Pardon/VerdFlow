from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    SourceAssemblyMemberSpec,
    SourceKind,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.project_store import (  # noqa: E402
    ProjectStore,
    SceneRevisionConflictError,
    SourceAssemblyError,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)

IDENTITY_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class SceneStoreTests(unittest.TestCase):
    def test_source_assembly_is_immutable_idempotent_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "assembly.verdflow"
            with ProjectStore.create(project_path, "Assembly") as store:
                first = self._source(store, "first.fbx", b"first")
                second = self._source(store, "second.fbx", b"second")
                translated = (
                    1.0, 0.0, 0.0, 10.0,
                    0.0, 1.0, 0.0, 20.0,
                    0.0, 0.0, 1.0, 30.0,
                    0.0, 0.0, 0.0, 1.0,
                )
                specs = (
                    SourceAssemblyMemberSpec(first.source_id, IDENTITY_MATRIX),
                    SourceAssemblyMemberSpec(second.source_id, translated),
                )

                assembly = store.create_source_assembly(
                    original_name="site.verdflow-source",
                    members=specs,
                )
                repeated = store.create_source_assembly(
                    original_name="renamed.verdflow-source",
                    members=specs,
                )
                members = store.list_source_assembly_members(
                    assembly.source_id
                )

                self.assertEqual(assembly.kind, SourceKind.ASSEMBLY)
                self.assertEqual(repeated.source_id, assembly.source_id)
                self.assertEqual([member.ordinal for member in members], [0, 1])
                self.assertEqual(members[0].source_id, first.source_id)
                self.assertEqual(members[1].source_id, second.source_id)
                self.assertEqual(
                    members[1].source_to_scene_matrix_row_major,
                    translated,
                )
                self.assertNotEqual(
                    members[0].source_member_id,
                    members[1].source_member_id,
                )
                with store.open_artifact(assembly.artifact_id) as stream:
                    manifest = stream.read().decode("utf-8")
                self.assertNotIn(first.source_id, manifest)
                self.assertIn(first.content_hash, manifest)

            with ProjectStore.open(project_path) as reopened:
                self.assertEqual(
                    reopened.list_source_assembly_members(assembly.source_id),
                    members,
                )

    def test_source_assembly_rejects_nested_and_invalid_matrices(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "invalid-assembly.verdflow"
            with ProjectStore.create(project_path, "Invalid assembly") as store:
                leaf = self._source(store, "leaf.fbx", b"leaf")
                assembly = store.create_source_assembly(
                    original_name="one",
                    members=(
                        SourceAssemblyMemberSpec(
                            leaf.source_id,
                            IDENTITY_MATRIX,
                        ),
                    ),
                )

                with self.assertRaisesRegex(
                    SourceAssemblyError,
                    "Nested",
                ):
                    store.create_source_assembly(
                        original_name="nested",
                        members=(
                            SourceAssemblyMemberSpec(
                                assembly.source_id,
                                IDENTITY_MATRIX,
                            ),
                        ),
                    )
                singular = (
                    0.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                )
                with self.assertRaisesRegex(
                    SourceAssemblyError,
                    "invertible",
                ):
                    store.create_source_assembly(
                        original_name="singular",
                        members=(
                            SourceAssemblyMemberSpec(
                                leaf.source_id,
                                singular,
                            ),
                        ),
                    )

    def test_source_frame_revision_and_active_pointer_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "scene.verdflow"
            with ProjectStore.create(project_path, "Scene") as store:
                source_artifact = store.put_bytes(
                    b"fake fbx",
                    media_type="model/vnd.autodesk.fbx",
                )
                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="sample.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                repeated = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="renamed.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                self.assertEqual(source.source_id, repeated.source_id)
                self.assertEqual(source.content_hash, source_artifact.content_hash)

                frame = store.create_coordinate_frame(
                    name="Blender world",
                    handedness="right-handed",
                    up_axis="+Z",
                    linear_unit="meter",
                    crs_kind=CoordinateReferenceKind.UNKNOWN,
                )
                manifest = store.put_bytes(
                    b'{"schema":"verdflow.scene-manifest","version":"1"}',
                    media_type="application/vnd.verdflow.scene-manifest+json",
                    schema_id="verdflow.scene-manifest",
                    schema_version="1",
                )
                revision = store.create_scene_revision(
                    source_id=source.source_id,
                    importer_id="verdflow-blender-worker",
                    importer_version="5.1.2",
                    coordinate_frame_id=frame.coordinate_frame_id,
                    scene_manifest_artifact_id=manifest.artifact_id,
                )

                self.assertEqual(
                    store.project_info().active_scene_revision_id,
                    revision.scene_revision_id,
                )
                self.assertEqual(
                    store.get_scene_revision(revision.scene_revision_id),
                    revision,
                )
                self.assertEqual(
                    store.get_coordinate_frame(frame.coordinate_frame_id),
                    frame,
                )

            with ProjectStore.open(project_path) as reopened:
                self.assertEqual(
                    reopened.project_info().active_scene_revision_id,
                    revision.scene_revision_id,
                )
                self.assertEqual(
                    reopened.get_source(source.source_id),
                    source,
                )

    def test_parent_revision_must_belong_to_same_source(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "lineage.verdflow"
            with ProjectStore.create(project_path, "Lineage") as store:
                first_artifact = store.put_bytes(
                    b"first",
                    media_type="model/vnd.autodesk.fbx",
                )
                second_artifact = store.put_bytes(
                    b"second",
                    media_type="model/vnd.autodesk.fbx",
                )
                first = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="first.fbx",
                    artifact_id=first_artifact.artifact_id,
                )
                second = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="second.fbx",
                    artifact_id=second_artifact.artifact_id,
                )
                frame = store.create_coordinate_frame(
                    name="Canonical",
                    handedness="right-handed",
                    up_axis="+Z",
                    linear_unit="meter",
                    crs_kind=CoordinateReferenceKind.LOCAL,
                )
                manifest = store.put_bytes(
                    b"{}",
                    media_type="application/vnd.verdflow.scene-manifest+json",
                )
                parent = store.create_scene_revision(
                    source_id=first.source_id,
                    importer_id="test",
                    importer_version="1",
                    coordinate_frame_id=frame.coordinate_frame_id,
                    scene_manifest_artifact_id=manifest.artifact_id,
                )

                with self.assertRaises(SceneRevisionConflictError):
                    store.create_scene_revision(
                        source_id=second.source_id,
                        parent_revision_id=parent.scene_revision_id,
                        importer_id="test",
                        importer_version="2",
                        coordinate_frame_id=frame.coordinate_frame_id,
                        scene_manifest_artifact_id=manifest.artifact_id,
                    )

    def test_worker_process_is_bound_to_an_existing_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "worker.verdflow"
            with ProjectStore.create(project_path, "Worker") as store:
                command = store.put_bytes(
                    b"{}",
                    media_type="application/vnd.verdflow.command+json",
                )
                from verdflow.contracts import JobCommandRef, JobSubmission

                job = store.create_job(
                    JobSubmission(
                        command=JobCommandRef(
                            command_type="test.worker",
                            command_version="1",
                            artifact_id=command.artifact_id,
                        ),
                        idempotency_key="worker-process",
                    ),
                    "2026-07-30T00:00:00+00:00",
                )
                claimed = store.claim_next_job(
                    worker_id="worker",
                    now="2026-07-30T00:00:01+00:00",
                    lease_expires_at="2026-07-30T00:01:01+00:00",
                )
                assert claimed is not None
                process = WorkerProcessInfo(
                    job_id=job.job_id,
                    attempt_number=claimed.lease.attempt_count,
                    adapter_id="test-adapter",
                    host="test-host",
                    pid=100,
                    started_at_token="start-token",
                    status=WorkerProcessStatus.RUNNING,
                    started_at=datetime.now(UTC).isoformat(),
                    heartbeat_at=None,
                    finished_at=None,
                    exit_code=None,
                )
                store.register_worker_process(process)
                store.heartbeat_worker_process(
                    process.job_id,
                    process.attempt_number,
                    "2026-07-30T00:00:02+00:00",
                )
                finished = store.finish_worker_process(
                    process.job_id,
                    process.attempt_number,
                    status=WorkerProcessStatus.EXITED,
                    finished_at="2026-07-30T00:00:03+00:00",
                    exit_code=0,
                )

                self.assertEqual(finished.status, WorkerProcessStatus.EXITED)
                self.assertEqual(finished.exit_code, 0)
                self.assertEqual(store.list_active_worker_processes(), ())

    @staticmethod
    def _source(
        store: ProjectStore,
        name: str,
        data: bytes,
    ):
        artifact = store.put_bytes(
            data,
            media_type="model/vnd.autodesk.fbx",
        )
        return store.register_source(
            kind=SourceKind.FBX,
            original_name=name,
            artifact_id=artifact.artifact_id,
        )


if __name__ == "__main__":
    unittest.main()
