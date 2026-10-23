from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
]

from verdflow.contracts import SourceKind  # noqa: E402
from verdflow.core_host import CoreHost, CoreHostConfig  # noqa: E402
from verdflow.core_host import PlantRunTimeouts  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class CoreHostTests(unittest.TestCase):
    def test_strategy_deadline_is_optional_and_remains_overridable(self) -> None:
        config = CoreHostConfig(ROOT / "tests" / ".tmp" / "deadline.verdflow")
        self.assertIsNone(config.strategy_timeout_seconds)
        self.assertIsNone(PlantRunTimeouts().generation_seconds)
        PlantRunTimeouts().validate()
        config.validate()
        custom = CoreHostConfig(config.project_path, strategy_timeout_seconds=15.0)
        self.assertEqual(custom.strategy_timeout_seconds, 15.0)
        custom.validate()
        for invalid in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(timeout=invalid):
                with self.assertRaisesRegex(ValueError, "strategy_timeout_seconds"):
                    CoreHostConfig(config.project_path, strategy_timeout_seconds=invalid).validate()
                with self.assertRaisesRegex(ValueError, "generation_seconds"):
                    PlantRunTimeouts(generation_seconds=invalid).validate()

    def test_model_api_worker_is_explicit_and_owns_one_command_type(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "model-host.verdflow"
            config = CoreHostConfig(
                project_path,
                enable_blender_worker=False,
                enable_model_api_worker=True,
            )

            with CoreHost.create(config, project_name="Model Host") as host:
                descriptors = host.registry.descriptors()
                model = next(
                    descriptor
                    for descriptor in descriptors
                    if descriptor.worker_id.startswith("model_api_")
                )

                self.assertEqual(
                    model.command_types,
                    ("verdflow.model-api.analyze-materials",),
                )

    def test_host_owns_project_source_and_assembly_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            project_path = root / "host.verdflow"
            source_a = root / "a.fbx"
            source_b = root / "b.fbx"
            source_a.write_bytes(b"source-a")
            source_b.write_bytes(b"source-b")
            config = CoreHostConfig(project_path)

            with CoreHost.create(config, project_name="Host Test") as host:
                first = host.add_source_file(source_a)
                second = host.add_source_file(source_b)
                assembly = host.create_identity_assembly(
                    original_name="A+B",
                    source_ids=(first.source_id, second.source_id),
                )

                self.assertEqual(first.kind, SourceKind.FBX)
                self.assertEqual(assembly.kind, SourceKind.ASSEMBLY)
                self.assertEqual(
                    len(host.application.source_assembly_members(assembly.source_id)),
                    2,
                )

            with CoreHost.open(config) as reopened:
                self.assertEqual(reopened.application.project_info().name, "Host Test")

    def test_diagnostics_are_structured(self) -> None:
        diagnostics = CoreHost.diagnose()

        self.assertEqual(
            diagnostics.blender_available,
            diagnostics.blender_executable is not None,
        )

    def test_scene_preparation_inspection_port_delegates_to_application(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = CoreHostConfig(
                Path(temporary) / "inspection-port.verdflow",
                enable_blender_worker=False,
            )
            with CoreHost.create(config, project_name="Inspection Port") as host:
                expected = object()
                with patch.object(
                    host.application,
                    "finalize_scene_inspection",
                    return_value=expected,
                ) as finalize:
                    actual = host.finalize_scene_inspection("job_inspection")

                self.assertIs(actual, expected)
                finalize.assert_called_once_with("job_inspection")

    def test_first_service_cycle_runs_project_maintenance(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "maintenance.verdflow"
            config = CoreHostConfig(
                project_path,
                enable_blender_worker=False,
            )
            with CoreHost.create(config, project_name="Maintenance") as host:
                artifact = host.store.put_bytes(
                    b"orphan",
                    media_type="application/octet-stream",
                )
                host.store.delete_artifact(artifact.artifact_id)
                self.assertEqual(host.store.object_count(), 1)
                host.start()

                cycle = host.service_loop.run_once()

                self.assertEqual(
                    tuple(
                        execution.task_id
                        for execution in cycle.maintenance_executions
                    ),
                    ("project.retention", "project.object-gc"),
                )
                self.assertTrue(
                    all(
                        execution.succeeded
                        for execution in cycle.maintenance_executions
                    )
                )
                self.assertEqual(host.store.object_count(), 0)
                self.assertEqual(
                    host.maintenance_status(),
                    cycle.maintenance_executions,
                )

                second = host.store.put_bytes(
                    b"second-orphan",
                    media_type="application/octet-stream",
                )
                host.store.delete_artifact(second.artifact_id)
                immediate = host.service_loop.run_once()
                self.assertEqual(immediate.maintenance_executions, ())
                self.assertEqual(host.store.object_count(), 1)

                host.stop()
                host.start()
                restarted = host.service_loop.run_once()
                self.assertEqual(len(restarted.maintenance_executions), 2)
                self.assertEqual(host.store.object_count(), 0)


if __name__ == "__main__":
    unittest.main()
