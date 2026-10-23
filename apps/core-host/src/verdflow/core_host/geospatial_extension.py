"""Built-in composition factory for the optional geospatial Worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from verdflow.core import (
    ElevationProviderDefinition,
    JobRuntime,
    RegionSourceProviderDefinition,
)
from verdflow.geospatial_worker import (
    GEOSPATIAL_ADAPTER_ID,
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    GeospatialWorkerConfig,
    GeospatialWorkerSupervisor,
)

from .host import CoreHostExtension


@dataclass(frozen=True, slots=True)
class GeospatialExtensionFactory:
    """Register geospatial capabilities only for Hosts that request them."""

    runtime_root: Path
    cache_root: Path
    timeout_seconds: float = 300.0
    python_executable: Path = Path(sys.executable)

    def build(self, runtime: JobRuntime) -> CoreHostExtension:
        supervisor = GeospatialWorkerSupervisor(
            runtime,
            GeospatialWorkerConfig(
                runtime_root=self.runtime_root,
                cache_root=self.cache_root,
                python_executable=self.python_executable,
                timeout_seconds=self.timeout_seconds,
            ),
        )
        return CoreHostExtension(
            worker_drivers=(supervisor,),
            region_source_providers=(
                RegionSourceProviderDefinition(
                    provider_id=GEOSPATIAL_ADAPTER_ID,
                    provider_version="1",
                    command_type=GEOSPATIAL_PREPARE_REGIONS_COMMAND,
                    command_version="1",
                    max_attempts=1,
                ),
            ),
            elevation_providers=(
                ElevationProviderDefinition(
                    provider_id=GEOSPATIAL_ADAPTER_ID,
                    provider_version="1",
                    command_type=GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
                    command_version="1",
                    max_attempts=1,
                ),
            ),
        )
