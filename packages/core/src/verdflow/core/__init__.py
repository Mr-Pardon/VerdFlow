"""VerdFlow application Core public API."""

from typing import TYPE_CHECKING, Any

from .assignment_preview import AssignmentPreviewService

from .application import (
    ApplicationFacade,
    CommandDocument,
    JobCancellationPort,
    SourceRegistration,
)
from .clock import Clock, SystemClock
from .strategy_preview import StrategyPreviewPort, StrategyPreviewService
from .job_runtime import JobRuntime
from .maintenance import (
    MaintenanceDescriptor,
    MaintenanceExecution,
    MaintenanceScheduler,
    MaintenanceTask,
    MaintenanceTaskResult,
)
from .project_maintenance import (
    ObjectGarbageCollectionTask,
    RetentionMaintenanceTask,
)
from .retention import JobRetentionPolicy
from .retention_sweeper import JobRetentionSweeper
from .library import (
    AssetCatalogLibraryPort,
    LibraryStrategyInfo,
    RoleBindingProfileLibraryPort,
    StrategyTrustDecision,
    StrategyTrustPort,
    StrategyTrustState,
    StrategyDocumentPort,
    UserLibraryPort,
)
from .point_import import PointSetImportError, PointSetImportService
from .material_selection import (
    load_material_facts,
    load_material_selection,
    load_region_material_selection,
)
from verdflow.contracts import (
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    PLANTING_GENERATE_POINTS_COMMAND,
)
from .region_extraction_import import (
    RegionExtractionImportError,
    RegionExtractionImportService,
)
from .region_import import RegionSetImportError, RegionSetImportService
from .region_providers import RegionSetProducerDefinition
from .region_source_import import (
    RegionSourceImportError,
    RegionSourceImportService,
)
from .scene_import import SceneImportError, SceneImportService
from .service_loop import (
    ServiceCycleResult,
    ServiceCycleTask,
    ServiceLoop,
    ServiceLoopError,
    ServiceLoopState,
    ServiceStartupReport,
)
from .strategy_access import (
    StrategyAccessError,
    StrategyExecutionAuthorization,
    StrategyExecutionAuthorizer,
)
from .workers import (
    WorkerDescriptor,
    WorkerDriver,
    WorkerReconciliation,
    WorkerRegistry,
)
if TYPE_CHECKING:
    from .asset_assignment import (
        AssetAssignmentError,
        AssetAssignmentRequest,
        AssetAssignmentService,
    )
    from .assignment_worker import AssetAssignmentWorker
    from .composition_output_execution import (
        CompositionOutputBatchRequest,
        CompositionOutputExecutionRequest,
        CompositionOutputExecutionService,
        CompositionOutputStages,
    )
    from .elevation_sampling import (
        ElevationProviderDefinition,
        ElevationSamplingRequest,
        ElevationSamplingService,
    )
    from .exporting import (
        CompositionOutputExportInput,
        CompositionOutputExportRequest,
        ExportProviderDefinition,
        OutputExportError,
        OutputExportRequest,
        OutputExportService,
    )
    from .planting_generation import (
        PlantingGenerationRequest,
        PlantingGenerationService,
    )
    from .plant_run_execution import (
        load_plant_run_execution_request,
        PlantRunContinuationRequest,
        PlantRunExecutionRequest,
        PlantRunExecutionService,
        PlantRunExecutionStages,
    )
    from .material_analysis import (
        MaterialAnalysisError,
        MaterialConfirmationRequired,
        MaterialConfirmationResult,
        MaterialAnalysisRequest,
        MaterialAnalysisService,
        ModelAnalysisProviderDefinition,
    )
    from .material_flow_execution import (
        MaterialFlowExecutionRequest,
        MaterialFlowExecutionService,
        MaterialFlowExecutionStages,
    )
    from .project_snapshots import (
        BindingSnapshotResult,
        ProjectSnapshotFinalizationError,
        ProjectSnapshotService,
    )
    from .region_extraction import (
        RegionExtractionProviderDefinition,
        RegionExtractionRequest,
        RegionExtractionService,
    )
    from .region_source import (
        RegionSourceProviderDefinition,
        RegionSourceRequest,
        RegionSourceService,
    )
    from .scene_preparation_execution import (
        ScenePreparationExecutionService,
        ScenePreparationRequest,
        ScenePreparationStages,
    )

