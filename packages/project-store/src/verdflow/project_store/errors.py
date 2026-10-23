"""Structured ProjectStore failures."""


class ProjectStoreError(RuntimeError):
    """Base class for ProjectStore failures."""


class ProjectAlreadyExistsError(ProjectStoreError):
    """Raised when creating over an existing VerdFlow project."""


class ProjectNotFoundError(ProjectStoreError):
    """Raised when opening a directory without a VerdFlow database."""


class ProjectLockedError(ProjectStoreError):
    """Raised when another writer owns the project lock."""


class ProjectSchemaError(ProjectStoreError):
    """Raised when the project schema is unsupported or malformed."""


class ProjectMigrationError(ProjectSchemaError):
    """Raised when a project schema migration cannot be completed."""


class ProjectRecoveryError(ProjectStoreError):
    """Raised when automatic project recovery cannot complete safely."""


class ProjectIntegrityError(ProjectRecoveryError):
    """Raised when project metadata or referenced objects are inconsistent."""


class JobStoreError(ProjectStoreError):
    """Base class for persisted job failures."""


class JobNotFoundError(JobStoreError):
    """Raised when a job identifier is unknown."""


class JobConflictError(JobStoreError):
    """Raised when a persisted job cannot perform the requested transition."""


class JobLeaseConflictError(JobConflictError):
    """Raised when a worker lease is stale or belongs to another worker."""


class JobIdempotencyConflictError(JobConflictError):
    """Raised when an idempotency key is reused for a different command."""


class ArtifactNotFoundError(ProjectStoreError):
    """Raised when an artifact record or object is missing."""


class SceneStoreError(ProjectStoreError):
    """Base class for source and scene revision persistence failures."""


class SourceNotFoundError(SceneStoreError):
    """Raised when a source or its required artifact is missing."""


class SourceAssemblyError(SceneStoreError):
    """Raised when an immutable Source Assembly is invalid."""


class CoordinateFrameNotFoundError(SceneStoreError):
    """Raised when a coordinate frame identifier is unknown."""


class SceneRevisionNotFoundError(SceneStoreError):
    """Raised when a scene revision or manifest is missing."""


class SceneRevisionConflictError(SceneStoreError):
    """Raised when immutable scene lineage would become inconsistent."""


class RegionStoreError(ProjectStoreError):
    """Base class for region set persistence failures."""


class RegionSetNotFoundError(RegionStoreError):
    """Raised when a region set identifier is unknown."""


class RegionSetConflictError(RegionStoreError):
    """Raised when immutable region set provenance is inconsistent."""


class PointStoreError(ProjectStoreError):
    """Base class for planting run and point set persistence failures."""


class PlantingRunNotFoundError(PointStoreError):
    """Raised when a planting run identifier is unknown."""


class PointSetNotFoundError(PointStoreError):
    """Raised when a point set identifier is unknown."""


class PointSetConflictError(PointStoreError):
    """Raised when immutable point set provenance is inconsistent."""


class SnapshotStoreError(ProjectStoreError):
    """Base class for immutable runtime snapshot failures."""


class SnapshotNotFoundError(SnapshotStoreError):
    """Raised when a runtime snapshot identifier is unknown."""


class SnapshotConflictError(SnapshotStoreError):
    """Raised when immutable runtime snapshot facts are inconsistent."""


class ExportStoreError(ProjectStoreError):
    """Base class for immutable output export persistence failures."""


class ExportNotFoundError(ExportStoreError):
    """Raised when an ExportRecord does not exist."""


class ExportConflictError(ExportStoreError):
    """Raised when an ExportRecord conflicts with project evidence."""


class CompositionOutputExecutionNotFoundError(ExportStoreError):
    """Raised when a composition output execution does not exist."""


class CompositionOutputExecutionConflictError(ExportStoreError):
    """Raised when a composition output execution update is inconsistent."""


class PlantRunExecutionStoreError(ProjectStoreError):
    """Base class for persistent Plant Run execution failures."""


class PlantRunExecutionNotFoundError(PlantRunExecutionStoreError):
    """Raised when a Plant Run execution identifier is unknown."""


class PlantRunExecutionConflictError(PlantRunExecutionStoreError):
    """Raised when an execution update is stale or inconsistent."""


class PlantingCompositionStoreError(ProjectStoreError):
    """Base class for current planting composition persistence failures."""


class PlantingCompositionNotFoundError(PlantingCompositionStoreError):
    """Raised when the current composition or one of its batches is absent."""


class PlantingCompositionConflictError(PlantingCompositionStoreError):
    """Raised when a batch conflicts with current project planting facts."""


class MaterialFlowExecutionStoreError(ProjectStoreError):
    """Base class for persistent Material Flow execution failures."""


class MaterialFlowExecutionNotFoundError(MaterialFlowExecutionStoreError):
    """Raised when a Material Flow execution identifier is unknown."""


class MaterialFlowExecutionConflictError(MaterialFlowExecutionStoreError):
    """Raised when a Material Flow update is stale or inconsistent."""


class ScenePreparationStoreError(ProjectStoreError):
    """Base class for persistent Scene Preparation failures."""


class ScenePreparationNotFoundError(ScenePreparationStoreError):
    """Raised when a Scene Preparation identifier is unknown."""


class ScenePreparationConflictError(ScenePreparationStoreError):
    """Raised when a Scene Preparation update is stale or inconsistent."""


class WorkerProcessStoreError(ProjectStoreError):
    """Base class for external worker process persistence failures."""


class WorkerProcessNotFoundError(WorkerProcessStoreError):
    """Raised when a Job attempt has no recorded worker process."""


class WorkerProcessConflictError(WorkerProcessStoreError):
    """Raised when worker process ownership or lifecycle conflicts."""
