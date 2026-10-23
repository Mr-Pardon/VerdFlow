from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
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

from verdflow.system_host import (  # noqa: E402
    ProjectAvailability,
    ProjectWorkspace,
    SystemHost,
    SystemHostConfig,
)
from verdflow.project_store import ProjectNotFoundError  # noqa: E402


class SystemProjectRegistryTests(unittest.TestCase):
    def test_recent_projects_survive_restart_and_deduplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SystemHostConfig(
                library_path=root / "library",
                system_path=root / "system",
            )
            project_path = root / "project"
            with SystemHost(config) as system:
                created = system.create_project(
                    project_path,
                    project_name="Registry",
                    enable_blender_worker=False,
                    workspace=ProjectWorkspace.DTS,
                )
                system.open_project(
                    project_path,
                    enable_blender_worker=False,
                    workspace=ProjectWorkspace.BLENDER,
                )
                recent = system.list_recent_projects()
                self.assertEqual(len(recent), 1)
                self.assertEqual(recent[0].project_id, created.project_id)
                self.assertEqual(recent[0].last_workspace, "blender")
                self.assertEqual(recent[0].active_session_id, created.session_id)

            with SystemHost(config) as reopened:
                recent = reopened.list_recent_projects()
                self.assertEqual(len(recent), 1)
                self.assertEqual(
                    recent[0].availability,
                    ProjectAvailability.AVAILABLE.value,
                )
                self.assertIsNone(recent[0].active_session_id)
                opened = reopened.open_project(
                    project_path,
                    enable_blender_worker=False,
                    workspace=ProjectWorkspace.BLENDER,
                )
                recent = reopened.list_recent_projects()
                self.assertEqual(len(recent), 1)
                self.assertEqual(recent[0].active_session_id, opened.session_id)
                self.assertEqual(recent[0].last_workspace, "blender")

    def test_missing_recent_project_is_reported_without_disk_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SystemHostConfig(
                library_path=root / "library",
                system_path=root / "system",
            )
            project_path = root / "project"
            with SystemHost(config) as system:
                system.create_project(
                    project_path,
                    project_name="Missing",
                    enable_blender_worker=False,
                )
            (project_path / "project.db").unlink()
            with SystemHost(config) as system:
                recent = system.list_recent_projects()
                self.assertEqual(recent[0].availability, "missing")

    def test_registry_write_failure_rolls_back_started_project_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                )
            ) as system:
                with patch.object(
                    system._project_registry,
                    "record_opened",
                    side_effect=OSError("registry unavailable"),
                ):
                    with self.assertRaises(OSError):
                        system.create_project(
                            root / "project",
                            project_name="Rollback",
                            enable_blender_worker=False,
                        )
                self.assertEqual(system.status().active_project_count, 0)
                self.assertEqual(system.list_projects(), ())

    def test_delete_project_closes_runtime_removes_files_and_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                )
            ) as system:
                system.create_project(
                    project_path,
                    project_name="Delete me",
                    enable_blender_worker=False,
                )
                recent = system.list_recent_projects()[0]
                removal = system.delete_project(recent.registry_id)

                self.assertTrue(removal.removed)
                self.assertTrue(removal.files_removed)
                self.assertFalse(project_path.exists())
                self.assertEqual(system.status().active_project_count, 0)
                self.assertEqual(system.list_recent_projects(), ())

    def test_delete_missing_project_only_removes_registry_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SystemHostConfig(
                library_path=root / "library",
                system_path=root / "system",
            )
            project_path = root / "project"
            with SystemHost(config) as system:
                system.create_project(
                    project_path,
                    project_name="Missing",
                    enable_blender_worker=False,
                )
                registry_id = system.list_recent_projects()[0].registry_id
            shutil.rmtree(project_path)
            with SystemHost(config) as system:
                removal = system.delete_project(registry_id)
                self.assertTrue(removal.removed)
                self.assertFalse(removal.files_removed)
                self.assertEqual(system.list_recent_projects(), ())

    def test_delete_failed_open_only_removes_registry_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid_path = root / "selected-parent"
            invalid_path.mkdir()
            marker = invalid_path / "keep.txt"
            marker.write_text("not owned by VerdFlow", encoding="utf-8")
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                )
            ) as system:
                with self.assertRaises(ProjectNotFoundError):
                    system.open_project(
                        invalid_path,
                        enable_blender_worker=False,
                    )
                recent = system.list_recent_projects()[0]
                self.assertEqual(recent.availability, "missing")

                removal = system.delete_project(recent.registry_id)

                self.assertTrue(removal.removed)
                self.assertFalse(removal.files_removed)
                self.assertTrue(marker.is_file())
                self.assertEqual(system.list_recent_projects(), ())


if __name__ == "__main__":
    unittest.main()
