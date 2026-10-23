from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.project_store import (  # noqa: E402
    ProjectIntegrityError,
    ProjectLockedError,
    ProjectRecoveryError,
    ProjectSchemaError,
    ProjectStorageProfile,
    ProjectStore,
)
from verdflow.project_store.locking import LockRecord  # noqa: E402
from verdflow.project_store.database import ProjectDatabase  # noqa: E402
from verdflow.project_store.processes import (  # noqa: E402
    ProcessIdentity,
    ProcessProbe,
    ProcessStatus,
)
from verdflow.project_store.schema import SCHEMA_VERSION  # noqa: E402

TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class FakeProcessInspector:
    def __init__(
        self,
        current: ProcessIdentity,
        probes: dict[int, ProcessProbe],
    ) -> None:
        self.current = current
        self.probes = probes

    def current_identity(self) -> ProcessIdentity:
        return self.current

    def probe(self, host: str, pid: int) -> ProcessProbe:
        return self.probes.get(pid, ProcessProbe(ProcessStatus.UNKNOWN))


def write_lock(path: Path, record: LockRecord) -> None:
    path.write_text(
        json.dumps(record.to_dict(), sort_keys=True),
        encoding="utf-8",
    )


class ProjectStoreTests(unittest.TestCase):
    def test_database_uses_durable_sqlite_profile_by_default(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            database = ProjectDatabase(Path(directory) / "durable.db")
            database.open()
            try:
                journal_mode = database.connection.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
                synchronous = database.connection.execute(
                    "PRAGMA synchronous"
                ).fetchone()[0]

                self.assertEqual(journal_mode, "wal")
                self.assertEqual(synchronous, 2)
            finally:
                database.close()

    def test_database_uses_memory_journal_for_ephemeral_profile(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            database = ProjectDatabase(
                Path(directory) / "ephemeral.db",
                storage_profile=ProjectStorageProfile.EPHEMERAL,
            )
            database.open()
            try:
                journal_mode = database.connection.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
                synchronous = database.connection.execute(
                    "PRAGMA synchronous"
                ).fetchone()[0]
                temp_store = database.connection.execute(
                    "PRAGMA temp_store"
                ).fetchone()[0]

                self.assertEqual(journal_mode, "memory")
                self.assertEqual(synchronous, 0)
                self.assertEqual(temp_store, 2)
            finally:
                database.close()

    def test_pre_release_v9_asset_shape_is_rejected_clearly(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory) / "old-v9.verdflow"
            with ProjectStore.create(root, "Old v9"):
                pass
            connection = sqlite3.connect(root / "project.db")
            try:
                connection.execute(
                    "ALTER TABLE point_set_roles RENAME TO point_set_assets"
                )
                connection.execute(
                    "ALTER TABLE point_set_assets RENAME COLUMN role_key TO asset_key"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(
                ProjectSchemaError,
                "recreate this pre-release project",
            ):
                ProjectStore.open(root)

    def test_deduplicates_physical_objects_and_logical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "dedupe.verdflow"
            with ProjectStore.create(project_path, "Dedupe") as store:
                first = store.put_bytes(
                    b"same bytes",
                    media_type="application/octet-stream",
                )
                repeated = store.put_bytes(
                    b"same bytes",
                    media_type="application/octet-stream",
                )
                alternate_media = store.put_bytes(
                    b"same bytes",
                    media_type="application/vnd.verdflow.test",
                )

                self.assertEqual(first.artifact_id, repeated.artifact_id)
                self.assertNotEqual(first.artifact_id, alternate_media.artifact_id)
                self.assertEqual(store.object_count(), 1)
                self.assertEqual(store.artifact_count(), 2)

    def test_materializes_artifact_without_exposing_object_store_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with ProjectStore.create(
                root / "materialize.verdflow",
                "Materialize",
            ) as store:
                artifact = store.put_bytes(
                    b"private adapter input",
                    media_type="application/octet-stream",
                )
                destination = root / "runtime" / "input.fbx"

                result = store.materialize_artifact(
                    artifact.artifact_id,
                    destination,
                )

                self.assertEqual(result, destination.resolve())
                self.assertEqual(result.read_bytes(), b"private adapter input")
                self.assertFalse(any(result.parent.glob("*.tmp")))

    def test_closed_project_can_be_copied_and_reopened(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            source_path = root / "source.verdflow"
            copy_path = root / "copy.verdflow"
            with ProjectStore.create(source_path, "Portable") as store:
                artifact = store.put_bytes(
                    b"portable payload",
                    media_type="application/octet-stream",
                )
                project_id = store.project_info().project_id

            self.assertFalse((source_path / "project.lock").exists())
            self.assertFalse((source_path / "project.db-wal").exists())
            shutil.copytree(source_path, copy_path)

            with ProjectStore.open(copy_path) as reopened:
                self.assertEqual(reopened.project_info().project_id, project_id)
                with reopened.open_artifact(artifact.artifact_id) as stream:
                    self.assertEqual(stream.read(), b"portable payload")

    def test_second_writer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "locked.verdflow"
            first = ProjectStore.create(project_path, "Locked")
            try:
                with self.assertRaises(ProjectLockedError):
                    ProjectStore.open(project_path)
            finally:
                first.close()

    def test_gc_removes_unreferenced_and_staging_objects(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "gc.verdflow"
            with ProjectStore.create(project_path, "GC") as store:
                artifact = store.put_bytes(
                    b"delete me",
                    media_type="application/octet-stream",
                )
                orphan = store.objects_path / "aa" / f"{'a' * 64}.blob"
                orphan.parent.mkdir(parents=True, exist_ok=True)
                orphan.write_bytes(b"orphan")
                staging = store.staging_path / "interrupted.tmp"
                staging.write_bytes(b"partial")
                store.delete_artifact(artifact.artifact_id)

                result = store.collect_garbage()

                self.assertEqual(result.removed_object_count, 2)
                self.assertEqual(result.removed_staging_file_count, 1)
                self.assertEqual(store.object_count(), 0)

    def test_failed_metadata_registration_leaves_only_collectable_object(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "interrupted.verdflow"
            with ProjectStore.create(project_path, "Interrupted") as store:
                with patch.object(
                    store._artifacts,
                    "register",
                    side_effect=RuntimeError("injected registration failure"),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "injected registration failure",
                    ):
                        store.put_bytes(
                            b"unregistered payload",
                            media_type="application/octet-stream",
                        )

                self.assertEqual(store.artifact_count(), 0)
                self.assertEqual(store.object_count(), 0)
                self.assertEqual(
                    len(list(store.objects_path.glob("[0-9a-f][0-9a-f]/*.blob"))),
                    1,
                )
                result = store.collect_garbage()
                self.assertEqual(result.removed_object_count, 1)

    def test_newer_schema_is_refused_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "future.verdflow"
            with ProjectStore.create(project_path, "Future"):
                pass

            connection = sqlite3.connect(project_path / "project.db")
            connection.execute(
                "UPDATE schema_metadata SET value = '999' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            with self.assertRaises(ProjectSchemaError):
                ProjectStore.open(project_path)
            self.assertFalse((project_path / "project.lock").exists())

    def test_v19_internal_preview_schema_adds_planting_composition(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "pre-release.verdflow"
            with ProjectStore.create(project_path, "Pre-release"):
                pass

            connection = sqlite3.connect(project_path / "project.db")
            connection.execute("DROP TABLE composition_output_executions")
            connection.execute("DROP TABLE composition_export_inputs")
            connection.execute("DROP TABLE composition_exports")
            connection.execute("DROP TABLE planting_batch_regions")
            connection.execute("DROP TABLE planting_batches")
            connection.execute("DROP TABLE planting_compositions")
            connection.execute(
                "UPDATE schema_metadata SET value = ? WHERE key = 'schema_version'",
                ("19",),
            )
            connection.commit()
            connection.close()

            with ProjectStore.open(project_path) as store:
                self.assertEqual(store.project_info().schema_version, SCHEMA_VERSION)
                self.assertIsNone(store.current_planting_composition())
            self.assertFalse((project_path / "project.lock").exists())

    def test_v20_schema_adds_deferred_composition_output_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "composition-v20.verdflow"
            with ProjectStore.create(project_path, "Composition v20"):
                pass

            connection = sqlite3.connect(project_path / "project.db")
            connection.execute("DROP TABLE composition_output_executions")
            connection.execute(
                "UPDATE schema_metadata SET value = '20' "
                "WHERE key = 'schema_version'"
            )
            connection.execute("UPDATE projects SET schema_version = 20")
            connection.commit()
            connection.close()

            with ProjectStore.open(project_path) as store:
                self.assertEqual(
                    store.project_info().schema_version,
                    SCHEMA_VERSION,
                )
                self.assertEqual(
                    store.list_active_composition_output_executions(),
                    (),
                )
            self.assertFalse((project_path / "project.lock").exists())

    def test_failed_creation_can_be_retried_cleanly(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "retry.verdflow"
            with self.assertRaisesRegex(ValueError, "Project name is required"):
                ProjectStore.create(project_path, " ")

            self.assertFalse(project_path.exists())
            with ProjectStore.create(project_path, "Retry") as store:
                self.assertEqual(store.project_info().name, "Retry")

    def test_dead_same_host_writer_is_recovered_and_audited(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "recovery.verdflow"
            with ProjectStore.create(project_path, "Recovery"):
                pass

            old_identity = ProcessIdentity("test-host", 40101, "old-start")
            write_lock(
                project_path / "project.lock",
                LockRecord.create(old_identity, "dead-writer"),
            )
            staging = project_path / "objects" / ".staging" / "partial.tmp"
            staging.write_bytes(b"partial")
            inspector = FakeProcessInspector(
                ProcessIdentity("test-host", 40102, "new-start"),
                {40101: ProcessProbe(ProcessStatus.DEAD)},
            )

            with ProjectStore.open(
                project_path,
                process_inspector=inspector,
            ) as store:
                self.assertEqual(store.project_info().name, "Recovery")

            self.assertFalse(staging.exists())
            audits = list(
                (project_path / "logs" / "recovery").glob(
                    "recovery_*.audit.json"
                )
            )
            self.assertEqual(len(audits), 1)
            connection = sqlite3.connect(project_path / "project.db")
            count = connection.execute(
                "SELECT COUNT(*) FROM recovery_events"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(count, 1)

    def test_reused_pid_is_treated_as_stale_identity(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "pid-reuse.verdflow"
            with ProjectStore.create(project_path, "PID reuse"):
                pass

            old_identity = ProcessIdentity("test-host", 40201, "old-start")
            write_lock(
                project_path / "project.lock",
                LockRecord.create(old_identity, "old-writer"),
            )
            inspector = FakeProcessInspector(
                ProcessIdentity("test-host", 40202, "new-writer"),
                {
                    40201: ProcessProbe(
                        ProcessStatus.ALIVE,
                        ProcessIdentity(
                            "test-host",
                            40201,
                            "different-process-start",
                        ),
                    )
                },
            )

            with ProjectStore.open(
                project_path,
                process_inspector=inspector,
            ):
                pass

    def test_foreign_host_lock_is_never_recovered_automatically(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "foreign.verdflow"
            with ProjectStore.create(project_path, "Foreign"):
                pass

            write_lock(
                project_path / "project.lock",
                LockRecord.create(
                    ProcessIdentity("another-host", 40301, "remote-start"),
                    "remote-writer",
                ),
            )
            inspector = FakeProcessInspector(
                ProcessIdentity("test-host", 40302, "local-start"),
                {},
            )

            with self.assertRaisesRegex(
                ProjectRecoveryError,
                "another host",
            ):
                ProjectStore.open(
                    project_path,
                    process_inspector=inspector,
                )
            self.assertTrue((project_path / "project.lock").exists())

    def test_unknown_process_identity_is_never_recovered_automatically(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "unknown.verdflow"
            with ProjectStore.create(project_path, "Unknown"):
                pass

            write_lock(
                project_path / "project.lock",
                LockRecord.create(
                    ProcessIdentity("test-host", 40401, "unknown-start"),
                    "unknown-writer",
                ),
            )
            inspector = FakeProcessInspector(
                ProcessIdentity("test-host", 40402, "local-start"),
                {40401: ProcessProbe(ProcessStatus.UNKNOWN)},
            )

            with self.assertRaisesRegex(
                ProjectRecoveryError,
                "identity is unknown",
            ):
                ProjectStore.open(
                    project_path,
                    process_inspector=inspector,
                )
            self.assertTrue((project_path / "project.lock").exists())

    def test_missing_referenced_object_refuses_open(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "missing-object.verdflow"
            with ProjectStore.create(project_path, "Missing object") as store:
                artifact = store.put_bytes(
                    b"required",
                    media_type="application/octet-stream",
                )
            object_path = (
                project_path
                / "objects"
                / artifact.content_hash[:2]
                / f"{artifact.content_hash}.blob"
            )
            object_path.unlink()

            with self.assertRaisesRegex(
                ProjectIntegrityError,
                "Referenced object is missing",
            ):
                ProjectStore.open(project_path)
            self.assertFalse((project_path / "project.lock").exists())

    def test_recovery_rehashes_referenced_object_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "corrupt-object.verdflow"
            with ProjectStore.create(project_path, "Corrupt object") as store:
                artifact = store.put_bytes(
                    b"original",
                    media_type="application/octet-stream",
                )
            object_path = (
                project_path
                / "objects"
                / artifact.content_hash[:2]
                / f"{artifact.content_hash}.blob"
            )
            object_path.write_bytes(b"modified")
            old_identity = ProcessIdentity("test-host", 40501, "old-start")
            write_lock(
                project_path / "project.lock",
                LockRecord.create(old_identity, "dead-writer"),
            )
            inspector = FakeProcessInspector(
                ProcessIdentity("test-host", 40502, "new-start"),
                {40501: ProcessProbe(ProcessStatus.DEAD)},
            )

            with self.assertRaisesRegex(
                ProjectIntegrityError,
                "content hash is invalid",
            ):
                ProjectStore.open(
                    project_path,
                    process_inspector=inspector,
                )
            failed_audits = list(
                (project_path / "logs" / "recovery").glob(
                    "recovery_*.failed.audit.json"
                )
            )
            self.assertEqual(len(failed_audits), 1)
            self.assertTrue((project_path / "project.lock").exists())
            self.assertFalse(
                (project_path / "logs" / "recovery" / ".blocked").exists()
            )


if __name__ == "__main__":
    unittest.main()
