from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
]

from verdflow.contracts import JobInfo  # noqa: E402
from verdflow.core import (  # noqa: E402
    CommandDocument,
    ElevationProviderDefinition,
    JobRuntime,
    RegionExtractionProviderDefinition,
    RegionSetProducerDefinition,
    RegionSourceProviderDefinition,
    WorkerDescriptor,
)
from verdflow.core_host import (  # noqa: E402
    CoreHost,
    CoreHostConfig,
    CoreHostExtension,
)


class _Driver:
    descriptor = WorkerDescriptor(
        worker_id="test-live-worker",
        command_types=(
            "verdflow.test.live-region",
            "verdflow.test.live-elevation",
        ),
    )

    def run_once(self) -> JobInfo | None:
        return None

    def reconcile(self) -> int:
        return 0

    def request_cancel(self, _job_id: str) -> None:
        return


class _ExtensionFactory:
    def __init__(self) -> None:
        self.runtime: JobRuntime | None = None

    def build(self, runtime: JobRuntime) -> CoreHostExtension:
        self.runtime = runtime
        return CoreHostExtension(
            worker_drivers=(_Driver(),),
            region_extraction_providers=(
                RegionExtractionProviderDefinition(
                    provider_id="test-live",
                    provider_version="1",
                    command_type="verdflow.test.live-region",
                    command_version="1",
                    max_attempts=1,
                ),
            ),
            elevation_providers=(
                ElevationProviderDefinition(
                    provider_id="test-live-elevation",
                    provider_version="1",
                    command_type="verdflow.test.live-elevation",
                    command_version="1",
                    max_attempts=1,
                ),
            ),
            region_source_providers=(
                RegionSourceProviderDefinition(
                    provider_id="test-region-source",
                    provider_version="1",
                    command_type="verdflow.test.live-region",
                    command_version="1",
                    max_attempts=1,
                ),
            ),
        )


class CoreHostExtensionTests(unittest.TestCase):
    def test_region_provider_types_share_only_their_output_contract(self) -> None:
        self.assertTrue(
            issubclass(
                RegionExtractionProviderDefinition,
                RegionSetProducerDefinition,
            )
        )
        self.assertTrue(
            issubclass(
                RegionSourceProviderDefinition,
                RegionSetProducerDefinition,
            )
        )
        self.assertFalse(
            issubclass(
                RegionExtractionProviderDefinition,
                RegionSourceProviderDefinition,
            )
        )

    def test_adds_only_declared_worker_and_provider_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            factory = _ExtensionFactory()
            with CoreHost.create(
                CoreHostConfig(
                    project_path=Path(temporary) / "live.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                ),
                project_name="DTS Live",
                extension_factories=(factory,),
            ) as host:
                descriptors = host.registry.descriptors()
                self.assertIs(factory.runtime, host.runtime)
                self.assertIn(
                    "test-live-worker",
                    {item.worker_id for item in descriptors},
                )
                self.assertIsNotNone(host.application._region_extraction)
                self.assertIsNotNone(host.application._elevation_sampling)
                self.assertIsNotNone(host.application._region_source)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "not owned by an installed CoreHost extension",
                ):
                    host.submit_extension_command(
                        CommandDocument(
                            command_type="verdflow.test.undeclared",
                            command_version="1",
                            content=b"{}",
                        ),
                        idempotency_key="undeclared",
                    )


if __name__ == "__main__":
    unittest.main()
