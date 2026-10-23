"""Public entry points for the VerdFlow Core Host."""

from typing import Any

from .host import (
    CoreHost,
    CoreHostConfig,
    CoreHostDiagnostics,
    CoreHostExtension,
    CoreHostExtensionFactory,
    CoreHostError,
    RegionExtractionOutcome,
    RegionSourceOutcome,
    SceneInspectionOutcome,
)
from .plant_run import (
    AssetAssignmentOutcome,
    ElevationSamplingOutcome,
    GenerateAndAssignOutcome,
    GenerateAndAssignRequest,
    OutputExportOutcome,
    PlantingGenerationOutcome,
    PlantRunCoordinator,
    PlantRunOutcome,
    PlantRunRequest,
    PlantRunTimeouts,
    ResolveElevationAndExportOutcome,
    ResolveElevationAndExportRequest,
)
from .material_flow import (
    MaterialExtractionSubmission,
    MaterialFlowCoordinator,
    MaterialFlowError,
    MaterialFlowOutcome,
    MaterialFlowRequest,
)
from verdflow.core import (
    MaterialFlowExecutionRequest,
    PlantRunContinuationRequest,
    PlantRunExecutionRequest,
    ScenePreparationRequest,
)

__all__ = [
    "AssetAssignmentOutcome",
    "CoreHost",
    "CoreHostConfig",
    "CoreHostDiagnostics",
    "CoreHostExtension",
    "CoreHostExtensionFactory",
    "CoreHostError",
    "ElevationSamplingOutcome",
    "GenerateAndAssignOutcome",
    "GenerateAndAssignRequest",
    "GeospatialExtensionFactory",
    "PlantingGenerationOutcome",
    "OutputExportOutcome",
    "MaterialFlowCoordinator",
    "MaterialExtractionSubmission",
    "MaterialFlowError",
    "MaterialFlowOutcome",
    "MaterialFlowRequest",
    "MaterialFlowExecutionRequest",
    "RegionExtractionOutcome",
    "RegionSourceOutcome",
    "PlantRunCoordinator",
    "PlantRunContinuationRequest",
    "PlantRunExecutionRequest",
    "PlantRunOutcome",
    "PlantRunRequest",
    "PlantRunTimeouts",
    "ResolveElevationAndExportOutcome",
    "ResolveElevationAndExportRequest",
    "SceneInspectionOutcome",
    "ScenePreparationRequest",
]


def __getattr__(name: str) -> Any:
    if name == "GeospatialExtensionFactory":
        from .geospatial_extension import GeospatialExtensionFactory

        return GeospatialExtensionFactory
    raise AttributeError(name)
