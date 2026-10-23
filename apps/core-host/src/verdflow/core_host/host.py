"""One-project VerdFlow Core Host composition root."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from verdflow.blender_worker import (
    BLENDER_EXTRACT_REGIONS_COMMAND,
    BLENDER_INSPECT_SCENE_COMMAND,
    BLENDER_SAMPLE_ELEVATION_COMMAND,
    BLENDER_WORKER_ADAPTER_ID,
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
    locate_blender,
)
from verdflow.contracts import (
    JobInfo,
    JobStatus,
    PlantRunExecutionInfo,
    RegionSetInfo,
    SceneRevisionInfo,
    ScenePreparationInfo,
    MaterialFactsView,
    MaterialSelectionRequest,
    SourceAssemblyMemberSpec,
    SourceInfo,
    SourceKind,
)
from verdflow.dts_adapter import (
    DTS_ADAPTER_ID,
    DTS_ADAPTER_VERSION,
    DTS_XML_EXPORT_COMMAND,
    DtsXmlExportWorker,
)
from verdflow.core import (
    ApplicationFacade,
    AssetAssignmentService,
    AssetAssignmentWorker,
    CommandDocument,
    CompositionOutputExecutionRequest,
    CompositionOutputExecutionService,
    JobRuntime,
    JobRetentionSweeper,
    MaintenanceExecution,
    MaintenanceScheduler,
    MaterialFlowExecutionRequest,
    MaterialFlowExecutionService,
    ObjectGarbageCollectionTask,
    PlantRunContinuationRequest,
    PlantRunExecutionRequest,
    PlantRunExecutionService,
    RetentionMaintenanceTask,
    ScenePreparationExecutionService,
    ScenePreparationRequest,
    ServiceLoop,
    ServiceLoopState,
    SourceRegistration,
    StrategyExecutionAuthorizer,
    AssetCatalogLibraryPort,
    RoleBindingProfileLibraryPort,
    StrategyTrustPort,
    UserLibraryPort,
    WorkerRegistry,
    WorkerDriver,
    ElevationProviderDefinition,
    RegionExtractionProviderDefinition,
    RegionSourceProviderDefinition,
)
from verdflow.project_store import ProjectStorageProfile, ProjectStore
from verdflow.strategy_worker import (
    ProjectStrategyDocumentProvider,
    StrategyPackageResolver,
    StrategyWorkerConfig,
    StrategyWorkerSupervisor,
    StrategyWorkerSupervisorConfig,
)

from .blender_driver import BlenderWorkerDriver
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
    MaterialFlowOutcome,
    MaterialFlowRequest,
)
from .strategy_driver import StrategyWorkerDriver

if TYPE_CHECKING:
    from verdflow.model_api import ModelApiProviderRegistry, SecretResolver
    from verdflow.core import (
        AssetAssignmentRequest,
        CompositionOutputExportRequest,
        ElevationSamplingRequest,
        PlantingGenerationRequest,
        OutputExportRequest,
        RegionExtractionRequest,
        RegionSourceRequest,
    )
    from verdflow.contracts import (
        CompositionExportRecordInfo,
        ExportRecordInfo,
        MaterialClassificationCandidate,
    )


IDENTITY_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class CoreHostError(RuntimeError):
    """Raised when a Host use case cannot complete safely."""


class StrategyRuntimeLibrary(
    AssetCatalogLibraryPort,
    RoleBindingProfileLibraryPort,
    UserLibraryPort,
    StrategyTrustPort,
    StrategyPackageResolver,
    Protocol,
):
    """Read-only capabilities supplied by the user-library composition root."""


@dataclass(frozen=True, slots=True)
class CoreHostExtension:
    """Additive Adapter capabilities accepted by the composition root."""

    worker_drivers: tuple[WorkerDriver, ...] = ()
    region_extraction_providers: tuple[
        RegionExtractionProviderDefinition,
        ...,
    ] = ()
    elevation_providers: tuple[ElevationProviderDefinition, ...] = ()
    region_source_providers: tuple[RegionSourceProviderDefinition, ...] = ()


class CoreHostExtensionFactory(Protocol):
    """Build Adapter capabilities against the Host-owned JobRuntime."""

    def build(self, runtime: JobRuntime) -> CoreHostExtension: ...


@dataclass(frozen=True, slots=True)
class CoreHostConfig:
    project_path: Path
    project_storage_profile: ProjectStorageProfile = field(
        default=ProjectStorageProfile.DURABLE,
        kw_only=True,
    )
    enable_blender_worker: bool = True
    blender_executable: Path | None = None
    runtime_root: Path | None = None
    lease_seconds: float = 60.0
    poll_interval_seconds: float = 0.1
    blender_ready_timeout_seconds: float = 90.0
    blender_heartbeat_timeout_seconds: float = 30.0
    blender_blocking_timeout_seconds: float = 900.0
    blender_execution_timeout_seconds: float = 1800.0
    strategy_timeout_seconds: float | None = None
    strategy_heartbeat_interval_seconds: float = 5.0
    enable_model_api_worker: bool = False
    model_api_ready_timeout_seconds: float = 10.0
    model_api_heartbeat_timeout_seconds: float = 15.0
    model_api_execution_timeout_seconds: float = 300.0
    model_api_request_timeout_seconds: float = 240.0
    model_api_heartbeat_interval_seconds: float = 2.0
    enable_project_maintenance: bool = True
    retention_interval_seconds: float = 300.0
    retention_batch_size: int = 100
    retention_orphan_grace_seconds: float = 86400.0
    object_gc_interval_seconds: float = 86400.0
    maintenance_retry_seconds: float = 60.0
    maintenance_max_retry_seconds: float = 1800.0
    maintenance_continuation_seconds: float = 1.0

    def validate(self) -> None:
        if not isinstance(self.project_storage_profile, ProjectStorageProfile):
            raise TypeError(
                "project_storage_profile must be a ProjectStorageProfile"
            )
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.strategy_timeout_seconds is not None and (
            not math.isfinite(self.strategy_timeout_seconds)
            or self.strategy_timeout_seconds <= 0
        ):
            raise ValueError(
                "strategy_timeout_seconds must be finite and positive, or None"
            )
        if self.strategy_heartbeat_interval_seconds <= 0:
            raise ValueError(
                "strategy_heartbeat_interval_seconds must be positive"
            )
        for field_name in (
            "model_api_ready_timeout_seconds",
            "model_api_heartbeat_timeout_seconds",
            "model_api_execution_timeout_seconds",
            "model_api_request_timeout_seconds",
            "model_api_heartbeat_interval_seconds",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if (
            self.model_api_request_timeout_seconds
            >= self.model_api_execution_timeout_seconds
        ):
            raise ValueError(
                "model_api_request_timeout_seconds must be below execution timeout"
            )
        if (
            self.model_api_heartbeat_interval_seconds
            >= self.model_api_heartbeat_timeout_seconds
        ):
            raise ValueError(
                "model_api_heartbeat_interval_seconds must be below heartbeat timeout"
            )
        if self.retention_interval_seconds <= 0:
            raise ValueError("retention_interval_seconds must be positive")
        if self.retention_batch_size < 1 or self.retention_batch_size > 1000:
            raise ValueError("retention_batch_size must be between 1 and 1000")
        if self.retention_orphan_grace_seconds <= 0:
            raise ValueError("retention_orphan_grace_seconds must be positive")
        if self.object_gc_interval_seconds <= 0:
            raise ValueError("object_gc_interval_seconds must be positive")
        if self.maintenance_retry_seconds <= 0:
            raise ValueError("maintenance_retry_seconds must be positive")
        if self.maintenance_max_retry_seconds < self.maintenance_retry_seconds:
            raise ValueError(
                "maintenance_max_retry_seconds must be at least "
                "maintenance_retry_seconds"
            )
        if self.maintenance_continuation_seconds <= 0:
            raise ValueError("maintenance_continuation_seconds must be positive")


@dataclass(frozen=True, slots=True)
class SceneInspectionOutcome:
    job: JobInfo
    scene_revision: SceneRevisionInfo


@dataclass(frozen=True, slots=True)
class RegionExtractionOutcome:
    job: JobInfo
    region_set: "RegionSetInfo"


@dataclass(frozen=True, slots=True)
class RegionSourceOutcome:
    job: JobInfo
    scene_revision: SceneRevisionInfo
    region_set: "RegionSetInfo"


@dataclass(frozen=True, slots=True)
class CoreHostDiagnostics:
    blender_available: bool
    blender_executable: str | None


class CoreHost:
    """Own one ProjectStore and compose its application execution boundary."""

    def __init__(
        self,
        store: ProjectStore,
        config: CoreHostConfig,
        *,
        strategy_library: StrategyRuntimeLibrary | None = None,
        model_api_provider_registry: "ModelApiProviderRegistry | None" = None,
        model_api_secret_resolver: "SecretResolver | None" = None,
        extension_factories: tuple[CoreHostExtensionFactory, ...] = (),
    ) -> None:
        config.validate()
        if store.root.resolve() != config.project_path.resolve():
            raise ValueError("CoreHost config and ProjectStore paths differ")
        self.store = store
        self.config = config
        self.runtime = JobRuntime(
            store,
            lease_duration=timedelta(seconds=config.lease_seconds),
        )
        self.registry = WorkerRegistry()
        from verdflow.core import ExportProviderDefinition

        export_providers = (
            ExportProviderDefinition(
                provider_id=DTS_ADAPTER_ID,
                provider_version=DTS_ADAPTER_VERSION,
                target_type="dts",
                command_type=DTS_XML_EXPORT_COMMAND,
                command_version="1",
                output_media_type="application/xml",
                output_schema_id="verdflow.dts-model-point-xml",
                output_schema_version="1",
                supports_asset_selection=True,
                supports_terrain_hit_selection=True,
            ),
        )
        self.registry.register(DtsXmlExportWorker(self.runtime))
        strategy_authorizer = (
            StrategyExecutionAuthorizer(strategy_library, strategy_library)
            if strategy_library is not None
            else None
        )
        if strategy_library is not None and strategy_authorizer is not None:
            strategy_supervisor = StrategyWorkerSupervisor(
                self.runtime,
                strategy_authorizer,
                strategy_library,
                StrategyWorkerSupervisorConfig(
                    worker=StrategyWorkerConfig(
                        timeout_seconds=config.strategy_timeout_seconds,
                    ),
                    heartbeat_interval_seconds=(
                        config.strategy_heartbeat_interval_seconds
                    ),
                    runtime_root=(
                        (config.runtime_root or (store.root / "runtime"))
                        / "strategy-worker"
                    ),
                ),
            )
            self.registry.register(
                StrategyWorkerDriver(strategy_supervisor)
            )
            assignment_service = AssetAssignmentService(
                store,
                self.runtime,
                ProjectStrategyDocumentProvider(store),
                strategy_library,
                strategy_library,
            )
            self.registry.register(AssetAssignmentWorker(self.runtime))
        else:
            assignment_service = None
        self.blender_executable = (
            config.blender_executable or locate_blender()
            if config.enable_blender_worker
            else None
        )
        if self.blender_executable is not None:
            runtime_root = config.runtime_root or (
                store.root / "runtime" / "blender-worker"
            )
            supervisor = BlenderWorkerSupervisor(
                self.runtime,
                BlenderWorkerConfig(
                    blender_executable=self.blender_executable,
                    runtime_root=runtime_root,
                    scene_cache_root=store.root / "cache" / "blender-scenes",
                    ready_timeout_seconds=config.blender_ready_timeout_seconds,
                    heartbeat_timeout_seconds=(
                        config.blender_heartbeat_timeout_seconds
                    ),
                    blocking_operation_timeout_seconds=(
                        config.blender_blocking_timeout_seconds
                    ),
                    execution_timeout_seconds=(
                        config.blender_execution_timeout_seconds
                    ),
                ),
            )
            self.registry.register(BlenderWorkerDriver(supervisor))
            elevation_providers = (
                ElevationProviderDefinition(
                    provider_id=BLENDER_WORKER_ADAPTER_ID,
                    provider_version="1",
                    command_type=BLENDER_SAMPLE_ELEVATION_COMMAND,
                    command_version="1",
                    max_attempts=2,
                ),
            )
            region_extraction_providers = (
                RegionExtractionProviderDefinition(
                    provider_id=BLENDER_WORKER_ADAPTER_ID,
                    provider_version="1",
                    command_type=BLENDER_EXTRACT_REGIONS_COMMAND,
                    command_version="1",
                    max_attempts=2,
                ),
            )
        else:
            elevation_providers = ()
            region_extraction_providers = ()
        extensions = tuple(
            factory.build(self.runtime)
            for factory in extension_factories
        )
        extension_command_types: set[str] = set()
        for extension in extensions:
            if not isinstance(extension, CoreHostExtension):
                raise TypeError(
                    "CoreHost extension factory must return CoreHostExtension"
                )
            for driver in extension.worker_drivers:
                self.registry.register(driver)
                extension_command_types.update(driver.descriptor.command_types)
            declared_provider_commands = {
                provider.command_type
                for provider in (
                    *extension.region_extraction_providers,
                    *extension.region_source_providers,
                    *extension.elevation_providers,
                )
            }
            unowned_commands = sorted(
                declared_provider_commands - extension_command_types
            )
            if unowned_commands:
                raise ValueError(
                    "CoreHost extension Provider command has no Worker owner: "
                    + ", ".join(unowned_commands)
                )
        self._extension_command_types = frozenset(extension_command_types)
        region_extraction_providers += tuple(
            provider
            for extension in extensions
            for provider in extension.region_extraction_providers
        )
        elevation_providers += tuple(
            provider
            for extension in extensions
            for provider in extension.elevation_providers
        )
        region_source_providers = tuple(
            provider
            for extension in extensions
            for provider in extension.region_source_providers
        )
        model_analysis_providers = ()
        if config.enable_model_api_worker:
            from verdflow.core import ModelAnalysisProviderDefinition
            from verdflow.model_api import (
                MODEL_API_ANALYZE_MATERIALS_COMMAND,
                EnvironmentSecretResolver,
                ModelApiProviderRegistry,
                ModelApiWorkerConfig,
                ModelApiWorkerSupervisor,
            )
            from .model_api_driver import ModelApiWorkerDriver

            registry = model_api_provider_registry or ModelApiProviderRegistry()
            resolver = model_api_secret_resolver or EnvironmentSecretResolver(
                provider_registry=registry
            )
            model_supervisor = ModelApiWorkerSupervisor(
                self.runtime,
                registry,
                resolver,
                ModelApiWorkerConfig(
                    runtime_root=(
                        (config.runtime_root or (store.root / "runtime"))
                        / "model-api-worker"
                    ),
                    ready_timeout_seconds=config.model_api_ready_timeout_seconds,
                    heartbeat_timeout_seconds=(
                        config.model_api_heartbeat_timeout_seconds
                    ),
                    execution_timeout_seconds=(
                        config.model_api_execution_timeout_seconds
                    ),
                    request_timeout_seconds=(
                        config.model_api_request_timeout_seconds
                    ),
                    heartbeat_interval_seconds=(
                        config.model_api_heartbeat_interval_seconds
                    ),
                ),
            )
            self.registry.register(ModelApiWorkerDriver(model_supervisor))
            model_analysis_providers = tuple(
                ModelAnalysisProviderDefinition(
                    provider_id=provider.descriptor.provider_id,
                    provider_version=provider.descriptor.provider_version,
                    protocol=provider.descriptor.protocol,
                    default_model=provider.descriptor.default_model,
                    command_type=MODEL_API_ANALYZE_MATERIALS_COMMAND,
                    command_version="1",
                    max_attempts=1,
                )
                for provider in registry.list()
            )
        self.application = ApplicationFacade(
            store,
            self.runtime,
            cancellation_port=self.registry,
            strategy_authorizer=strategy_authorizer,
            region_extraction_providers=region_extraction_providers,
            region_source_providers=region_source_providers,
            elevation_providers=elevation_providers,
            asset_assignment_service=assignment_service,
            export_providers=export_providers,
            model_analysis_providers=model_analysis_providers,
        )
        self.plant_run_executions = PlantRunExecutionService(
            store,
            self.runtime,
            self.application,
        )
        self.composition_output_executions = CompositionOutputExecutionService(
            store,
            self.runtime,
            self.plant_run_executions,
            self.application,
        )
        self.material_flow_executions = MaterialFlowExecutionService(
            store,
            self.runtime,
            self.application,
        )
        self.scene_preparations = ScenePreparationExecutionService(
            store,
            self.runtime,
            self,
            self.material_flow_executions,
        )
        self.maintenance_scheduler: MaintenanceScheduler | None = None
        if config.enable_project_maintenance:
            self.maintenance_scheduler = MaintenanceScheduler()
            self.maintenance_scheduler.register(
                RetentionMaintenanceTask(
                    JobRetentionSweeper(
                        store,
                        orphan_grace=timedelta(
                            seconds=config.retention_orphan_grace_seconds
                        ),
                        batch_size=config.retention_batch_size,
                    ),
                    interval_seconds=config.retention_interval_seconds,
                    retry_interval_seconds=config.maintenance_retry_seconds,
                    max_retry_interval_seconds=(
                        config.maintenance_max_retry_seconds
                    ),
                    continuation_interval_seconds=(
                        config.maintenance_continuation_seconds
                    ),
                )
            )
            self.maintenance_scheduler.register(
                ObjectGarbageCollectionTask(
                    store,
                    interval_seconds=config.object_gc_interval_seconds,
                    retry_interval_seconds=config.maintenance_retry_seconds,
                    max_retry_interval_seconds=(
                        config.maintenance_max_retry_seconds
                    ),
                )
            )
        self.service_loop = ServiceLoop(
            self.runtime,
            self.registry,
            poll_interval_seconds=config.poll_interval_seconds,
            maintenance_scheduler=self.maintenance_scheduler,
            application_tasks=(
                self.scene_preparations,
                self.material_flow_executions,
                self.plant_run_executions,
                self.composition_output_executions,
            ),
        )
        self._closed = False

    @classmethod
    def diagnose(cls) -> CoreHostDiagnostics:
        blender = locate_blender()
        return CoreHostDiagnostics(
            blender_available=blender is not None,
            blender_executable=str(blender) if blender is not None else None,
        )

    @classmethod
    def create(
        cls,
        config: CoreHostConfig,
        *,
        project_name: str,
        strategy_library: StrategyRuntimeLibrary | None = None,
        model_api_provider_registry: "ModelApiProviderRegistry | None" = None,
        model_api_secret_resolver: "SecretResolver | None" = None,
        extension_factories: tuple[CoreHostExtensionFactory, ...] = (),
    ) -> CoreHost:
        store = ProjectStore.create(
            config.project_path,
            project_name,
            storage_profile=config.project_storage_profile,
        )
        try:
            return cls(
                store,
                config,
                strategy_library=strategy_library,
                model_api_provider_registry=model_api_provider_registry,
                model_api_secret_resolver=model_api_secret_resolver,
                extension_factories=extension_factories,
            )
        except Exception:
            store.close()
            raise

    @classmethod
    def open(
        cls,
        config: CoreHostConfig,
        *,
        strategy_library: StrategyRuntimeLibrary | None = None,
        model_api_provider_registry: "ModelApiProviderRegistry | None" = None,
        model_api_secret_resolver: "SecretResolver | None" = None,
        extension_factories: tuple[CoreHostExtensionFactory, ...] = (),
    ) -> CoreHost:
        store = ProjectStore.open(
            config.project_path,
            storage_profile=config.project_storage_profile,
        )
        try:
            return cls(
                store,
                config,
                strategy_library=strategy_library,
                model_api_provider_registry=model_api_provider_registry,
                model_api_secret_resolver=model_api_secret_resolver,
                extension_factories=extension_factories,
            )
        except Exception:
            store.close()
            raise

    def __enter__(self) -> CoreHost:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        self._require_open()
        if self.service_loop.state is ServiceLoopState.STOPPED:
            self.service_loop.start()

    def stop(self) -> None:
        if self.service_loop.state not in {
            ServiceLoopState.STOPPED,
            ServiceLoopState.FAILED,
        }:
            self.service_loop.stop()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self.store.close()
            self._closed = True

    def maintenance_status(self) -> tuple[MaintenanceExecution, ...]:
        self._require_open()
        if self.maintenance_scheduler is None:
            return ()
        return self.maintenance_scheduler.last_executions()

    def add_source_file(
        self,
        source_path: Path,
        *,
        kind: SourceKind = SourceKind.FBX,
        media_type: str | None = None,
    ) -> SourceInfo:
        self._require_open()
        source_path = source_path.resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        artifact = self.store.put_file(
            source_path,
            media_type=media_type or _media_type_for_source(kind),
        )
        return self.application.register_source(
            SourceRegistration(
                kind=kind,
                original_name=source_path.name,
                artifact_id=artifact.artifact_id,
            )
        )

    def add_source_bytes(
        self,
        content: bytes,
        *,
        original_name: str,
        kind: SourceKind = SourceKind.OTHER,
        media_type: str | None = None,
    ) -> SourceInfo:
        """Register a small Adapter-owned source without requiring a temp file."""
        self._require_open()
        if not original_name.strip():
            raise ValueError("original_name is required")
        artifact = self.store.put_bytes(
            content,
            media_type=media_type or _media_type_for_source(kind),
        )
        return self.application.register_source(
            SourceRegistration(
                kind=kind,
                original_name=original_name.strip(),
                artifact_id=artifact.artifact_id,
            )
        )

    def submit_extension_command(
        self,
        command: CommandDocument,
        *,
        idempotency_key: str,
        max_attempts: int = 1,
    ) -> JobInfo:
        """Submit only a command type declared by an installed Host extension."""
        self.start()
        if command.command_type not in self._extension_command_types:
            raise CoreHostError(
                "Command type is not owned by an installed CoreHost extension"
            )
        return self.application.submit_command(
            command,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )

    def create_identity_assembly(
        self,
        *,
        original_name: str,
        source_ids: tuple[str, ...],
    ) -> SourceInfo:
        return self.application.create_source_assembly(
            original_name=original_name,
            members=tuple(
                SourceAssemblyMemberSpec(source_id, IDENTITY_MATRIX)
                for source_id in source_ids
            ),
        )

    def submit_scene_inspection(
        self,
        source_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        self._require_blender()
        source = self.application.get_source(source_id)
        content = json.dumps(
            {
                "schema": "verdflow.blender-worker.command",
                "version": "1",
                "operation": "inspect_scene",
                "source_id": source.source_id,
                "source_artifact_id": source.artifact_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.application.submit_command(
            CommandDocument(
                command_type=BLENDER_INSPECT_SCENE_COMMAND,
                command_version="1",
                content=content,
                schema_id="verdflow.blender-worker.command",
                schema_version="1",
            ),
            idempotency_key=(
                idempotency_key
                or f"inspect-scene:{source.source_id}:{source.content_hash}:v1"
            ),
            max_attempts=2,
        )

    def finalize_scene_inspection(self, job_id: str) -> SceneRevisionInfo:
        """Persist the successful inspection result as a scene revision."""
        self._require_open()
        return self.application.finalize_scene_inspection(job_id)

    def inspect_scene(
        self,
        source_id: str,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = 1800.0,
    ) -> SceneInspectionOutcome:
        self.start()
        submitted = self.submit_scene_inspection(
            source_id,
            idempotency_key=idempotency_key,
        )
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error
                else completed.status.value
            )
            raise CoreHostError(f"Scene inspection failed: {message}")
        scene_revision = self.finalize_scene_inspection(completed.job_id)
        return SceneInspectionOutcome(completed, scene_revision)

    def generate_points(
        self,
        request: PlantingGenerationRequest,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> PlantingGenerationOutcome:
        self.start()
        try:
            submitted = self.application.submit_planting_generation(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise CoreHostError(f"Planting generation failed: {message}")
        point_set = self.application.finalize_generated_points(
            completed.job_id
        )
        return PlantingGenerationOutcome(completed, point_set)

    def extract_regions(
        self,
        request: "RegionExtractionRequest",
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = 1800.0,
    ) -> RegionExtractionOutcome:
        self.start()
        try:
            submitted = self.application.submit_region_extraction(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise CoreHostError(f"Region extraction failed: {message}")
        region_set = self.application.finalize_region_extraction(
            completed.job_id
        )
        return RegionExtractionOutcome(completed, region_set)

    def analyze_materials(
        self,
        request: MaterialFlowRequest,
        *,
        analysis_timeout_seconds: float = 300.0,
        extraction_timeout_seconds: float = 1800.0,
    ) -> MaterialFlowOutcome:
        self._require_open()
        return MaterialFlowCoordinator(self).execute(
            request,
            analysis_timeout_seconds=analysis_timeout_seconds,
            extraction_timeout_seconds=extraction_timeout_seconds,
        )

    def submit_material_analysis(
        self,
        request: "MaterialAnalysisRequest",
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        self.start()
        return self.application.submit_material_analysis(
            request,
            idempotency_key=idempotency_key,
        )

    def get_material_candidate(
        self,
        analysis_job_id: str,
    ) -> "MaterialClassificationCandidate":
        self._require_open()
        return self.application.load_material_candidate(analysis_job_id)

    def confirm_materials_and_submit_extraction(
        self,
        analysis_job_id: str,
        *,
        region_provider_id: str,
        allow_contains: bool = False,
        import_settings: dict[str, Any] | None = None,
    ) -> MaterialExtractionSubmission:
        self._require_open()
        return MaterialFlowCoordinator(self).confirm_and_submit(
            analysis_job_id,
            region_provider_id=region_provider_id,
            allow_contains=allow_contains,
            import_settings=import_settings,
        )

    def submit_region_extraction(
        self,
        request: "RegionExtractionRequest",
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        self.start()
        return self.application.submit_region_extraction(
            request,
            idempotency_key=idempotency_key,
        )

    def finalize_region_extraction(self, job_id: str) -> RegionSetInfo:
        self._require_open()
        return self.application.finalize_region_extraction(job_id)

    def prepare_region_source(
        self,
        request: "RegionSourceRequest",
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = 300.0,
    ) -> RegionSourceOutcome:
        self.start()
        submitted = self.application.submit_region_source(
            request,
            idempotency_key=idempotency_key,
        )
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise CoreHostError(f"Region source preparation failed: {message}")
        scene_revision, region_set = self.application.finalize_region_source(
            completed.job_id
        )
        return RegionSourceOutcome(completed, scene_revision, region_set)

    def submit_region_source(
        self,
        request: "RegionSourceRequest",
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        self.start()
        return self.application.submit_region_source(
            request,
            idempotency_key=idempotency_key,
        )

    def finalize_region_source(
        self,
        job_id: str,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        self._require_open()
        return self.application.finalize_region_source(job_id)

    def submit_scene_preparation(
        self,
        request: ScenePreparationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ScenePreparationInfo:
        self.start()
        return self.scene_preparations.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def submit_current_scene_preparation(
        self,
        request: ScenePreparationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ScenePreparationInfo:
        """Start the one current scene workflow for this fixed-source project."""
        self.start()
        workflow = self.store.project_workflow_state()
        if workflow.source_id not in {None, request.source_id}:
            raise CoreHostError(
                "Project source is fixed; create another project for a new source"
            )
        execution = self.scene_preparations.submit(
            request,
            idempotency_key=idempotency_key,
        )
        self.store.start_scene_preparation_workflow(execution)
        return execution

    def get_scene_preparation(
        self,
        execution_id: str,
    ) -> ScenePreparationInfo:
        self._require_open()
        return self.scene_preparations.get(execution_id)

    def confirm_scene_preparation(
        self,
        execution_id: str,
        selection: MaterialSelectionRequest | None = None,
    ) -> ScenePreparationInfo:
        self._require_open()
        return self.scene_preparations.confirm(execution_id, selection)

    def cancel_scene_preparation(
        self,
        execution_id: str,
    ) -> ScenePreparationInfo:
        self.start()
        return self.scene_preparations.request_cancel(execution_id)

    def submit_material_flow_execution(
        self,
        request: MaterialFlowExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ):
        self.start()
        return self.material_flow_executions.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def get_material_flow_execution_request(
        self,
        execution_id: str,
    ) -> MaterialFlowExecutionRequest:
        self._require_open()
        return self.material_flow_executions.get_request(execution_id)

    def get_material_flow_execution(self, execution_id: str):
        self._require_open()
        return self.material_flow_executions.get(execution_id)

    def confirm_material_flow_execution(
        self,
        execution_id: str,
        selection: MaterialSelectionRequest | None = None,
    ):
        self._require_open()
        return self.material_flow_executions.confirm(execution_id, selection)

    def get_material_facts(self, scene_revision_id: str) -> MaterialFactsView:
        self._require_open()
        return self.application.get_material_facts(scene_revision_id)

    def cancel_material_flow_execution(self, execution_id: str):
        self._require_open()
        return self.material_flow_executions.request_cancel(execution_id)

    def confirm_materials_and_extract(
        self,
        analysis_job_id: str,
        *,
        region_provider_id: str,
        allow_contains: bool = False,
        import_settings: dict[str, Any] | None = None,
        timeout_seconds: float = 1800.0,
    ) -> MaterialFlowOutcome:
        self._require_open()
        return MaterialFlowCoordinator(self).confirm_and_extract(
            analysis_job_id,
            region_provider_id=region_provider_id,
            allow_contains=allow_contains,
            import_settings=import_settings,
            timeout_seconds=timeout_seconds,
        )

    def sample_elevation(
        self,
        request: ElevationSamplingRequest,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = 1800.0,
    ) -> ElevationSamplingOutcome:
        self.start()
        try:
            submitted = self.application.submit_elevation_sampling(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise CoreHostError(f"Elevation sampling failed: {message}")
        point_set = self.application.finalize_elevation(completed.job_id)
        return ElevationSamplingOutcome(completed, point_set)

    def assign_assets(
        self,
        request: AssetAssignmentRequest,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = 300.0,
    ) -> AssetAssignmentOutcome:
        self.start()
        try:
            submitted = self.application.submit_asset_assignment(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise CoreHostError(f"Asset assignment failed: {message}")
        assignments = self.application.finalize_asset_assignment(
            completed.job_id
        )
        return AssetAssignmentOutcome(completed, assignments)

    def export_output(
        self,
        request: OutputExportRequest,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = 300.0,
    ) -> OutputExportOutcome:
        self.start()
        try:
            submitted = self.application.submit_output_export(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error
        completed = self.service_loop.run_until_terminal(
            submitted.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise CoreHostError(f"Output export failed: {message}")
        export = self.application.finalize_output_export(completed.job_id)
        return OutputExportOutcome(completed, export)

    def submit_output_export(
        self,
        request: OutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        """Submit output generation without blocking the project owner thread."""
        self.start()
        try:
            return self.application.submit_output_export(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error

    def submit_composition_output_export(
        self,
        request: CompositionOutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        """Submit one output over selected batches in the current composition."""
        self.start()
        try:
            return self.application.submit_composition_output_export(
                request,
                idempotency_key=idempotency_key,
            )
        except RuntimeError as error:
            raise CoreHostError(str(error)) from error

    def submit_composition_output_execution(
        self,
        request: CompositionOutputExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ):
        """Submit deferred elevation and one final composition output."""
        self.start()
        return self.composition_output_executions.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def get_composition_output_execution(self, execution_id: str):
        self._require_open()
        return self.composition_output_executions.get(execution_id)

    def get_active_composition_output_execution(self):
        self._require_open()
        return self.composition_output_executions.get_active()

    def finalize_output_export(
        self, job_id: str
    ) -> "ExportRecordInfo | CompositionExportRecordInfo":
        """Commit one completed output Job as an immutable ExportRecord."""
        self._require_open()
        return self.application.finalize_output_export(job_id)

    def run_planting(
        self,
        request: PlantRunRequest,
        *,
        timeouts: PlantRunTimeouts | None = None,
    ) -> PlantRunOutcome:
        """Execute the explicit generation-to-export flow with stage idempotency."""
        self.start()
        return PlantRunCoordinator(self).execute(request, timeouts=timeouts)

    def freeze_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        self.start()
        return self.application.freeze_profile(profile_id)

    def freeze_role_assignments(self, profile_id: str | None, overrides: dict[str, Any]) -> dict[str, Any]:
        self.start()
        return self.application.freeze_role_assignments(profile_id, overrides)

    def freeze_generation_args(self, profile_id: str | None, args: dict[str, Any]) -> dict[str, Any]:
        self.start()
        return self.application.freeze_generation_args(profile_id, args)

    def submit_plant_run_execution(
        self,
        request: PlantRunExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantRunExecutionInfo:
        """Persist a composed Plant Run and return without running a Worker."""
        self.start()
        return self.plant_run_executions.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def submit_current_plant_run_execution(
        self,
        request: PlantRunExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantRunExecutionInfo:
        """Replace the current Plant Run without retaining a product branch."""
        self.start()
        workflow = self.store.project_workflow_state()
        if (
            workflow.region_set_id is None
            and workflow.material_flow_execution_id is not None
        ):
            material_flow = self.store.get_material_flow_execution(
                workflow.material_flow_execution_id
            )
            workflow = self.store.synchronize_material_flow_workflow(
                material_flow
            )
        if (
            workflow.region_set_id is None
            and workflow.scene_preparation_execution_id is not None
        ):
            preparation = self.store.get_scene_preparation(
                workflow.scene_preparation_execution_id
            )
            workflow = self.store.synchronize_scene_preparation_workflow(
                preparation
            )
        if workflow.region_set_id != request.region_set_id:
            raise CoreHostError(
                "Plant Run must use the current workflow RegionSet"
            )
        execution = self.plant_run_executions.submit(
            request,
            idempotency_key=idempotency_key,
        )
        self.store.start_plant_run_workflow(
            execution,
            region_set_id=request.region_set_id,
        )
        return execution

    def get_plant_run_execution(
        self,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        self._require_open()
        return self.plant_run_executions.get(execution_id)

    def get_plant_run_execution_request(
        self,
        execution_id: str,
    ) -> PlantRunExecutionRequest:
        self._require_open()
        return self.plant_run_executions.get_request(execution_id)

    def get_material_flow_selection(
        self,
        execution_id: str,
    ) -> MaterialSelectionRequest | None:
        self._require_open()
        return self.material_flow_executions.get_selection(execution_id)

    def confirm_plant_run_execution(
        self,
        execution_id: str,
        request: PlantRunContinuationRequest,
    ) -> PlantRunExecutionInfo:
        """Confirm Review and continue from the existing PointSet."""
        self.start()
        return self.plant_run_executions.confirm(execution_id, request)

    def retry_plant_run_assignment(
        self,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        """Retry assignment without regenerating the existing PointSet."""

        self.start()
        return self.plant_run_executions.retry_assignment(execution_id)

    def cancel_plant_run_execution(
        self,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        self.start()
        return self.plant_run_executions.request_cancel(execution_id)

    def generate_and_assign(
        self,
        request: GenerateAndAssignRequest,
        *,
        timeouts: PlantRunTimeouts | None = None,
    ) -> GenerateAndAssignOutcome:
        """Generate points and resolve target assets without elevation."""
        self.start()
        return PlantRunCoordinator(self).generate_and_assign(
            request,
            timeouts=timeouts,
        )

    def resolve_elevation_and_export(
        self,
        request: ResolveElevationAndExportRequest,
        *,
        timeouts: PlantRunTimeouts | None = None,
    ) -> ResolveElevationAndExportOutcome:
        """Resolve terrain height and export a prepared planting run."""
        self.start()
        return PlantRunCoordinator(self).resolve_elevation_and_export(
            request,
            timeouts=timeouts,
        )

    def _require_blender(self) -> Path:
        if self.blender_executable is None:
            raise CoreHostError("Blender executable was not found")
        return self.blender_executable

    def _require_open(self) -> None:
        if self._closed:
            raise CoreHostError("CoreHost is closed")


def _media_type_for_source(kind: SourceKind) -> str:
    return {
        SourceKind.BLEND: "application/x-blender",
        SourceKind.FBX: "model/vnd.autodesk.fbx",
        SourceKind.GEOJSON: "application/geo+json",
        SourceKind.GEOTIFF: "image/tiff",
        SourceKind.GLTF: "model/gltf+json",
        SourceKind.SHAPEFILE: "application/vnd.verdflow.shapefile+zip",
        SourceKind.XML: "application/xml",
    }.get(kind, "application/octet-stream")