__all__ = [
    "AssignmentPreviewService",
    "StrategyPreviewPort",
    "StrategyPreviewService",
    "ApplicationFacade",
    "AssetAssignmentError",
    "AssetAssignmentRequest",
    "AssetAssignmentService",
    "AssetAssignmentWorker",
    "AssetCatalogLibraryPort",
    "Clock",
    "CommandDocument",
    "ELEVATION_SAMPLING_COMMAND_SCHEMA",
    "ELEVATION_SAMPLING_COMMAND_VERSION",
    "ElevationProviderDefinition",
    "ElevationSamplingRequest",
    "ElevationSamplingService",
    "ExportProviderDefinition",
    "JobCancellationPort",
    "JobRuntime",
    "JobRetentionPolicy",
    "JobRetentionSweeper",
    "MaintenanceDescriptor",
    "MaintenanceExecution",
    "MaintenanceScheduler",
    "MaintenanceTask",
    "MaintenanceTaskResult",
    "MaterialAnalysisError",
    "MaterialConfirmationRequired",
    "MaterialConfirmationResult",
    "MaterialAnalysisRequest",
    "MaterialAnalysisService",
    "ModelAnalysisProviderDefinition",
    "MaterialFlowExecutionRequest",
    "MaterialFlowExecutionService",
    "MaterialFlowExecutionStages",
    "load_material_facts",
    "load_material_selection",
    "load_region_material_selection",
    "LibraryStrategyInfo",
    "PointSetImportError",
    "PointSetImportService",
    "PLANTING_GENERATE_POINTS_COMMAND",
    "PlantingGenerationRequest",
    "PlantingGenerationService",
    "PlantRunExecutionRequest",
    "PlantRunContinuationRequest",
    "PlantRunExecutionService",
    "PlantRunExecutionStages",
    "load_plant_run_execution_request",
    "ObjectGarbageCollectionTask",
    "CompositionOutputExportInput",
    "CompositionOutputExportRequest",
    "CompositionOutputBatchRequest",
    "CompositionOutputExecutionRequest",
    "CompositionOutputExecutionService",
    "CompositionOutputStages",
    "OutputExportError",
    "OutputExportRequest",
    "OutputExportService",
    "BindingSnapshotResult",
    "ProjectSnapshotFinalizationError",
    "ProjectSnapshotService",
    "RegionExtractionImportError",
    "RegionExtractionImportService",
    "RegionExtractionProviderDefinition",
    "RegionExtractionRequest",
    "RegionExtractionService",
    "RegionSourceImportError",
    "RegionSourceImportService",
    "RegionSourceProviderDefinition",
    "RegionSourceRequest",
    "RegionSourceService",
    "RegionSetImportError",
    "RegionSetImportService",
    "RegionSetProducerDefinition",
    "RetentionMaintenanceTask",
    "RoleBindingProfileLibraryPort",
    "SceneImportError",
    "SceneImportService",
    "ScenePreparationExecutionService",
    "ScenePreparationRequest",
    "ScenePreparationStages",
    "ServiceCycleResult",
    "ServiceCycleTask",
    "ServiceLoop",
    "ServiceLoopError",
    "ServiceLoopState",
    "ServiceStartupReport",
    "SourceRegistration",
    "StrategyAccessError",
    "StrategyDocumentPort",
    "StrategyExecutionAuthorization",
    "StrategyExecutionAuthorizer",
    "StrategyTrustDecision",
    "StrategyTrustPort",
    "StrategyTrustState",
    "SystemClock",
    "UserLibraryPort",
    "WorkerDescriptor",
    "WorkerDriver",
    "WorkerReconciliation",
    "WorkerRegistry",
]


def __getattr__(name: str) -> Any:
    if name in {
        "AssetAssignmentError",
        "AssetAssignmentRequest",
        "AssetAssignmentService",
    }:
        from . import asset_assignment

        return getattr(asset_assignment, name)
    if name == "AssetAssignmentWorker":
        from .assignment_worker import AssetAssignmentWorker

        return AssetAssignmentWorker
    if name in {
        "ElevationProviderDefinition",
        "ElevationSamplingRequest",
        "ElevationSamplingService",
    }:
        from . import elevation_sampling

        return getattr(elevation_sampling, name)
    if name in {
        "CompositionOutputBatchRequest",
        "CompositionOutputExecutionRequest",
        "CompositionOutputExecutionService",
        "CompositionOutputStages",
    }:
        from . import composition_output_execution

        return getattr(composition_output_execution, name)
    if name in {
        "CompositionOutputExportInput",
        "CompositionOutputExportRequest",
        "ExportProviderDefinition",
        "OutputExportError",
        "OutputExportRequest",
        "OutputExportService",
    }:
        from . import exporting

        return getattr(exporting, name)
    if name in {
        "MaterialAnalysisError",
        "MaterialConfirmationRequired",
        "MaterialConfirmationResult",
        "MaterialAnalysisRequest",
        "MaterialAnalysisService",
        "ModelAnalysisProviderDefinition",
    }:
        from . import material_analysis

        return getattr(material_analysis, name)
    if name in {
        "MaterialFlowExecutionRequest",
        "MaterialFlowExecutionService",
        "MaterialFlowExecutionStages",
    }:
        from . import material_flow_execution

        return getattr(material_flow_execution, name)
    if name in {
        "PlantingGenerationRequest",
        "PlantingGenerationService",
    }:
        from . import planting_generation

        return getattr(planting_generation, name)
    if name in {
        "load_plant_run_execution_request",
        "PlantRunContinuationRequest",
        "PlantRunExecutionRequest",
        "PlantRunExecutionService",
        "PlantRunExecutionStages",
    }:
        from . import plant_run_execution

        return getattr(plant_run_execution, name)
    if name in {
        "RegionExtractionProviderDefinition",
        "RegionExtractionRequest",
        "RegionExtractionService",
    }:
        from . import region_extraction

        return getattr(region_extraction, name)
    if name in {
        "RegionSourceProviderDefinition",
        "RegionSourceRequest",
        "RegionSourceService",
    }:
        from . import region_source

        return getattr(region_source, name)
    if name in {
        "ScenePreparationExecutionService",
        "ScenePreparationRequest",
        "ScenePreparationStages",
    }:
        from . import scene_preparation_execution

        return getattr(scene_preparation_execution, name)
    if name in {
        "BindingSnapshotResult",
        "ProjectSnapshotFinalizationError",
        "ProjectSnapshotService",
    }:
        from . import project_snapshots

        return getattr(project_snapshots, name)
    raise AttributeError(name)
