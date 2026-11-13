"""Built-in composition factory for the optional geospatial Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from verdflow.contracts import CapabilityRef, InputRecipe
from pathlib import Path
import sys

from verdflow.core import (
    ElevationProviderDefinition,
    JobRuntime,
    RegionSourceImportService,
    RegionSourceProviderDefinition,
)
from verdflow.geospatial_worker import (
    GEOSPATIAL_ADAPTER_ID,
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    IMAGERY_PREPARE_REGIONS_COMMAND,
    IMAGERY_REGION_PROVIDER_ID,
    IMAGERY_REGION_RECIPE_ID,
    GeospatialWorkerConfig,
    GeospatialWorkerSupervisor,
)
from verdflow.geospatial_worker.imagery_confirmation import refine_selection

from .host import CoreHostExtension


@dataclass(frozen=True, slots=True)
class GeospatialExtensionFactory:
    """Register geospatial capabilities only for Hosts that request them."""

    runtime_root: Path
    cache_root: Path
    timeout_seconds: float = 300.0
    python_executable: Path = Path(sys.executable)
    imagery_model_path: Path | None = None
    imagery_python_executable: Path | None = None
    enable_imagery: bool = False

    def build(self, runtime: JobRuntime) -> CoreHostExtension:
        supervisor = GeospatialWorkerSupervisor(
            runtime,
            GeospatialWorkerConfig(
                runtime_root=self.runtime_root,
                cache_root=self.cache_root,
                python_executable=self.python_executable,
                timeout_seconds=self.timeout_seconds,
                imagery_model_path=self.imagery_model_path,
                imagery_python_executable=self.imagery_python_executable,
                enable_imagery=self.enable_imagery,
            ),
            publish_region_source=partial(RegionSourceImportService(runtime.store).complete_and_publish, runtime),
        )
        vector_provider = RegionSourceProviderDefinition(
            provider_id=GEOSPATIAL_ADAPTER_ID, provider_version="1",
            command_type=GEOSPATIAL_PREPARE_REGIONS_COMMAND, command_version="1", max_attempts=2,
            supports_auto_publish=True,
        )
        vector_recipe = InputRecipe(
            recipe_id="verdflow.vector-regions", version="1", adapter_id=GEOSPATIAL_ADAPTER_ID,
            preparation=CapabilityRef("region_source", GEOSPATIAL_ADAPTER_ID, "1", GEOSPATIAL_PREPARE_REGIONS_COMMAND, "1"),
            sampling=CapabilityRef("elevation", GEOSPATIAL_ADAPTER_ID, "1", GEOSPATIAL_SAMPLE_ELEVATION_COMMAND, "1"),
            geometry_kinds=("polygon",), sampling_source_kind="source",
            configuration_schema="verdflow.geotiff-elevation.config/1",
            failure_policy="fail-outside-or-nodata", source_member_kind="geotiff",
            legacy_importer_ids=(GEOSPATIAL_ADAPTER_ID,),
        )
        providers, recipes = (vector_provider,), (vector_recipe,)
        if self.enable_imagery or self.imagery_model_path is not None:
            providers += (replace(vector_provider, provider_id=IMAGERY_REGION_PROVIDER_ID,
                                  command_type=IMAGERY_PREPARE_REGIONS_COMMAND, supports_auto_publish=False),)
            recipes += (replace(
                vector_recipe, recipe_id=IMAGERY_REGION_RECIPE_ID, legacy_importer_ids=(),
                preparation=CapabilityRef("region_source", IMAGERY_REGION_PROVIDER_ID, "1",
                                          IMAGERY_PREPARE_REGIONS_COMMAND, "1"),
            ),)
        return CoreHostExtension(
            worker_drivers=(supervisor,),
            region_source_providers=providers,
            elevation_providers=(
                ElevationProviderDefinition(
                    provider_id=GEOSPATIAL_ADAPTER_ID,
                    provider_version="1",
                    command_type=GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
                    command_version="1",
                    max_attempts=2,
                    sampling_source_kind="source",
                    configuration_schema="verdflow.geotiff-elevation.config/1",
                    failure_policy="fail-outside-or-nodata",
                ),
            ),
            input_recipes=recipes,
            region_selection_refiner=partial(
                refine_selection, runtime.store, self.imagery_python_executable or self.python_executable,
                timeout_seconds=self.timeout_seconds,
            )
            if self.enable_imagery or self.imagery_model_path is not None else None,
        )
