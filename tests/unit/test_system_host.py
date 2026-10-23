from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


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
    str(ROOT / "adapters" / "geospatial-worker" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
]

from verdflow.system_host import (  # noqa: E402
    ProjectSessionNotFoundError,
    ProjectSessionState,
    SystemHost,
    SystemHostConfig,
    SystemHostState,
)
from verdflow.contracts import SourceKind  # noqa: E402


class SystemHostTests(unittest.TestCase):
    def test_registers_geospatial_input_without_a_parallel_core_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector_path = root / "regions.geojson"
            vector_path.write_text("{}", encoding="utf-8")
            elevation_path = root / "elevation.tif"
            elevation_path.write_bytes(b"tiff")
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                project = system.create_project(
                    root / "geospatial.verdflow",
                    project_name="Geospatial",
                    enable_blender_worker=False,
                    enable_geospatial_worker=True,
                )
                vector = system.add_source_file(
                    project.session_id,
                    vector_path,
                    kind=SourceKind.GEOJSON,
                )
                elevation = system.add_source_file(
                    project.session_id,
                    elevation_path,
                    kind=SourceKind.GEOTIFF,
                )
                assembly = system.create_identity_source_assembly(
                    project.session_id,
                    original_name="regions + elevation",
                    source_ids=(vector.source_id, elevation.source_id),
                )

                self.assertIs(assembly.kind, SourceKind.ASSEMBLY)
                worker_runtime = (
                    root
                    / "geospatial.verdflow"
                    / "runtime"
                    / "geospatial-worker"
                )
                self.assertTrue(worker_runtime.is_dir())

    def test_shapefile_source_is_packaged_at_the_system_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shape = root / "areas.shp"
            shape.write_bytes(b"shape")
            shape.with_suffix(".shx").write_bytes(b"index")
            shape.with_suffix(".dbf").write_bytes(b"table")
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                project = system.create_project(
                    root / "shapefile.verdflow",
                    project_name="Shapefile",
                    enable_blender_worker=False,
                    enable_geospatial_worker=True,
                )
                source = system.add_source_file(
                    project.session_id,
                    shape,
                    kind=SourceKind.SHAPEFILE,
                )
                artifact = system.get_artifact(
                    project.session_id,
                    source.artifact_id,
                )

                self.assertEqual(source.original_name, "areas.shapefile.zip")
                self.assertEqual(
                    artifact.media_type,
                    "application/vnd.verdflow.shapefile+zip",
                )

    def test_owns_two_projects_and_reuses_an_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                first = system.create_project(
                    root / "project-a",
                    project_name="Project A",
                    enable_blender_worker=False,
                )
                reused = system.open_project(
                    root / "project-a",
                    enable_blender_worker=False,
                )
                second = system.create_project(
                    root / "project-b",
                    project_name="Project B",
                    enable_blender_worker=False,
                )

                self.assertEqual(first.session_id, reused.session_id)
                self.assertNotEqual(first.project_id, second.project_id)
                self.assertEqual(system.status().active_project_count, 2)
                self.assertEqual(len(system.list_projects()), 2)

                closed = system.close_project(first.session_id)
                self.assertEqual(closed.state, ProjectSessionState.CLOSED)
                self.assertEqual(system.status().active_project_count, 1)

                reopened = system.open_project(
                    root / "project-a",
                    enable_blender_worker=False,
                )
                self.assertEqual(reopened.project_id, first.project_id)
                self.assertNotEqual(reopened.session_id, first.session_id)

            self.assertEqual(system.state, SystemHostState.STOPPED)

    def test_unknown_project_session_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(
                SystemHostConfig(library_path=Path(temporary) / "library")
            ) as system:
                with self.assertRaises(ProjectSessionNotFoundError):
                    system.close_project("missing")

    def test_open_project_replaces_a_failed_cached_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                first = system.create_project(
                    root / "project-a",
                    project_name="Project A",
                    enable_blender_worker=False,
                )
                failed_runtime = system._projects[first.session_id]
                failed_runtime._set_state(
                    ProjectSessionState.FAILED,
                    error="fixture failure",
                )

                reopened = system.open_project(
                    root / "project-a",
                    enable_blender_worker=False,
                )

                self.assertEqual(reopened.state, ProjectSessionState.ACTIVE)
                self.assertEqual(reopened.project_id, first.project_id)
                self.assertNotEqual(reopened.session_id, first.session_id)
                self.assertNotIn(first.session_id, system._projects)


if __name__ == "__main__":
    unittest.main()
