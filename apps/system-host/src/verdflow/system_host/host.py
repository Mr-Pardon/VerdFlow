"""System-scoped composition root for VerdFlow desktop processes."""

from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import secrets
import shutil
from threading import RLock
import time
from typing import Any, Callable
from uuid import uuid4
from .asset_search import AssetSearchService, AssetSearchRequest, AssetSearchPage

from verdflow.asset_package import (
    AssetPackageError,
    AssetPackageResource,
    catalog_resource_ids,
    inspect_asset_catalog_package,
    read_asset_catalog_package,
    write_asset_catalog_package,
)
from verdflow.core import (
    AssignmentPreviewService,
    StrategyExecutionAuthorizer,
    StrategyPreviewService,
    CompositionOutputBatchRequest,
    CompositionOutputExecutionRequest,
    MaterialAnalysisRequest,
    MaterialFlowExecutionRequest,
    OutputExportRequest,
    RegionExtractionRequest,
    RegionSourceRequest,
    ScenePreparationRequest,
)
from verdflow.core_host import (
    CoreHostConfig,
    MaterialExtractionSubmission,
    PlantRunContinuationRequest,
    PlantRunExecutionRequest,
)
from verdflow.contracts import (
    AssignmentPreviewRequest,
    AssignmentPreviewResult,
    StrategyPreviewRequest,
    StrategyPreviewOperation,
    StrategyPreviewInput,
    StrategyPreviewResult,
    ArtifactRef,
    JobAttemptInfo,
    JobEvent,
    JobInfo,
    JobStatus,
    LibraryResourceInfo,
    AssetCatalogRemovalInfo,
    MaterialClassificationCandidate,
    MaterialConfirmationMode,
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStatus,
    MaterialFactsView,
    MaterialSelectionRequest,
    MaterialSelectionMode,
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    PlantRunReviewPolicy,
    RegionSetInfo,
    SceneRevisionInfo,
    ScenePreparationInfo,
    SourceInfo,
    SourceKind,
)
from verdflow.dts_adapter import (
    DtsLiveExecutionSnapshot,
    DtsLiveSelection,
)
from verdflow.core import (
    load_plant_run_execution_request,
    load_material_facts,
    load_material_selection,
    load_region_material_selection,
)
from verdflow.library_store import (
    AssetCatalogConflictError,
    AssetCatalogNotFoundError,
    LibraryIntegrityError,
    LibraryResourceNotFoundError as StoredLibraryResourceNotFoundError,
    RoleBindingProfileConflictError,
    StrategyNotInstalledError,
)
from verdflow.model_api import ModelApiClient, ModelApiError
from verdflow.planting import (
    build_binding_draft,
    profile_from_binding_draft,
    normalize_asset_selection,
    build_profile_v2,
    prepare_asset_catalog_document,
    validate_binding_strategy,
    sync_profile_catalog_metadata,
    update_profile_v2,
    prepare_role_binding_profile_document,
)
from verdflow.profile_package import (
    ProfilePackageError,
    inspect_role_binding_profile_package,
    write_role_binding_profile_package,
)
from verdflow.project_store import (
    ArtifactNotFoundError,
    ExportNotFoundError,
    JobNotFoundError,
    MaterialFlowExecutionNotFoundError,
    PlantRunExecutionNotFoundError,
    SystemProcessInspector,
    ScenePreparationNotFoundError,
)
from verdflow.strategy_worker import (
    StrategyPreviewWorker,
    StrategyPackage,
    StrategyPackageError,
    inspect_strategy_package,
    open_strategy_package_archive,
    write_strategy_package_archive,
)

from .instance import (
    SystemEndpointInfo,
    SystemEndpointStore,
    SystemHostInstanceGuard,
    system_root_id,
)
from .models import (
    AssetPackageExportInfo,
    AssetPackageImportInfo,
    AuthoringKitExportInfo,
    AssetCatalogAssetPage,
    AssetCatalogDetail,
    AssetCatalogSaveRequest,
    AssetCatalogSummary,
    ExportDownloadInfo,
    ExternalRuntimeInfo,
    DtsLiveConfigurationOptions,
    DtsLiveConfigurationRequest,
    DtsLiveStrategyOption,
    ProjectSessionInfo,
    ProjectSessionState,
    ProjectRemovalInfo,
    RecentProjectInfo,
    JobCancellationInfo,
    MaterialFlowCancellationInfo,
    ScenePreparationCancellationInfo,
    ModelProviderInfo,
    ModelProviderModelInfo,
    ModelProviderModelList,
    ModelProviderTestResult,
    PlantExportRequest,
    PlantExportSubmission,
    PlantRunCancellationInfo,
    PlantPreviewOutputRequest,
    PlantPreviewReexportInfo,
    PlantPreviewInputInfo,
    PlantPreviewRequest,
    PlantPreviewSubmission,
    PlantingBatchCommitRequest,
    PlantingBatchSummary,
    PlantingCompositionExportRequest,
    PlantingCompositionSummary,
    CompositionExportOperationInfo,
    ProjectXmlOutputInfo,
    ProfilePackageExportInfo,
    ProfilePackageImportInfo,
    RoleBindingProfileDetail,
    RoleBindingProfileRemovalInfo,
    RoleBindingProfileSaveRequest,
    RoleBindingProfileSummary,
    StrategyLibraryDetail,
    StrategyLibrarySummary,
    StrategyPackageExportInfo,
    StrategyPackageImportInfo,
    StrategyRemovalInfo,
    SystemHostConfig,
    SystemHostState,
    SystemHostStatus,
    ProjectBusinessSummary,
    ProjectTaskPage,
)
from .library_views import (
    profile_source_catalogs,
    catalog_asset_page,
    catalog_summary,
    profile_detail,
    profile_summary,
    strategy_detail,
    strategy_summary,
)
from .planting_routes import (
    resolve_plant_output_route,
    resolve_region_extraction_provider,
)
from .model_providers import ModelProviderConfigStore
from .external_runtimes import (
    BLENDER_RUNTIME_ID,
    ExternalRuntimeConfigStore,
    ExternalRuntimeResolution,
)
from .dts_live import freeze_dts_live_execution
from .profile_strategies import ProfileStrategyResolver
from verdflow.contracts import require_strategy_version, strategy_version_accepts
from .dts_live_connector import (
    DtsLiveBootstrap,
    DtsLiveConnectorManager,
)
from .dts_live_runtime import DtsLiveTemporaryRuntime
from .project_registry import (
    ProjectRegistry,
    ProjectWorkspace,
    availability,
)
from .project_views import (
    decode_job_cursor,
    project_business_summary,
    project_task_page,
)
from .runtime import LibraryRuntime, ProjectRuntime
from .strategy_previews import StrategyPreviewRuntime
from .resource_downloads import StarterManifest, StarterResource
from .starter_resources import StarterImportOperation
from .plan_imports import PlanImportService
from .plan_import_library import PlanImportLibrary
from .secret_store import SecretStore, SecretStoreError, platform_secret_store


class SystemHostError(RuntimeError):
    """Raised when the system-scoped runtime cannot satisfy a request."""


class SystemHostAlreadyRunningError(SystemHostError):
    def __init__(self, endpoint: SystemEndpointInfo | None) -> None:
        message = "A SystemHost already owns this system root"
        if endpoint is not None:
            message += f": {endpoint.base_url}"
        super().__init__(message)
        self.endpoint = endpoint


class ProjectSessionNotFoundError(SystemHostError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Project session was not found: {session_id}")
        self.session_id = session_id


class ProjectResourceNotFoundError(SystemHostError):
    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(f"{resource_type} was not found: {resource_id}")
        self.resource_type = resource_type
        self.resource_id = resource_id


class LibraryResourceNotFoundError(SystemHostError):
    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(f"{resource_type} was not found: {resource_id}")
        self.resource_type = resource_type
        self.resource_id = resource_id


class SystemHost:
    """Own the user library and coordinate isolated project owner threads."""

    def __init__(
        self,
        config: SystemHostConfig,
        *,
        secret_store: SecretStore | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._state = SystemHostState.STOPPED
        self._library: LibraryRuntime | None = None
        self._strategy_previews = StrategyPreviewRuntime()
        self._starter_resources = PlanImportService(self._import_starter_resource,
            PlanImportLibrary(self._library_runtime, self.config.project_call_timeout_seconds),
            lambda package: self._install_inspected_strategy(package)[1])
        self._asset_search = AssetSearchService()
        self._projects: dict[str, ProjectRuntime] = {}
        self._sessions_by_path: dict[str, str] = {}
        self._lock = RLock()
        self._lifecycle_lock = RLock()
        self._system_root = config.resolved_system_path()
        self._instance_id = str(uuid4())
        self._system_root_id = system_root_id(self._system_root)
        self._instance_guard = SystemHostInstanceGuard(self._system_root)
        self._instance_guard_acquired = False
        self._endpoint_store = SystemEndpointStore(self._system_root)
        self._endpoint: SystemEndpointInfo | None = None
        self._process_identity = None
        self._provider_store = ModelProviderConfigStore(
            self._system_root
        )
        self._external_runtime_store = ExternalRuntimeConfigStore(
            self._system_root
        )
        self._dts_live_connector = DtsLiveConnectorManager(
            self.freeze_dts_live_execution,
        )
        self._dts_live_runtimes: dict[str, DtsLiveTemporaryRuntime] = {}
        self._project_registry = ProjectRegistry(self._system_root)
        self._secret_store = secret_store or platform_secret_store()

    def __enter__(self) -> SystemHost:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def state(self) -> SystemHostState:
        with self._lock:
            return self._state

    @property
    def dts_live_connector(self) -> DtsLiveConnectorManager:
        """Return the trusted in-process port used by the Connector app."""

        self._require_running()
        return self._dts_live_connector

    def start(self) -> SystemHostStatus:
        with self._lifecycle_lock:
            with self._lock:
                if self._state is SystemHostState.RUNNING:
                    return self.status()
                if self._state is not SystemHostState.STOPPED:
                    raise SystemHostError(
                        f"Cannot start SystemHost from {self._state}"
                    )
                if not self._instance_guard.acquire(
                    self.config.project_start_timeout_seconds
                ):
                    try:
                        endpoint = self._endpoint_store.read()
                    except ValueError:
                        endpoint = None
                    raise SystemHostAlreadyRunningError(endpoint)
                self._instance_guard_acquired = True
                self._endpoint_store.clear_stale()
                self._process_identity = (
                    SystemProcessInspector().current_identity()
                )
                library = LibraryRuntime(self.config.library_path)
                self._library = library
                self._strategy_previews = StrategyPreviewRuntime()
                self._starter_resources = PlanImportService(self._import_starter_resource,
                    PlanImportLibrary(self._library_runtime, self.config.project_call_timeout_seconds),
                    lambda package: self._install_inspected_strategy(package)[1])
            try:
                self._provider_store.prepare()
                self._external_runtime_store.prepare()
                self._project_registry.prepare()
                library.start(self.config.project_start_timeout_seconds)
            except BaseException:
                try:
                    library.close(
                        self.config.project_call_timeout_seconds
                    )
                finally:
                    self._release_instance_guard()
                with self._lock:
                    self._state = SystemHostState.FAILED
                raise
            with self._lock:
                self._state = SystemHostState.RUNNING
            return self.status()

    def status(self) -> SystemHostStatus:
        with self._lifecycle_lock:
            with self._lock:
                state = self._state
                library = self._library
                active_count = sum(
                    runtime.snapshot().state is ProjectSessionState.ACTIVE
                    for runtime in self._projects.values()
                )
            library_available = (
                state is SystemHostState.RUNNING and library is not None
            )
            if library_available and library is not None:
                library.call(
                    lambda store: store.root,
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
            return SystemHostStatus(
                state=state,
                instance_id=self._instance_id,
                system_root_id=self._system_root_id,
                library_path=str(self.config.library_path.resolve()),
                library_available=library_available,
                active_project_count=active_count,
                model_provider_count=len(
                    self._provider_store.registry().list()
                ),
                secret_store_kind=type(self._secret_store).__name__,
                endpoint_base_url=(
                    self._endpoint.base_url
                    if self._endpoint is not None
                    else None
                ),
            )

    def publish_endpoint(
        self,
        *,
        endpoint_id: str,
        base_url: str,
        protocol_version: str,
    ) -> SystemEndpointInfo:
        with self._lifecycle_lock:
            self._require_running()
            if (
                self._endpoint is not None
                and self._endpoint.endpoint_id != endpoint_id
            ):
                raise SystemHostError(
                    "SystemHost already has a published endpoint"
                )
            if self._process_identity is None:
                raise SystemHostError("SystemHost process identity is unavailable")
            endpoint = SystemEndpointInfo.create(
                endpoint_id=endpoint_id,
                instance_id=self._instance_id,
                system_root_id=self._system_root_id,
                process_identity=self._process_identity,
                base_url=base_url,
                protocol_version=protocol_version,
            )
            self._endpoint_store.publish(endpoint)
            self._endpoint = endpoint
            return endpoint

    def clear_endpoint(self, endpoint_id: str) -> bool:
        with self._lifecycle_lock:
            endpoint = self._endpoint
            if endpoint is None or endpoint.endpoint_id != endpoint_id:
                return False
            cleared = self._endpoint_store.clear(
                instance_id=self._instance_id,
                endpoint_id=endpoint_id,
            )
            self._endpoint = None
            return cleared

    def list_model_providers(self) -> tuple[ModelProviderInfo, ...]:
        self._require_running()
        return tuple(
            self._provider_info(provider)
            for provider in self._provider_store.registry().list()
        )

    def list_external_runtimes(self) -> tuple[ExternalRuntimeInfo, ...]:
        self._require_running()
        return tuple(
            self._external_runtime_info(item)
            for item in self._external_runtime_store.list()
        )

    def get_external_runtime(self, runtime_id: str) -> ExternalRuntimeInfo:
        self._require_running()
        return self._external_runtime_info(
            self._external_runtime_store.get(runtime_id)
        )

    def configure_external_runtime(
        self,
        runtime_id: str,
        path: str,
    ) -> ExternalRuntimeInfo:
        self._require_running()
        return self._external_runtime_info(
            self._external_runtime_store.configure(runtime_id, path)
        )

    def clear_external_runtime(self, runtime_id: str) -> ExternalRuntimeInfo:
        self._require_running()
        return self._external_runtime_info(
            self._external_runtime_store.clear(runtime_id)
        )

    def freeze_dts_live_execution(
        self,
        request: DtsLiveConfigurationRequest,
    ) -> DtsLiveExecutionSnapshot:
        self._require_running()
        library = self._library_runtime()
        return library.call(
            lambda store: freeze_dts_live_execution(store, request),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def dts_live_configuration_options(self) -> DtsLiveConfigurationOptions:
        """Return the narrow trusted strategy and DTS asset view used by WebUI."""
        catalogs = self.list_asset_catalogs("dts")

        def query(store: Any) -> tuple[DtsLiveStrategyOption, ...]:
            resolver = ProfileStrategyResolver(store)
            catalog_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
            plans = [
                profile_summary(item, store, catalog_cache, resolver)
                for item in store.list_role_binding_profiles(target_type="dts")
            ]
            options = []
            for package in resolver.strategies:
                if not resolver.trusted(package):
                    continue
                detail = strategy_detail(store, package.package_hash)
                profiles = tuple(
                    plan for plan in plans
                    if resolver.accepts(package, resolver.load_profile(plan.profile_id))
                )
                options.append(DtsLiveStrategyOption(
                    summary=detail.summary,
                    description=detail.description,
                    roles=detail.roles,
                    default_random_seed=detail.default_random_seed,
                    generation_parameters=detail.generation_parameters,
                    profiles=profiles,
                    parameter_preview=detail.parameter_preview,
                ))
            return tuple(options)

        return DtsLiveConfigurationOptions(
            strategies=self._library_runtime().call(
                query, timeout_seconds=self.config.project_call_timeout_seconds,
            ),
            catalogs=catalogs,
        )

    def create_dts_live_session(self) -> DtsLiveBootstrap:
        """Create one ephemeral Connector session and its private Core runtime."""
        with self._lifecycle_lock:
            self._require_running()
            bootstrap = self._dts_live_connector.create_session()
            session_id = bootstrap.connector_session_id
            runtime = DtsLiveTemporaryRuntime(
                connector_session_id=session_id,
                project_path=(
                    self._system_root
                    / "runtime"
                    / "dts-live"
                    / f"{session_id}.verdflow"
                ),
                library_path=self.config.library_path,
                port=self._dts_live_connector.trusted_port(session_id),
                idle_cycle_seconds=self.config.project_idle_cycle_seconds,
            )
            try:
                runtime.start(self.config.project_start_timeout_seconds)
            except BaseException:
                try:
                    runtime.close(
                        self.config.project_call_timeout_seconds,
                        remove_files=True,
                    )
                finally:
                    self._dts_live_connector.discard_session(session_id)
                raise
            with self._lock:
                self._dts_live_runtimes[session_id] = runtime
            return bootstrap

    def submit_dts_live_selection(self, selection: DtsLiveSelection) -> None:
        """Queue a selected polygon without blocking the Connector callback."""
        self._require_running()
        session_id = selection.correlation.connector_session_id
        with self._lock:
            runtime = self._dts_live_runtimes.get(session_id)
        if runtime is None:
            raise SystemHostError(
                f"DTS Live runtime was not found: {session_id}"
            )
        runtime.submit(selection)

    def close_dts_live_session(self, connector_session_id: str) -> None:
        """Release the private Core runtime after Connector authorization."""
        with self._lock:
            runtime = self._dts_live_runtimes.pop(
                connector_session_id,
                None,
            )
        if runtime is not None:
            runtime.close(
                self.config.project_call_timeout_seconds,
                remove_files=True,
            )

    def discard_dts_live_session(self, connector_session_id: str) -> None:
        """Trusted cleanup for sessions that cannot complete HTTP shutdown."""

        self.close_dts_live_session(connector_session_id)
        self._dts_live_connector.discard_session(connector_session_id)

    def upsert_model_provider(
        self,
        document: dict[str, Any],
    ) -> ModelProviderInfo:
        self._require_running()
        provider = self._provider_store.upsert(document)
        return self._provider_info(provider)

    def delete_model_provider(self, provider_id: str) -> ModelProviderInfo:
        self._require_running()
        provider = self._provider_store.delete(provider_id)
        return self._provider_info(provider)

    def set_model_provider_secret(
        self,
        provider_id: str,
        secret_value: str,
    ) -> ModelProviderInfo:
        self._require_running()
        provider = self._provider_store.registry().get(provider_id)
        self._secret_store.set(provider.secret_ref, secret_value)
        return self._provider_info(provider)

    def delete_model_provider_secret(self, provider_id: str) -> ModelProviderInfo:
        self._require_running()
        provider = self._provider_store.registry().get(provider_id)
        self._secret_store.delete(provider.secret_ref)
        return self._provider_info(provider)

    def set_model_provider_model(
        self,
        provider_id: str,
        model: str,
    ) -> ModelProviderInfo:
        self._require_running()
        provider = self._provider_store.set_selected_model(provider_id, model)
        return self._provider_info(provider)

    def list_model_provider_models(
        self,
        provider_id: str,
    ) -> ModelProviderModelList:
        self._require_running()
        registry = self._provider_store.registry()
        provider = registry.get(provider_id)
        api_key = self._secret_store.resolve(provider.secret_ref)
        models = ModelApiClient(provider_registry=registry).list_models(
            provider_id=provider_id,
            api_key=api_key,
            timeout_seconds=self.config.provider_test_timeout_seconds,
        )
        return ModelProviderModelList(
            provider_id=provider_id,
            items=tuple(
                ModelProviderModelInfo(
                    model_id=item.model_id,
                    display_name=item.display_name,
                    owned_by=item.owned_by,
                )
                for item in models
            ),
        )

    def test_model_provider(
        self,
        provider_id: str,
        *,
        model: str | None = None,
    ) -> ModelProviderTestResult:
        self._require_running()
        registry = self._provider_store.registry()
        provider = registry.get(provider_id)
        selected_model = model or provider.descriptor.default_model
        started = time.monotonic()
        if selected_model is None:
            return ModelProviderTestResult(
                provider_id=provider_id,
                model="",
                succeeded=False,
                duration_ms=self._elapsed_ms(started),
                message="Select a model before testing this Provider",
            )
        try:
            api_key = self._secret_store.resolve(provider.secret_ref)
            ModelApiClient(provider_registry=registry).analyze_materials(
                {
                    "schema": "verdflow.provider-connection-test",
                    "materials": [
                        {
                            "name": "VerdFlow_Connection_Test",
                            "face_count": 0,
                            "object_count": 0,
                            "objects": [],
                            "diffuse_color": [],
                            "textures": [],
                        }
                    ],
                },
                provider_id=provider_id,
                api_key=api_key,
                input_ref="system:provider-connection-test",
                model=selected_model,
                timeout_seconds=self.config.provider_test_timeout_seconds,
                max_output_tokens=1024,
            )
        except (ModelApiError, SecretStoreError, ValueError) as error:
            return ModelProviderTestResult(
                provider_id=provider_id,
                model=selected_model,
                succeeded=False,
                duration_ms=self._elapsed_ms(started),
                message=str(error),
            )
        return ModelProviderTestResult(
            provider_id=provider_id,
            model=selected_model,
            succeeded=True,
            duration_ms=self._elapsed_ms(started),
            message="Provider connection and structured response are valid",
        )

    def list_strategies(self) -> tuple[StrategyLibrarySummary, ...]:
        library = self._library_runtime()
        return library.call(
            lambda store: tuple(
                strategy_summary(store, item)
                for item in store.list_strategies()
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_strategy(self, package_hash: str) -> StrategyLibraryDetail:
        library = self._library_runtime()
        result = library.call(
            lambda store: strategy_detail(store, package_hash),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        if result is None:
            raise LibraryResourceNotFoundError("strategy", package_hash)
        return result

    def install_strategy(self, source_root: Path) -> StrategyLibraryDetail:
        self._require_running()
        inspected = inspect_strategy_package(source_root)
        detail, _ = self._install_inspected_strategy(inspected)
        return detail

    def submit_strategy_preview(
        self, request: StrategyPreviewRequest,
    ) -> StrategyPreviewOperation:
        library = self._library_runtime()

        def prepare(store: Any) -> tuple[StrategyPackage, StrategyPreviewInput]:
            authorizer = StrategyExecutionAuthorizer(store, store)
            authorizer.authorize(request.package_hash)
            package = inspect_strategy_package(
                store.strategy_package_root(request.package_hash)
            )
            if package.package_hash != request.package_hash:
                raise ValueError("Strategy package changed after installation")
            prepared = StrategyPreviewService(authorizer).prepare(
                request, package.strategy
            )
            return package, prepared

        package, prepared = library.call(
            prepare, timeout_seconds=self.config.project_call_timeout_seconds
        )
        directory = self._system_root / "runtime" / "strategy-previews"
        directory.mkdir(parents=True, exist_ok=True)
        worker = StrategyPreviewWorker(package, directory)

        def generate(cancelled: Callable[[], bool]) -> StrategyPreviewResult:
            result = StrategyPreviewService.run(
                prepared, worker, cancellation_requested=cancelled
            )
            if not cancelled():
                library.call(
                    lambda store: StrategyExecutionAuthorizer(store, store).authorize(
                        request.package_hash
                    ),
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
            return result

        return self._strategy_previews.submit(request.package_hash, generate)

    def preview_asset_assignments(self, request: AssignmentPreviewRequest) -> AssignmentPreviewResult:
        self._require_running()
        return AssignmentPreviewService.resolve(request)

    def get_strategy_preview(self, preview_id: str) -> StrategyPreviewOperation:
        self._require_running()
        return self._strategy_previews.get(preview_id)

    def cancel_strategy_preview(self, preview_id: str) -> StrategyPreviewOperation:
        self._require_running()
        return self._strategy_previews.cancel(preview_id)

    def starter_resource_manifest(self) -> StarterManifest:
        self._require_running()
        return self._starter_resources.manifest()

    def prepare_resource_import(self, paths: tuple[str, ...], *, recommended: bool = False,
                                strategy_paths: tuple[str, ...] = ()) -> StarterImportOperation:
        self._require_running()
        return self._starter_resources.prepare(paths, recommended=recommended, strategy_paths=strategy_paths)

    def confirm_resource_import(self, operation_id: str, *, optional_ids: tuple[str, ...] = (),
                                overwrite_ids: tuple[str, ...] = ()) -> StarterImportOperation:
        self._require_running()
        return self._starter_resources.confirm(operation_id, optional_ids, overwrite_ids)

    def start_starter_import(self, revision: str) -> StarterImportOperation:
        self._require_running()
        return self._starter_resources.start(revision)

    def get_starter_import(self, operation_id: str) -> StarterImportOperation:
        self._require_running()
        return self._starter_resources.get(operation_id)

    def cancel_starter_import(self, operation_id: str) -> StarterImportOperation:
        self._require_running()
        return self._starter_resources.cancel(operation_id)

    def _import_starter_resource(self, item: StarterResource, path: Path) -> bool:
        self._require_running()
        if item.kind == "assets":
            inspected = inspect_asset_catalog_package(path)
            if inspected.catalog.catalog_id != item.identity or inspected.catalog.target_type != "dts":
                raise ValueError("资产包身份与推荐清单不符。")
            try:
                return self.import_asset_catalog_package(path).already_installed
            except SystemHostError as error:
                if "same id already has different content" in str(error):
                    raise ValueError("已有同名标识的资产库包含不同内容，未覆盖；请在资产库中手动处理。") from error
                raise
        with open_strategy_package_archive(path) as opened:
            if opened.package.package_id != item.identity or opened.package.package_version != item.version:
                raise ValueError("策略包身份或版本与推荐清单不符。")
            _, existing = self._install_inspected_strategy(opened.package)
            return existing

    def import_strategy_package(
        self,
        source_path: Path,
    ) -> StrategyPackageImportInfo:
        self._require_running()
        try:
            with open_strategy_package_archive(source_path) as opened:
                detail, already_installed = self._install_inspected_strategy(
                    opened.package
                )
                return StrategyPackageImportInfo(
                    source_path=opened.source_path,
                    package_hash=opened.package.package_hash,
                    archive_hash=opened.archive_hash,
                    already_installed=already_installed,
                    strategy=detail.summary,
                )
        except StrategyPackageError as error:
            raise ValueError(str(error)) from error

    def export_strategy_package(
        self,
        package_hash: str,
        destination_path: Path,
    ) -> StrategyPackageExportInfo:
        self._require_running()
        library = self._library_runtime()

        def export(store: Any) -> StrategyPackageExportInfo:
            try:
                package = inspect_strategy_package(
                    store.strategy_package_root(package_hash)
                )
            except StrategyNotInstalledError as error:
                raise LibraryResourceNotFoundError(
                    "strategy",
                    package_hash,
                ) from error
            archive = write_strategy_package_archive(
                package,
                destination_path,
            )
            detail = strategy_detail(store, package_hash)
            if detail is None:
                raise SystemHostError("Exported strategy cannot be queried")
            return StrategyPackageExportInfo(
                destination_path=archive.destination_path,
                package_hash=archive.package_hash,
                archive_hash=archive.archive_hash,
                byte_size=archive.byte_size,
                strategy=detail.summary,
            )

        try:
            return library.call(
                export,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except StrategyPackageError as error:
            raise ValueError(str(error)) from error

    def export_authoring_kit(
        self,
        skill_id: str,
        destination_path: Path,
    ) -> AuthoringKitExportInfo:
        self._require_running()
        from verdflow.authoring_kits import write_authoring_kit

        archive = write_authoring_kit(skill_id, destination_path)
        return AuthoringKitExportInfo(
            skill_id=archive.skill_id,
            destination_path=archive.destination_path,
            archive_hash=archive.archive_hash,
            byte_size=archive.byte_size,
        )

    def _install_inspected_strategy(
        self,
        inspected: StrategyPackage,
    ) -> tuple[StrategyLibraryDetail, bool]:
        require_strategy_version(inspected.package_version)
        library = self._library_runtime()

        def install(store: Any) -> tuple[StrategyLibraryDetail, bool]:
            already_installed = (
                store.get_strategy_by_hash(inspected.package_hash) is not None
            )
            info = store.install_verified_strategy(
                inspected.root,
                package_id=inspected.package_id,
                package_version=inspected.package_version,
                package_hash=inspected.package_hash,
                display_name=inspected.strategy["display_name"],
            )
            managed = inspect_strategy_package(
                store.strategy_package_root(info.package_hash)
            )
            if managed.package_hash != inspected.package_hash:
                store.uninstall_strategy(info.package_hash)
                raise SystemHostError(
                    "Strategy package changed while it was being imported"
                )
            detail = strategy_detail(store, info.package_hash)
            if detail is None:
                raise SystemHostError("Imported strategy cannot be queried")
            return detail, already_installed

        return library.call(
            install,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def trust_strategy(
        self,
        package_hash: str,
        *,
        reason: str | None = None,
    ) -> StrategyLibrarySummary:
        return self._set_strategy_trust(
            package_hash,
            trusted=True,
            reason=reason,
        )

    def revoke_strategy_trust(
        self,
        package_hash: str,
        *,
        reason: str | None = None,
    ) -> StrategyLibrarySummary:
        return self._set_strategy_trust(
            package_hash,
            trusted=False,
            reason=reason,
        )

    def uninstall_strategy(self, package_hash: str) -> StrategyRemovalInfo:
        self._require_running()
        library = self._library_runtime()
        try:
            removed = library.call(
                lambda store: store.uninstall_strategy(package_hash),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except StrategyNotInstalledError as error:
            raise LibraryResourceNotFoundError(
                "strategy",
                package_hash,
            ) from error
        return StrategyRemovalInfo(
            package_hash=removed.package_hash,
            removed=True,
        )

    def list_asset_catalogs(
        self,
        target_type: str,
    ) -> tuple[AssetCatalogSummary, ...]:
        normalized_target = self._required_library_text(
            target_type,
            "target_type",
        )
        library = self._library_runtime()

        def query(store: Any) -> tuple[AssetCatalogSummary, ...]:
            return tuple(
                sorted(
                    (
                        catalog_summary(item)
                        for item in store.list_asset_catalogs(
                            target_type=normalized_target
                        )
                    ),
                    key=lambda item: (
                        item.display_name.casefold(),
                        item.catalog_id,
                    ),
                )
            )

        return library.call(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_asset_catalog(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogDetail:
        target = self._required_library_text(target_type, "target_type")
        identifier = self._required_library_text(catalog_id, "catalog_id")
        library = self._library_runtime()

        def query(store: Any) -> AssetCatalogDetail | None:
            info = store.get_asset_catalog(target, identifier)
            if info is None:
                return None
            document = store.load_asset_catalog_document(target, identifier)
            return AssetCatalogDetail(
                info=info,
                document=self._json_object(document.canonical_json),
            )

        result = library.call(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        if result is None:
            raise LibraryResourceNotFoundError(
                "asset catalog",
                f"{target}/{identifier}",
            )
        return result

    def search_asset_catalogs(self, request: AssetSearchRequest) -> AssetSearchPage:
        request.validate()
        return self._library_runtime().call(
            lambda store: self._asset_search.search(store, request),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_asset_catalog_assets(
        self,
        target_type: str,
        catalog_id: str,
        *,
        after_ordinal: int = -1,
        limit: int = 200,
        collection_id: str | None = None,
        query: str | None = None,
    ) -> AssetCatalogAssetPage:
        if after_ordinal < -1:
            raise ValueError("after_ordinal must be at least -1")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        target = self._required_library_text(target_type, "target_type")
        identifier = self._required_library_text(catalog_id, "catalog_id")
        collection_filter = self._optional_library_text(
            collection_id,
            "collection_id",
        )
        query_filter = self._optional_library_text(query, "query")
        library = self._library_runtime()

        def query(store: Any) -> AssetCatalogAssetPage | None:
            info = store.get_asset_catalog(target, identifier)
            if info is None:
                return None
            return catalog_asset_page(
                store,
                info,
                after_ordinal=after_ordinal,
                limit=limit,
                collection_id=collection_filter,
                query=query_filter,
            )

        result = library.call(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        if result is None:
            raise LibraryResourceNotFoundError(
                "asset catalog",
                f"{target}/{identifier}",
            )
        return result

    def save_asset_catalog(
        self,
        request: AssetCatalogSaveRequest,
    ) -> AssetCatalogDetail:
        if request.expected_generation < 0:
            raise ValueError("expected_generation must not be negative")
        prepared = prepare_asset_catalog_document(request.document)
        library = self._library_runtime()
        try:
            info = library.call(
                lambda store: store.save_asset_catalog(
                    prepared,
                    expected_generation=request.expected_generation,
                ),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except AssetCatalogConflictError as error:
            raise SystemHostError(str(error)) from error
        except (AssetCatalogNotFoundError, LibraryIntegrityError) as error:
            raise SystemHostError(str(error)) from error
        return AssetCatalogDetail(
            info=info,
            document=self._json_object(prepared.canonical_json),
        )

    def delete_asset_catalog(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogRemovalInfo:
        target = self._required_library_text(target_type, "target_type")
        identifier = self._required_library_text(catalog_id, "catalog_id")
        library = self._library_runtime()
        try:
            return library.call(
                lambda store: store.delete_asset_catalog(target, identifier),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except AssetCatalogNotFoundError as error:
            raise LibraryResourceNotFoundError(
                "asset catalog",
                f"{target}/{identifier}",
            ) from error

    def import_library_thumbnail(self, source_path: Path) -> LibraryResourceInfo:
        self._require_running()
        source = source_path.resolve(strict=True)
        media_type = _thumbnail_media_type(source)
        library = self._library_runtime()
        return library.call(
            lambda store: store.import_resource(source, media_type=media_type),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def import_asset_catalog_package(
        self,
        source_path: Path,
        *,
        replace_existing: bool = False,
    ) -> AssetPackageImportInfo:
        self._require_running()
        library = self._library_runtime()
        try:
            inspection = inspect_asset_catalog_package(source_path)
            catalog_value = self._json_object(
                inspection.catalog.canonical_json
            )
            document = prepare_asset_catalog_document(catalog_value)
            if (
                document.semantic_hash != inspection.catalog.semantic_hash
                or document.canonical_json != inspection.catalog.canonical_json
            ):
                raise ValueError(
                    "Asset package Catalog failed domain validation"
                )

            existing = library.call(
                lambda store: store.get_asset_catalog(
                    document.target_type,
                    document.catalog_id,
                ),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
            if existing is not None:
                installed = library.call(
                    lambda store: store.load_asset_catalog_document(
                        document.target_type,
                        document.catalog_id,
                    ),
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
                if (
                    installed.semantic_hash != document.semantic_hash
                    or installed.canonical_json != document.canonical_json
                ):
                    if not replace_existing:
                        raise SystemHostError(
                            "A Catalog with the same id already has different content"
                        )

            def import_resource(resource: AssetPackageResource) -> None:
                imported = library.call(
                    lambda store: store.import_resource_bytes(
                        resource.payload,
                        media_type=resource.media_type,
                    ),
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
                if imported.resource_id != resource.resource_id:
                    raise ValueError(
                        f"Resource identity mismatch: {resource.resource_id}"
                    )

            read_asset_catalog_package(
                source_path,
                expected_package_hash=inspection.package_hash,
                on_resource=import_resource,
            )
            already_installed = (
                existing is not None
                and existing.semantic_hash == document.semantic_hash
            )
            if already_installed:
                catalog_info = existing
            else:
                catalog_info = library.call(
                    lambda store: store.save_asset_catalog(
                        document,
                        expected_generation=(
                            existing.generation if existing is not None else 0
                        ),
                        origin_package_hash=inspection.package_hash,
                    ),
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
            return AssetPackageImportInfo(
                source_path=inspection.source_path,
                package_hash=inspection.package_hash,
                resource_count=len(inspection.resources),
                already_installed=already_installed,
                catalog=catalog_info,
            )
        except AssetPackageError as error:
            raise ValueError(str(error)) from error
        except (AssetCatalogConflictError, LibraryIntegrityError) as error:
            raise SystemHostError(str(error)) from error

    def export_asset_catalog_package(
        self,
        target_type: str,
        catalog_id: str,
        destination_path: Path,
    ) -> AssetPackageExportInfo:
        self._require_running()
        target = self._required_library_text(target_type, "target_type")
        identifier = self._required_library_text(catalog_id, "catalog_id")
        library = self._library_runtime()
        try:
            catalog_info = library.call(
                lambda store: store.get_asset_catalog(target, identifier),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
            if catalog_info is None:
                raise LibraryResourceNotFoundError(
                    "asset catalog",
                    f"{target}/{identifier}",
                )
            document = library.call(
                lambda store: store.load_asset_catalog_document(
                    target,
                    identifier,
                ),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )

            def resources():
                for resource_id in catalog_resource_ids(document):
                    info, payload = library.call(
                        lambda store, identifier=resource_id: store.read_resource(
                            identifier
                        ),
                        timeout_seconds=self.config.project_call_timeout_seconds,
                    )
                    yield AssetPackageResource(
                        resource_id=info.resource_id,
                        content_hash=info.content_hash,
                        media_type=info.media_type,
                        payload=payload,
                    )

            archive = write_asset_catalog_package(
                document,
                resources(),
                destination_path,
            )
            return AssetPackageExportInfo(
                destination_path=archive.destination_path,
                package_hash=archive.package_hash,
                byte_size=archive.byte_size,
                resource_count=archive.resource_count,
                catalog=catalog_info,
            )
        except AssetPackageError as error:
            raise ValueError(str(error)) from error

    def read_library_resource(
        self,
        resource_id: str,
    ) -> tuple[LibraryResourceInfo, bytes]:
        self._require_running()
        identifier = self._required_library_text(resource_id, "resource_id")
        library = self._library_runtime()
        try:
            return library.call(
                lambda store: store.read_resource(identifier),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except StoredLibraryResourceNotFoundError as error:
            raise LibraryResourceNotFoundError(
                "library resource",
                identifier,
            ) from error

    def list_role_binding_profiles(
        self,
        target_type: str,
        *,
        strategy_id: str | None = None,
        strategy_version: str | None = None,
        catalog_id: str | None = None,
    ) -> tuple[RoleBindingProfileSummary, ...]:
        target = self._required_library_text(target_type, "target_type")
        library = self._library_runtime()
        catalog_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        requested_id = self._optional_library_text(strategy_id, "strategy_id")
        requested_version = self._optional_library_text(strategy_version, "strategy_version")

        def query(store: Any) -> tuple[RoleBindingProfileSummary, ...]:
            resolver = ProfileStrategyResolver(store)
            packages = [
                item for item in resolver.strategies
                if (requested_id is None or item.package_id == requested_id)
                and item.package_version == requested_version
            ] if requested_version else []
            profiles = store.list_role_binding_profiles(
                    target_type=target,
                    strategy_id=requested_id,
                    catalog_id=self._optional_library_text(
                        catalog_id, "catalog_id"
                    ),
                )
            return tuple(
                profile_summary(item, store, catalog_cache, resolver)
                for item in profiles
                if requested_version is None or any(
                    resolver.accepts(package, resolver.load_profile(item.profile_id))
                    for package in packages
                )
            )

        return library.call(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_role_binding_profile(
        self,
        profile_id: str,
    ) -> RoleBindingProfileDetail:
        identifier = self._required_library_text(profile_id, "profile_id")
        library = self._library_runtime()

        def query(store: Any) -> RoleBindingProfileDetail | None:
            info = store.get_role_binding_profile(identifier)
            return profile_detail(store, info) if info is not None else None

        result = library.call(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        if result is None:
            raise LibraryResourceNotFoundError("role binding profile", identifier)
        return result

    def save_role_binding_profile(
        self,
        request: RoleBindingProfileSaveRequest,
    ) -> RoleBindingProfileDetail:
        if request.expected_generation < 0:
            raise ValueError("expected_generation must not be negative")
        if not isinstance(request.assignments, dict) or not request.assignments:
            raise ValueError("assignments must be a non-empty object")
        profile_id = request.profile_id or f"profile_{uuid4().hex[:12]}"
        display_name = self._required_library_text(
            request.display_name, "display_name"
        )
        strategy_id = self._required_library_text(
            request.strategy_id, "strategy_id"
        )
        strategy_version = self._required_library_text(
            request.strategy_version, "strategy_version"
        )
        target_type = self._required_library_text(
            request.target_type, "target_type"
        )
        catalog_id = self._optional_library_text(request.catalog_id, "catalog_id")
        library = self._library_runtime()

        def save(store: Any) -> RoleBindingProfileDetail:
            existing = store.get_role_binding_profile(profile_id)
            source_id = existing.profile_id if existing is not None else request.source_profile_id
            if source_id is not None and store.get_role_binding_profile(source_id) is None:
                raise ValueError("Source Profile is not installed")
            base_profile = (
                self._json_object(
                    store.load_role_binding_profile_document(
                        source_id
                    ).canonical_json
                )
                if source_id is not None
                else None
            )
            if base_profile is not None and (
                base_profile["strategy"]["strategy_id"] != strategy_id
                or not strategy_version_accepts(
                    base_profile["strategy"]["strategy_version"], strategy_version
                )
                or base_profile["target"]["target_type"] != target_type
            ):
                raise ValueError("A Profile must retain its strategy, target and compatible version")
            self_contained = all(
                isinstance(assignment, dict) and isinstance(assignment.get("candidates"), list)
                and all(isinstance(candidate, dict) and "target_asset_ref" in candidate
                        for candidate in assignment["candidates"])
                for assignment in request.assignments.values()
            )
            if catalog_id is not None and not self_contained:
                strategy, catalog, _source = self._effective_binding_inputs(
                    store,
                    strategy_id,
                    strategy_version,
                    target_type,
                    catalog_id,
                )
                profile = build_profile_v2(
                    profile_id=profile_id,
                    display_name=display_name,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                    catalog=catalog,
                    assignments=request.assignments,
                    base_profile=base_profile,
                )
            else:
                if base_profile is None and not self_contained:
                    raise ValueError(
                        "catalog_id is required when creating a Profile"
                    )
                strategies = tuple(
                    item
                    for item in store.list_strategies()
                    if item.package_id == strategy_id
                    and item.package_version == strategy_version
                )
                if len(strategies) != 1:
                    raise SystemHostError(
                        "Profile requires one exact installed Strategy: "
                        f"{strategy_id}@{strategy_version}"
                    )
                strategy = inspect_strategy_package(
                    store.strategy_package_root(strategies[0].package_hash)
                ).strategy
                if self_contained:
                    profile = profile_from_binding_draft(
                        build_binding_draft(request.assignments, target_type=target_type),
                        profile_id=profile_id, display_name=display_name,
                        strategy_id=strategy_id, strategy_version=strategy_version,
                    )
                    if base_profile is not None and "strategy_parameters" in base_profile:
                        profile["strategy_parameters"] = base_profile["strategy_parameters"]
                else:
                    profile = update_profile_v2(
                        base_profile,
                        display_name=display_name,
                        assignments=request.assignments,
                    )
                    profile["profile_id"] = profile_id
                    profile["strategy"]["strategy_version"] = strategy_version
            if request.strategy_parameters is not None:
                profile["strategy_parameters"] = request.strategy_parameters
            elif request.replace_strategy_parameters:
                profile.pop("strategy_parameters", None)
            validate_binding_strategy(strategy, profile)
            info = store.save_role_binding_profile(
                prepare_role_binding_profile_document(profile),
                expected_generation=request.expected_generation,
            )
            return profile_detail(store, info)

        try:
            return library.call(
                save,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except (
            AssetCatalogNotFoundError,
            LibraryIntegrityError,
            RoleBindingProfileConflictError,
        ) as error:
            raise SystemHostError(str(error)) from error

    def delete_role_binding_profile(
        self,
        profile_id: str,
    ) -> RoleBindingProfileRemovalInfo:
        identifier = self._required_library_text(profile_id, "profile_id")
        library = self._library_runtime()
        removed = library.call(
            lambda store: store.delete_role_binding_profile(identifier),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return RoleBindingProfileRemovalInfo(
            profile_id=identifier,
            removed=removed,
        )

    def sync_role_binding_profile_assets(
        self,
        profile_id: str,
        *,
        expected_generation: int,
    ) -> RoleBindingProfileDetail:
        identifier = self._required_library_text(profile_id, "profile_id")
        if expected_generation < 0:
            raise ValueError("expected_generation must not be negative")
        library = self._library_runtime()

        def sync(store: Any) -> RoleBindingProfileDetail:
            info = store.get_role_binding_profile(identifier)
            if info is None:
                raise LibraryResourceNotFoundError(
                    "role binding profile",
                    identifier,
                )
            profile = self._json_object(
                store.load_role_binding_profile_document(
                    identifier
                ).canonical_json
            )
            catalogs = profile_source_catalogs(store, profile)
            if not catalogs:
                raise SystemHostError("No linked asset Catalog is currently installed")
            synced = profile
            for catalog in catalogs:
                synced = sync_profile_catalog_metadata(synced, catalog)
            saved = store.save_role_binding_profile(
                prepare_role_binding_profile_document(synced),
                expected_generation=expected_generation,
            )
            return profile_detail(store, saved)

        try:
            return library.call(
                sync,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except (
            AssetCatalogNotFoundError,
            LibraryIntegrityError,
            RoleBindingProfileConflictError,
        ) as error:
            raise SystemHostError(str(error)) from error

    def import_role_binding_profile_package(
        self,
        source_path: Path,
    ) -> ProfilePackageImportInfo:
        self._require_running()
        library = self._library_runtime()
        try:
            inspection = inspect_role_binding_profile_package(source_path)
            value = self._json_object(inspection.profile.canonical_json)
            document = prepare_role_binding_profile_document(value)

            def install(store: Any) -> ProfilePackageImportInfo:
                resolution = ProfileStrategyResolver(store).resolve(
                    value, require_trust=False,
                    original_package_hash=inspection.strategy.package_hash,
                )
                strategy = resolution.strategy
                if strategy is None:
                    raise LibraryResourceNotFoundError(
                        "strategy dependency",
                        f"{inspection.strategy.strategy_id}@"
                        f"{inspection.strategy.strategy_version}",
                    )
                validate_binding_strategy(
                    inspect_strategy_package(store.strategy_package_root(strategy.package_hash)).strategy,
                    value,
                )
                existing = store.get_role_binding_profile(document.profile_id)
                saved = store.save_role_binding_profile(
                    document,
                    expected_generation=(existing.generation if existing else 0),
                )
                return ProfilePackageImportInfo(
                    source_path=inspection.source_path,
                    package_hash=inspection.package_hash,
                    already_installed=existing is not None,
                    profile=profile_summary(saved),
                )

            return library.call(
                install,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ProfilePackageError as error:
            raise ValueError(str(error)) from error

    def export_role_binding_profile_package(
        self,
        profile_id: str,
        destination_path: Path,
    ) -> ProfilePackageExportInfo:
        self._require_running()
        identifier = self._required_library_text(profile_id, "profile_id")
        library = self._library_runtime()

        def export_facts(store: Any) -> tuple[Any, Any, Any]:
            info = store.get_role_binding_profile(identifier)
            if info is None:
                raise LibraryResourceNotFoundError(
                    "role binding profile", identifier
                )
            document = store.load_role_binding_profile_document(identifier)
            value = self._json_object(document.canonical_json)
            resolver = ProfileStrategyResolver(store)
            resolution = resolver.resolve(value)
            if resolution.strategy is None:
                resolution = resolver.resolve(value, require_trust=False)
            if resolution.strategy is None:
                raise SystemHostError(resolution.issue)
            package = resolution.strategy
            # The exchange freezes its own usable baseline, not a mutation of the local plan.
            value["strategy"]["strategy_version"] = package.package_version
            return info, prepare_role_binding_profile_document(value), package

        info, document, strategy = library.call(
            export_facts,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        archive = write_role_binding_profile_package(
            document,
            strategy_package_hash=strategy.package_hash,
            destination=destination_path,
        )
        return ProfilePackageExportInfo(
            destination_path=archive.destination_path,
            package_hash=archive.package_hash,
            byte_size=archive.byte_size,
            profile=profile_summary(info),
        )

    def create_project(
        self,
        project_path: Path,
        *,
        project_name: str,
        enable_blender_worker: bool = True,
        enable_geospatial_worker: bool = False,
        blender_executable: Path | None = None,
        enable_model_api_worker: bool = False,
        workspace: ProjectWorkspace | None = None,
    ) -> ProjectSessionInfo:
        if not project_name.strip():
            raise ValueError("project_name is required")
        return self._start_project(
            project_path,
            create_name=project_name.strip(),
            enable_blender_worker=enable_blender_worker,
            enable_geospatial_worker=enable_geospatial_worker,
            blender_executable=blender_executable,
            enable_model_api_worker=enable_model_api_worker,
            workspace=workspace,
        )

    def open_project(
        self,
        project_path: Path,
        *,
        enable_blender_worker: bool = True,
        enable_geospatial_worker: bool = False,
        blender_executable: Path | None = None,
        enable_model_api_worker: bool = False,
        workspace: ProjectWorkspace | None = None,
    ) -> ProjectSessionInfo:
        return self._start_project(
            project_path,
            create_name=None,
            enable_blender_worker=enable_blender_worker,
            enable_geospatial_worker=enable_geospatial_worker,
            blender_executable=blender_executable,
            enable_model_api_worker=enable_model_api_worker,
            workspace=workspace,
        )

    def list_projects(self) -> tuple[ProjectSessionInfo, ...]:
        self._require_running()
        with self._lock:
            snapshots = tuple(runtime.snapshot() for runtime in self._projects.values())
        return tuple(sorted(snapshots, key=lambda item: item.opened_at))

    def list_recent_projects(self) -> tuple[RecentProjectInfo, ...]:
        self._require_running()
        with self._lock:
            active_by_path = {
                self._path_key(Path(runtime.snapshot().project_path)):
                    runtime.snapshot().session_id
                for runtime in self._projects.values()
                if runtime.snapshot().state is ProjectSessionState.ACTIVE
            }
        return tuple(
            RecentProjectInfo(
                registry_id=record.registry_id,
                project_id=record.project_id,
                name=record.name,
                project_path=record.project_path,
                last_opened_at=record.last_opened_at,
                last_workspace=(
                    record.last_workspace.value
                    if record.last_workspace is not None
                    else None
                ),
                availability=availability(record).value,
                active_session_id=active_by_path.get(
                    self._path_key(Path(record.project_path))
                ),
                last_error=record.last_error,
            )
            for record in self._project_registry.list()
        )

    def delete_project(self, registry_id: str) -> ProjectRemovalInfo:
        """Delete one registered project and its owned local directory."""

        with self._lifecycle_lock:
            self._require_running()
            record = self._project_registry.get(registry_id)
            if record is None:
                raise ProjectResourceNotFoundError(
                    "RecentProject",
                    registry_id,
                )
            project_path = Path(record.project_path).resolve()
            key = self._path_key(project_path)
            with self._lock:
                session_id = self._sessions_by_path.get(key)
            if session_id is not None:
                self.close_project(session_id)

            files_removed = False
            if (
                project_path.is_dir()
                and not project_path.is_symlink()
                and not getattr(project_path, "is_junction", lambda: False)()
                and (project_path / "project.db").is_file()
            ):
                shutil.rmtree(project_path)
                files_removed = True

            if not self._project_registry.remove(record.registry_id):
                raise SystemHostError(
                    "Project files were removed but registry cleanup failed"
                )
            return ProjectRemovalInfo(
                registry_id=record.registry_id,
                project_id=record.project_id,
                project_path=str(project_path),
                removed=True,
                files_removed=files_removed,
            )

    def get_project(self, session_id: str) -> ProjectSessionInfo:
        self._require_running()
        with self._lock:
            runtime = self._projects.get(session_id)
        if runtime is None:
            raise ProjectSessionNotFoundError(session_id)
        return runtime.snapshot()

    def get_job(self, session_id: str, job_id: str) -> JobInfo:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.get_job(job_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError("Job", job_id) from error

    def list_project_tasks(
        self,
        session_id: str,
        *,
        statuses: tuple[JobStatus, ...] = (),
        before: str | None = None,
        limit: int = 50,
    ) -> ProjectTaskPage:
        runtime = self._project_runtime(session_id)
        page = runtime.query(
            lambda store: store.list_jobs(
                statuses=statuses,
                before=decode_job_cursor(before),
                limit=limit,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return project_task_page(page)

    def get_project_business_summary(
        self,
        session_id: str,
    ) -> ProjectBusinessSummary:
        runtime = self._project_runtime(session_id)

        def query(store):
            workflow, preparation, material_flow, plant_run = (
                store.current_workflow_executions()
            )
            scene_revision_id = (
                store.get_region_set(workflow.region_set_id).scene_revision_id
                if workflow.region_set_id is not None
                else preparation.scene_revision_id if preparation is not None else None
            )
            scene_revision = (
                store.get_scene_revision(scene_revision_id)
                if scene_revision_id is not None
                else None
            )
            return project_business_summary(
                store.project_info(),
                workflow=workflow,
                scene_revision_id=scene_revision_id,
                scene_importer_id=(
                    scene_revision.importer_id
                    if scene_revision is not None
                    else None
                ),
                scene_preparation=preparation,
                material_flow=material_flow,
                plant_run=plant_run,
            )

        return runtime.query(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_job_events(
        self,
        session_id: str,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobEvent, ...]:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.list_job_events(
                    job_id,
                    after_sequence=after_sequence,
                    limit=limit,
                ),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError("Job", job_id) from error

    def get_job_attempts(
        self,
        session_id: str,
        job_id: str,
    ) -> tuple[JobAttemptInfo, ...]:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.list_job_attempts(job_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError("Job", job_id) from error

    def get_artifact(
        self,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactRef:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.get_artifact(artifact_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ArtifactNotFoundError as error:
            raise ProjectResourceNotFoundError("Artifact", artifact_id) from error

    def get_export_download(
        self,
        session_id: str,
        export_id: str,
    ) -> ExportDownloadInfo:
        runtime = self._project_runtime(session_id)

        def query(store: Any) -> ExportDownloadInfo:
            try:
                record = store.get_export_record(export_id)
                composition_export = False
            except ExportNotFoundError:
                record = store.get_composition_export_record(export_id)
                composition_export = True
            artifact = store.get_artifact(record.output_artifact_id)
            extension = "xml" if "xml" in artifact.media_type else "bin"
            job = store.get_job(record.producer_job_id)
            command_artifact = store.get_artifact(job.command.artifact_id)
            command = json.loads(store.read_artifact_chunk(
                job.command.artifact_id, offset=0, limit=command_artifact.byte_size,
            ))
            return ExportDownloadInfo(
                export_id=record.export_id,
                artifact_id=artifact.artifact_id,
                media_type=artifact.media_type,
                byte_size=artifact.byte_size,
                suggested_filename=f"verdflow-{record.export_id}.{extension}",
                offset=(record.offset_x, record.offset_y, record.offset_z),
                selected_asset_ids=(
                    None
                    if composition_export
                    else normalize_asset_selection(
                        command.get("selected_asset_ids")
                    )
                ),
            )

        try:
            return runtime.query(
                query,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ExportNotFoundError as error:
            raise ProjectResourceNotFoundError("Export", export_id) from error
        except ArtifactNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Export Artifact",
                export_id,
            ) from error

    def list_project_xml_outputs(
        self,
        session_id: str,
        *,
        limit: int = 200,
    ) -> tuple[ProjectXmlOutputInfo, ...]:
        runtime = self._project_runtime(session_id)

        def query(store: Any) -> tuple[ProjectXmlOutputInfo, ...]:
            result: list[ProjectXmlOutputInfo] = []
            for record in store.list_composition_export_records(limit=limit):
                artifact = store.get_artifact(record.output_artifact_id)
                extension = "xml" if "xml" in artifact.media_type else "bin"
                result.append(
                    ProjectXmlOutputInfo(
                        export_id=record.export_id,
                        suggested_filename=(
                            f"verdflow-{record.export_id}.{extension}"
                        ),
                        media_type=artifact.media_type,
                        byte_size=artifact.byte_size,
                        record_count=record.record_count,
                        offset=(
                            record.offset_x,
                            record.offset_y,
                            record.offset_z,
                        ),
                        created_at=record.created_at,
                    )
                )
            return tuple(result)

        return runtime.query(
            query,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def delete_project_xml_output(
        self,
        session_id: str,
        export_id: str,
    ) -> ProjectXmlOutputInfo:
        runtime = self._project_runtime(session_id)

        def remove(host: Any) -> ProjectXmlOutputInfo:
            store = host.store
            record = store.get_composition_export_record(export_id)
            artifact = store.get_artifact(record.output_artifact_id)
            extension = "xml" if "xml" in artifact.media_type else "bin"
            info = ProjectXmlOutputInfo(
                export_id=record.export_id,
                suggested_filename=f"verdflow-{record.export_id}.{extension}",
                media_type=artifact.media_type,
                byte_size=artifact.byte_size,
                record_count=record.record_count,
                offset=(record.offset_x, record.offset_y, record.offset_z),
                created_at=record.created_at,
            )
            store.delete_composition_export_record(export_id)
            return info

        try:
            return runtime.call(
                remove,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ExportNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Project XML Output",
                export_id,
            ) from error

    def read_export_chunk(
        self,
        session_id: str,
        export_id: str,
        *,
        offset: int,
        limit: int,
    ) -> bytes:
        runtime = self._project_runtime(session_id)

        def query(store: Any) -> bytes:
            try:
                record = store.get_export_record(export_id)
            except ExportNotFoundError:
                record = store.get_composition_export_record(export_id)
            return store.read_artifact_chunk(
                record.output_artifact_id,
                offset=offset,
                limit=limit,
            )

        try:
            return runtime.query(
                query,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ExportNotFoundError as error:
            raise ProjectResourceNotFoundError("Export", export_id) from error
        except ArtifactNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Export Artifact",
                export_id,
            ) from error

    def add_source_file(
        self,
        session_id: str,
        source_path: Path,
        *,
        kind: SourceKind = SourceKind.FBX,
        media_type: str | None = None,
    ) -> SourceInfo:
        runtime = self._project_runtime(session_id)

        def add(host):
            if host.store.project_workflow_state().source_id is not None:
                raise SystemHostError(
                    "Project source is fixed; create another project for a new source"
                )
            if kind is SourceKind.SHAPEFILE:
                from verdflow.geospatial_worker import package_shapefile

                return host.add_source_bytes(
                    package_shapefile(source_path),
                    original_name=f"{source_path.stem}.shapefile.zip",
                    kind=kind,
                    media_type=(
                        media_type
                        or "application/vnd.verdflow.shapefile+zip"
                    ),
                )
            return host.add_source_file(
                source_path,
                kind=kind,
                media_type=media_type,
            )

        return runtime.call(
            add,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def create_identity_source_assembly(
        self,
        session_id: str,
        *,
        original_name: str,
        source_ids: tuple[str, ...],
    ) -> SourceInfo:
        runtime = self._project_runtime(session_id)

        def assemble(host):
            if host.store.project_workflow_state().source_id is not None:
                raise SystemHostError(
                    "Project source is fixed; create another project for a new source"
                )
            return host.create_identity_assembly(
                original_name=original_name,
                source_ids=source_ids,
            )

        return runtime.call(
            assemble,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def submit_material_analysis(
        self,
        session_id: str,
        request: MaterialAnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_material_analysis(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_material_candidate(
        self,
        session_id: str,
        analysis_job_id: str,
    ) -> MaterialClassificationCandidate:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.call(
                lambda host: host.get_material_candidate(analysis_job_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Job",
                analysis_job_id,
            ) from error
        except RuntimeError as error:
            raise SystemHostError(str(error)) from error

    def confirm_materials_and_submit_extraction(
        self,
        session_id: str,
        analysis_job_id: str,
        *,
        region_provider_id: str,
        allow_contains: bool = False,
        import_settings: dict[str, Any] | None = None,
    ) -> MaterialExtractionSubmission:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.call(
                lambda host: host.confirm_materials_and_submit_extraction(
                    analysis_job_id,
                    region_provider_id=region_provider_id,
                    allow_contains=allow_contains,
                    import_settings=import_settings,
                ),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Job",
                analysis_job_id,
            ) from error
        except RuntimeError as error:
            raise SystemHostError(str(error)) from error

    def submit_region_extraction(
        self,
        session_id: str,
        request: RegionExtractionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_region_extraction(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def finalize_region_extraction(
        self,
        session_id: str,
        job_id: str,
    ) -> RegionSetInfo:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.call(
                lambda host: host.finalize_region_extraction(job_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError("Job", job_id) from error
        except RuntimeError as error:
            raise SystemHostError(str(error)) from error

    def submit_region_source(
        self,
        session_id: str,
        request: RegionSourceRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_region_source(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def finalize_region_source(
        self,
        session_id: str,
        job_id: str,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.call(
                lambda host: host.finalize_region_source(job_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError("Job", job_id) from error
        except RuntimeError as error:
            raise SystemHostError(str(error)) from error

    def submit_plant_run_execution(
        self,
        session_id: str,
        request: PlantRunExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantRunExecutionInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_plant_run_execution(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def _submit_current_plant_run_execution(
        self,
        session_id: str,
        request: PlantRunExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantRunExecutionInfo:
        if request.profile_id:
            assignments, args = self._library_runtime().call(
                lambda store: ProfileStrategyResolver(store).prepare_execution(
                    request.strategy_package_hash, request.profile_id,
                    request.binding_overrides, request.generation_args,
                ),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
            request = replace(request, binding_overrides=assignments, generation_args=args)
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_current_plant_run_execution(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def confirm_plant_run_execution(
        self,
        session_id: str,
        execution_id: str,
        request: PlantRunContinuationRequest,
    ) -> PlantRunExecutionInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.confirm_plant_run_execution(
                execution_id,
                request,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def retry_plant_run_assignment(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.retry_plant_run_assignment(execution_id),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def submit_plant_preview(
        self,
        session_id: str,
        request: PlantPreviewRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantPreviewSubmission:
        seed = self._plant_run_seed(request.random_seed, idempotency_key)
        execution = self._submit_current_plant_run_execution(
            session_id,
            PlantRunExecutionRequest(
                scene_revision_id=request.scene_revision_id,
                region_set_id=request.region_set_id,
                selected_region_ids=request.selected_region_ids,
                strategy_package_hash=request.strategy_package_hash,
                random_seed=seed,
                target_type=request.target_type,
                catalog_id=request.catalog_id,
                review_policy=PlantRunReviewPolicy.REQUIRED,
                profile_id=request.profile_id,
                assignment_random_seed=request.assignment_random_seed,
                generation_args=request.generation_args,
                binding_overrides=request.binding_overrides,
            ),
            idempotency_key=idempotency_key,
        )
        return PlantPreviewSubmission(execution=execution, random_seed=seed)

    def submit_plant_export(
        self,
        session_id: str,
        request: PlantExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantExportSubmission:
        planting = request.planting
        offset = self._validated_plant_offset(request.offset)
        runtime = self._project_runtime(session_id)
        scene = runtime.query(
            lambda store: store.get_scene_revision(
                planting.scene_revision_id
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        route = resolve_plant_output_route(
            scene_importer_id=scene.importer_id,
            target_type=planting.target_type,
        )
        seed = self._plant_run_seed(
            planting.random_seed,
            idempotency_key,
        )
        execution = self._submit_current_plant_run_execution(
            session_id,
            PlantRunExecutionRequest(
                scene_revision_id=planting.scene_revision_id,
                region_set_id=planting.region_set_id,
                selected_region_ids=planting.selected_region_ids,
                strategy_package_hash=planting.strategy_package_hash,
                random_seed=seed,
                target_type=planting.target_type,
                catalog_id=planting.catalog_id,
                review_policy=PlantRunReviewPolicy.AUTOMATIC,
                elevation_provider_id=route.elevation_provider_id,
                export_provider_id=route.export_provider_id,
                offset=offset,
                profile_id=planting.profile_id,
                assignment_random_seed=planting.assignment_random_seed,
                generation_args=planting.generation_args,
                binding_overrides=planting.binding_overrides,
            ),
            idempotency_key=idempotency_key,
        )
        return PlantExportSubmission(execution=execution, random_seed=seed)

    def get_plant_export(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        return self.get_plant_run_execution(session_id, execution_id)

    def get_plant_preview(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        return self.get_plant_run_execution(session_id, execution_id)

    def get_plant_preview_input(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantPreviewInputInfo:
        runtime = self._project_runtime(session_id)
        try:
            request = runtime.query(
                lambda store: load_plant_run_execution_request(store, execution_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except PlantRunExecutionNotFoundError as error:
            raise ProjectResourceNotFoundError("PlantRunExecution", execution_id) from error
        return PlantPreviewInputInfo(
            scene_revision_id=request.scene_revision_id,
            region_set_id=request.region_set_id,
            selected_region_ids=request.selected_region_ids,
            strategy_package_hash=request.strategy_package_hash,
            random_seed=request.random_seed,
            target_type=request.target_type,
            catalog_id=request.catalog_id,
            profile_id=request.profile_id,
            generation_args=request.generation_args,
            binding_overrides=request.binding_overrides,
        )

    def get_current_planting_composition(
        self,
        session_id: str,
    ) -> PlantingCompositionSummary | None:
        runtime = self._project_runtime(session_id)

        def read(store: Any) -> PlantingCompositionSummary | None:
            composition = store.current_planting_composition()
            if composition is None:
                return None
            summaries: list[PlantingBatchSummary] = []
            for batch in store.list_planting_batches():
                execution = store.get_plant_run_execution(
                    batch.plant_run_execution_id
                )
                point_set = store.get_point_set(batch.generated_point_set_id)
                planting_run = store.get_planting_run(
                    point_set.planting_run_id
                )
                summaries.append(
                    PlantingBatchSummary(
                        batch_id=batch.batch_id,
                        ordinal=batch.ordinal,
                        plant_run_execution_id=(
                            batch.plant_run_execution_id
                        ),
                        generated_point_set_id=batch.generated_point_set_id,
                        asset_assignment_set_id=(
                            batch.asset_assignment_set_id
                        ),
                        selected_region_ids=batch.selected_region_ids,
                        selected_asset_ids=batch.selected_asset_ids,
                        strategy_id=planting_run.strategy_id,
                        strategy_version=planting_run.strategy_version,
                        point_count=point_set.point_count,
                        status=execution.status,
                        stage=execution.stage,
                        export_id=execution.export_id,
                        error_message=execution.error_message,
                        created_at=batch.created_at,
                    )
                )
            occupied = tuple(
                sorted(
                    {
                        region_id
                        for batch in summaries
                        for region_id in batch.selected_region_ids
                    }
                )
            )
            return PlantingCompositionSummary(
                composition_id=composition.composition_id,
                scene_revision_id=composition.scene_revision_id,
                region_set_id=composition.region_set_id,
                generation=composition.generation,
                occupied_region_ids=occupied,
                batches=tuple(summaries),
                updated_at=composition.updated_at,
            )

        return runtime.query(
            read,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def commit_plant_preview_batch(
        self,
        session_id: str,
        execution_id: str,
        request: PlantingBatchCommitRequest,
    ) -> PlantingCompositionSummary:
        runtime = self._project_runtime(session_id)
        selected_assets = normalize_asset_selection(request.selected_asset_ids)

        def commit(host: Any) -> None:
            input_request = host.get_plant_run_execution_request(execution_id)
            if request.replacing_batch_id is not None:
                batches = host.store.list_planting_batches()
                replaced = next(
                    (
                        item
                        for item in batches
                        if item.batch_id == request.replacing_batch_id
                    ),
                    None,
                )
                already_committed = any(
                    item.plant_run_execution_id == execution_id
                    for item in batches
                )
                if replaced is None and not already_committed:
                    raise ProjectResourceNotFoundError(
                        "Planting Batch", request.replacing_batch_id
                    )
                if replaced is not None:
                    replaced_execution = host.get_plant_run_execution(
                        replaced.plant_run_execution_id
                    )
                    if replaced_execution.status in {
                        PlantRunExecutionStatus.QUEUED,
                        PlantRunExecutionStatus.RUNNING,
                        PlantRunExecutionStatus.CANCEL_REQUESTED,
                    }:
                        raise SystemHostError(
                            "Wait for the batch output task before replacing it"
                        )
            host.store.commit_current_planting_batch(
                plant_run_execution_id=execution_id,
                selected_region_ids=input_request.selected_region_ids,
                selected_asset_ids=selected_assets,
                continue_planting=request.continue_planting,
                replacing_batch_id=request.replacing_batch_id,
            )

        runtime.call(
            commit,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        result = self.get_current_planting_composition(session_id)
        if result is None:
            raise SystemHostError("Planting composition was not committed")
        return result

    def discard_plant_preview(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantingCompositionSummary | None:
        """Discard the current uncommitted preview without touching saved batches."""
        runtime = self._project_runtime(session_id)

        def discard(host: Any) -> None:
            workflow = host.store.project_workflow_state()
            if workflow.plant_run_execution_id != execution_id:
                raise SystemHostError(
                    "Only the current Plant Preview can be discarded"
                )
            if any(
                batch.plant_run_execution_id == execution_id
                for batch in host.store.list_planting_batches()
            ):
                raise SystemHostError(
                    "A committed planting batch cannot be discarded as a preview"
                )
            execution = host.get_plant_run_execution(execution_id)
            if (
                execution.status
                is not PlantRunExecutionStatus.AWAITING_CONFIRMATION
                or execution.stage is not PlantRunExecutionStage.REVIEW
            ):
                raise SystemHostError(
                    "Only an uncommitted Plant Preview awaiting review can be discarded"
                )
            host.cancel_plant_run_execution(execution_id)
            host.store.discard_current_planting_preview(execution_id)

        runtime.call(
            discard,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return self.get_current_planting_composition(session_id)

    def remove_planting_batch(
        self,
        session_id: str,
        batch_id: str,
    ) -> PlantingCompositionSummary | None:
        runtime = self._project_runtime(session_id)

        def remove(host: Any) -> None:
            batch = next(
                (
                    item
                    for item in host.store.list_planting_batches()
                    if item.batch_id == batch_id
                ),
                None,
            )
            if batch is None:
                raise ProjectResourceNotFoundError(
                    "Planting Batch", batch_id
                )
            execution = host.get_plant_run_execution(
                batch.plant_run_execution_id
            )
            if execution.status in {
                PlantRunExecutionStatus.QUEUED,
                PlantRunExecutionStatus.RUNNING,
                PlantRunExecutionStatus.CANCEL_REQUESTED,
            }:
                raise SystemHostError(
                    "Wait for the batch output task before removing it"
                )
            host.store.remove_planting_batch(batch_id)

        runtime.call(
            remove,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return self.get_current_planting_composition(session_id)

    def submit_planting_composition_export(
        self,
        session_id: str,
        request: PlantingCompositionExportRequest,
    ) -> CompositionExportOperationInfo:
        runtime = self._project_runtime(session_id)
        offset = self._validated_plant_offset(request.offset)
        requested_batches = {item.batch_id: item for item in request.batches}
        if not requested_batches:
            raise SystemHostError("Select at least one planting batch")
        if len(requested_batches) != len(request.batches):
            raise SystemHostError("Planting batch identifiers must be unique")

        def submit(host: Any):
            composition = host.store.current_planting_composition()
            batches = host.store.list_planting_batches()
            if composition is None or not batches:
                raise SystemHostError(
                    "Project does not have committed planting batches"
                )
            known_batch_ids = {batch.batch_id for batch in batches}
            unknown_batch_ids = set(requested_batches) - known_batch_ids
            if unknown_batch_ids:
                raise SystemHostError(
                    "Composition export references unknown batches: "
                    + ", ".join(sorted(unknown_batch_ids))
                )
            scene = host.store.get_scene_revision(
                composition.scene_revision_id
            )
            inputs: list[CompositionOutputBatchRequest] = []
            target_type: str | None = None
            for batch in batches:
                requested = requested_batches.get(batch.batch_id)
                if requested is None:
                    continue
                assignment = host.store.get_asset_assignment_set(
                    batch.asset_assignment_set_id
                )
                if target_type is None:
                    target_type = assignment.target_type
                elif assignment.target_type != target_type:
                    raise SystemHostError(
                        "Composition batches use different output targets"
                    )
                selected_assets = normalize_asset_selection(
                    requested.selected_asset_ids
                )
                if selected_assets is None:
                    selected_assets = batch.selected_asset_ids
                elif batch.selected_asset_ids is not None:
                    unavailable = set(selected_assets) - set(
                        batch.selected_asset_ids
                    )
                    if unavailable:
                        raise SystemHostError(
                            "Composition export cannot re-enable assets excluded "
                            "when the batch was committed"
                        )
                inputs.append(
                    CompositionOutputBatchRequest(
                        batch_id=batch.batch_id,
                        selected_asset_ids=selected_assets,
                    )
                )
            route = resolve_plant_output_route(
                scene_importer_id=scene.importer_id,
                target_type=target_type or "",
            )
            operation = host.submit_composition_output_execution(
                CompositionOutputExecutionRequest(
                    composition_id=composition.composition_id,
                    composition_generation=composition.generation,
                    elevation_provider_id=route.elevation_provider_id,
                    export_provider_id=route.export_provider_id,
                    offset=offset,
                    batches=tuple(inputs),
                )
            )
            return operation

        operation = runtime.call(
            submit,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return CompositionExportOperationInfo(
            operation_id=operation.execution_id,
            composition_id=operation.composition_id,
            composition_generation=operation.composition_generation,
            stage=operation.stage.value,
            export_job_id=operation.export_job_id,
            status=operation.status,
            export_id=operation.export_id,
            error_message=operation.error_message,
        )

    def get_planting_composition_export(
        self,
        session_id: str,
        operation_id: str,
    ) -> CompositionExportOperationInfo:
        runtime = self._project_runtime(session_id)
        operation = runtime.query(
            lambda host: host.get_composition_output_execution(operation_id),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return CompositionExportOperationInfo(
            operation_id=operation.execution_id,
            composition_id=operation.composition_id,
            composition_generation=operation.composition_generation,
            stage=operation.stage.value,
            export_job_id=operation.export_job_id,
            status=operation.status,
            export_id=operation.export_id,
            error_message=operation.error_message,
        )

    def get_active_planting_composition_export(
        self,
        session_id: str,
    ) -> CompositionExportOperationInfo | None:
        runtime = self._project_runtime(session_id)
        operation = runtime.query(
            lambda host: host.get_active_composition_output_execution(),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        if operation is None:
            return None
        return CompositionExportOperationInfo(
            operation_id=operation.execution_id,
            composition_id=operation.composition_id,
            composition_generation=operation.composition_generation,
            stage=operation.stage.value,
            export_job_id=operation.export_job_id,
            status=operation.status,
            export_id=operation.export_id,
            error_message=operation.error_message,
        )

    def get_plant_preview_export(
        self,
        session_id: str,
        execution_id: str,
    ) -> ExportDownloadInfo:
        """Return the latest finalized output produced from this preview."""
        runtime = self._project_runtime(session_id)

        def latest_export_id(store: Any) -> str:
            execution = store.get_plant_run_execution(execution_id)
            if (
                execution.elevation_point_set_id is None
                or execution.asset_assignment_set_id is None
            ):
                raise SystemHostError(
                    "Plant preview does not have reusable output inputs"
                )
            record = store.get_latest_export_record(
                execution.elevation_point_set_id,
                execution.asset_assignment_set_id,
            )
            if record is not None:
                return record.export_id
            if execution.export_id is not None:
                return execution.export_id
            raise ProjectResourceNotFoundError(
                "Plant Preview Export",
                execution_id,
            )

        export_id = runtime.query(
            latest_export_id,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return self.get_export_download(session_id, export_id)

    def export_plant_preview(
        self,
        session_id: str,
        execution_id: str,
        request: PlantPreviewOutputRequest,
    ) -> PlantRunExecutionInfo:
        runtime = self._project_runtime(session_id)
        offset = self._validated_plant_offset(request.offset)

        def resolve_route(store: Any):
            execution = store.get_plant_run_execution(execution_id)
            if (
                execution.generated_point_set_id is None
                or execution.asset_assignment_set_id is None
            ):
                raise SystemHostError(
                    "Plant preview does not have reviewed point outputs"
                )
            point_set = store.get_point_set(
                execution.generated_point_set_id
            )
            planting_run = store.get_planting_run(
                point_set.planting_run_id
            )
            scene = store.get_scene_revision(
                planting_run.scene_revision_id
            )
            assignment = store.get_assignment_set(
                execution.asset_assignment_set_id
            )
            return resolve_plant_output_route(
                scene_importer_id=scene.importer_id,
                target_type=assignment.target_type,
            )

        route = runtime.query(
            resolve_route,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return self.confirm_plant_run_execution(
            session_id,
            execution_id,
            PlantRunContinuationRequest(
                elevation_provider_id=route.elevation_provider_id,
                export_provider_id=route.export_provider_id,
                offset=offset,
                selected_asset_ids=normalize_asset_selection(request.selected_asset_ids),
            ),
        )

    def submit_plant_preview_reexport(
        self,
        session_id: str,
        execution_id: str,
        request: PlantPreviewOutputRequest,
    ) -> PlantPreviewReexportInfo:
        """Generate another output from one completed preview without rerunning it."""
        runtime = self._project_runtime(session_id)
        offset = self._validated_plant_offset(request.offset)

        def submit(host: Any):
            execution = host.get_plant_run_execution(execution_id)
            if execution.status is not PlantRunExecutionStatus.SUCCEEDED:
                raise SystemHostError(
                    "Plant preview must complete before it can be re-exported"
                )
            if (
                execution.elevation_point_set_id is None
                or execution.asset_assignment_set_id is None
            ):
                raise SystemHostError(
                    "Plant preview does not have reusable output inputs"
                )
            point_set = host.store.get_point_set(
                execution.elevation_point_set_id
            )
            planting_run = host.store.get_planting_run(
                point_set.planting_run_id
            )
            scene = host.store.get_scene_revision(
                planting_run.scene_revision_id
            )
            assignment = host.store.get_asset_assignment_set(
                execution.asset_assignment_set_id
            )
            route = resolve_plant_output_route(
                scene_importer_id=scene.importer_id,
                target_type=assignment.target_type,
            )
            job = host.submit_output_export(
                OutputExportRequest(
                    point_set_id=execution.elevation_point_set_id,
                    asset_assignment_set_id=(
                        execution.asset_assignment_set_id
                    ),
                    provider_id=route.export_provider_id,
                    offset=offset,
                    selected_asset_ids=normalize_asset_selection(request.selected_asset_ids),
                )
            )
            export = (
                host.finalize_output_export(job.job_id)
                if job.status is JobStatus.SUCCEEDED
                else None
            )
            return job, export

        job, export = runtime.call(
            submit,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )
        return PlantPreviewReexportInfo(
            execution_id=execution_id,
            export_job_id=job.job_id,
            status=job.status,
            export_id=export.export_id if export is not None else None,
            error_message=(job.error.message if job.error is not None else None),
        )

    def get_plant_preview_reexport(
        self,
        session_id: str,
        execution_id: str,
        export_job_id: str,
    ) -> PlantPreviewReexportInfo:
        """Poll and finalize a preview re-export operation."""
        runtime = self._project_runtime(session_id)
        job = self.get_job(session_id, export_job_id)
        export = None
        if job.status is JobStatus.SUCCEEDED:
            export = runtime.call(
                lambda host: host.finalize_output_export(export_job_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
            execution = runtime.query(
                lambda store: store.get_plant_run_execution(execution_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
            if (
                export.point_set_id != execution.elevation_point_set_id
                or export.asset_assignment_set_id
                != execution.asset_assignment_set_id
            ):
                raise SystemHostError(
                    "Output export Job does not belong to this Plant preview"
                )
        return PlantPreviewReexportInfo(
            execution_id=execution_id,
            export_job_id=job.job_id,
            status=job.status,
            export_id=export.export_id if export is not None else None,
            error_message=(job.error.message if job.error is not None else None),
        )

    def submit_material_flow_execution(
        self,
        session_id: str,
        request: MaterialFlowExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> MaterialFlowExecutionInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_material_flow_execution(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def revise_current_materials(
        self,
        session_id: str,
        selection: MaterialSelectionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> MaterialFlowExecutionInfo:
        runtime = self._project_runtime(session_id)

        def revise(host):
            workflow = host.store.project_workflow_state()
            if workflow.source_id is None:
                raise SystemHostError("Project does not have a fixed source")
            if workflow.material_flow_execution_id is not None:
                current_material_flow = host.store.get_material_flow_execution(
                    workflow.material_flow_execution_id
                )
                if current_material_flow.status in {
                    MaterialFlowExecutionStatus.QUEUED,
                    MaterialFlowExecutionStatus.RUNNING,
                    MaterialFlowExecutionStatus.CANCEL_REQUESTED,
                }:
                    raise SystemHostError(
                        "Wait for the active material flow before revising materials"
                    )
            if workflow.plant_run_execution_id is not None:
                plant_run = host.store.get_plant_run_execution(
                    workflow.plant_run_execution_id
                )
                if plant_run.status in {
                    PlantRunExecutionStatus.QUEUED,
                    PlantRunExecutionStatus.RUNNING,
                    PlantRunExecutionStatus.CANCEL_REQUESTED,
                }:
                    raise SystemHostError(
                        "Cancel the active Plant Run before revising materials"
                    )
            material_flow_id = workflow.material_flow_execution_id
            if (
                material_flow_id is None
                and workflow.scene_preparation_execution_id is not None
            ):
                preparation = host.store.get_scene_preparation(
                    workflow.scene_preparation_execution_id
                )
                material_flow_id = preparation.material_flow_execution_id
            if material_flow_id is None:
                if workflow.region_set_id is None:
                    raise SystemHostError(
                        "Project does not have a reusable material extraction"
                    )
                region_set = host.store.get_region_set(workflow.region_set_id)
                scene_revision_id = region_set.scene_revision_id
                allow_contains = False
                import_settings: dict[str, Any] = {}
            else:
                template = host.get_material_flow_execution_request(
                    material_flow_id
                )
                scene_revision_id = template.scene_revision_id
                allow_contains = template.allow_contains
                import_settings = template.import_settings
            scene = host.store.get_scene_revision(scene_revision_id)
            region_provider_id = resolve_region_extraction_provider(
                scene_importer_id=scene.importer_id
            )
            execution = host.submit_material_flow_execution(
                MaterialFlowExecutionRequest(
                    scene_revision_id=scene_revision_id,
                    analysis_provider_id=None,
                    region_provider_id=region_provider_id,
                    confirmation_mode=MaterialConfirmationMode.MANUAL,
                    selection_mode=MaterialSelectionMode.MANUAL,
                    allow_contains=allow_contains,
                    import_settings=import_settings,
                    initial_selection=selection,
                ),
                idempotency_key=idempotency_key,
            )
            host.store.start_material_flow_workflow(execution)
            return execution

        return runtime.call(
            revise,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_current_material_selection(
        self,
        session_id: str,
    ) -> MaterialSelectionRequest | None:
        runtime = self._project_runtime(session_id)

        def read(store):
            _, preparation, material_flow, _ = (
                store.current_workflow_executions()
            )
            if (
                preparation is None
                or preparation.scene_revision_id is None
            ):
                return None
            if (
                material_flow is not None
                and material_flow.rule_pack_artifact_id is not None
            ):
                return load_material_selection(
                    store,
                    preparation.scene_revision_id,
                    material_flow.rule_pack_artifact_id,
                )
            workflow = store.project_workflow_state()
            if workflow.region_set_id is None:
                return None
            return load_region_material_selection(
                store,
                preparation.scene_revision_id,
                workflow.region_set_id,
            )

        try:
            return runtime.query(
                read,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ArtifactNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Material facts Artifact",
                str(error),
            ) from error

    def submit_scene_preparation(
        self,
        session_id: str,
        request: ScenePreparationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ScenePreparationInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.submit_current_scene_preparation(
                request,
                idempotency_key=idempotency_key,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_scene_preparation(
        self,
        session_id: str,
        execution_id: str,
    ) -> ScenePreparationInfo:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.get_scene_preparation(execution_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ScenePreparationNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "ScenePreparation",
                execution_id,
            ) from error

    def confirm_scene_preparation(
        self,
        session_id: str,
        execution_id: str,
        selection: MaterialSelectionRequest | None = None,
    ) -> ScenePreparationInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.confirm_scene_preparation(execution_id, selection),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_material_flow_execution(
        self,
        session_id: str,
        execution_id: str,
    ) -> MaterialFlowExecutionInfo:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.get_material_flow_execution(execution_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except MaterialFlowExecutionNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "MaterialFlowExecution",
                execution_id,
            ) from error

    def confirm_material_flow_execution(
        self,
        session_id: str,
        execution_id: str,
        selection: MaterialSelectionRequest | None = None,
    ) -> MaterialFlowExecutionInfo:
        runtime = self._project_runtime(session_id)
        return runtime.call(
            lambda host: host.confirm_material_flow_execution(
                execution_id,
                selection,
            ),
            timeout_seconds=self.config.project_call_timeout_seconds,
        )

    def get_material_facts(
        self,
        session_id: str,
        scene_revision_id: str,
    ) -> MaterialFactsView:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: load_material_facts(store, scene_revision_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except ArtifactNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "Material facts Artifact",
                scene_revision_id,
            ) from error

    def get_plant_run_execution(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        runtime = self._project_runtime(session_id)
        try:
            return runtime.query(
                lambda store: store.get_plant_run_execution(execution_id),
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except PlantRunExecutionNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "PlantRunExecution",
                execution_id,
            ) from error

    def request_job_cancel(
        self,
        session_id: str,
        job_id: str,
    ) -> JobCancellationInfo:
        runtime = self._project_runtime(session_id)
        try:
            job, accepted = runtime.request_cancel(
                job_id,
                timeout_seconds=self.config.project_call_timeout_seconds,
            )
        except JobNotFoundError as error:
            raise ProjectResourceNotFoundError("Job", job_id) from error
        return JobCancellationInfo(job=job, accepted=accepted)

    def request_plant_run_cancel(
        self,
        session_id: str,
        execution_id: str,
    ) -> PlantRunCancellationInfo:
        runtime = self._project_runtime(session_id)
        try:
            execution, accepted, worker_signal_sent = (
                runtime.request_plant_run_cancel(
                    execution_id,
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
            )
        except PlantRunExecutionNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "PlantRunExecution",
                execution_id,
            ) from error
        return PlantRunCancellationInfo(
            execution=execution,
            accepted=accepted,
            worker_signal_sent=worker_signal_sent,
        )

    def request_material_flow_cancel(
        self,
        session_id: str,
        execution_id: str,
    ) -> MaterialFlowCancellationInfo:
        runtime = self._project_runtime(session_id)
        try:
            execution, accepted, worker_signal_sent = (
                runtime.request_material_flow_cancel(
                    execution_id,
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
            )
        except MaterialFlowExecutionNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "MaterialFlowExecution",
                execution_id,
            ) from error
        return MaterialFlowCancellationInfo(
            execution=execution,
            accepted=accepted,
            worker_signal_sent=worker_signal_sent,
        )

    def request_scene_preparation_cancel(
        self,
        session_id: str,
        execution_id: str,
    ) -> ScenePreparationCancellationInfo:
        runtime = self._project_runtime(session_id)
        try:
            execution, accepted, worker_signal_sent = (
                runtime.request_scene_preparation_cancel(
                    execution_id,
                    timeout_seconds=self.config.project_call_timeout_seconds,
                )
            )
        except ScenePreparationNotFoundError as error:
            raise ProjectResourceNotFoundError(
                "ScenePreparation",
                execution_id,
            ) from error
        return ScenePreparationCancellationInfo(
            execution=execution,
            accepted=accepted,
            worker_signal_sent=worker_signal_sent,
        )

    def close_project(self, session_id: str) -> ProjectSessionInfo:
        with self._lifecycle_lock:
            self._require_running()
            with self._lock:
                runtime = self._projects.get(session_id)
            if runtime is None:
                raise ProjectSessionNotFoundError(session_id)
            runtime.close(self.config.project_call_timeout_seconds)
            closed = runtime.snapshot()
            with self._lock:
                self._projects.pop(session_id, None)
                self._sessions_by_path.pop(
                    self._path_key(Path(closed.project_path)), None
                )
            return closed

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._state is SystemHostState.STOPPED:
                    return
                self._state = SystemHostState.CLOSING
                projects = tuple(self._projects.values())
                dts_live_runtimes = tuple(self._dts_live_runtimes.values())
                library = self._library
                endpoint = self._endpoint
            failures: list[BaseException] = []
            try:
                self._starter_resources.close()
            except BaseException as error:
                failures.append(error)
            try:
                self._strategy_previews.close()
            except BaseException as error:
                failures.append(error)
            if endpoint is not None:
                try:
                    self._endpoint_store.clear(
                        instance_id=self._instance_id,
                        endpoint_id=endpoint.endpoint_id,
                    )
                except BaseException as error:
                    failures.append(error)
            for runtime in projects:
                try:
                    runtime.close(self.config.project_call_timeout_seconds)
                except BaseException as error:
                    failures.append(error)
            for runtime in dts_live_runtimes:
                try:
                    runtime.close(
                        self.config.project_call_timeout_seconds,
                        remove_files=True,
                    )
                except BaseException as error:
                    failures.append(error)
            if library is not None:
                try:
                    library.call(lambda store: self._asset_search.clear(), timeout_seconds=self.config.project_call_timeout_seconds)
                except BaseException as error:
                    failures.append(error)
                try:
                    library.close(self.config.project_call_timeout_seconds)
                except BaseException as error:
                    failures.append(error)
            self._dts_live_connector.close_all()
            with self._lock:
                self._projects.clear()
                self._dts_live_runtimes.clear()
                self._sessions_by_path.clear()
                self._library = None
                self._endpoint = None
                self._process_identity = None
                self._state = (
                    SystemHostState.FAILED if failures else SystemHostState.STOPPED
                )
            if failures:
                raise SystemHostError(
                    "SystemHost close failed: " + "; ".join(map(str, failures))
                )
            self._release_instance_guard()

    def _start_project(
        self,
        project_path: Path,
        *,
        create_name: str | None,
        enable_blender_worker: bool,
        enable_geospatial_worker: bool,
        blender_executable: Path | None,
        enable_model_api_worker: bool,
        workspace: ProjectWorkspace | None = None,
    ) -> ProjectSessionInfo:
        with self._lifecycle_lock:
            self._require_running()
            resolved = project_path.resolve()
            key = self._path_key(resolved)
            terminal_runtime: ProjectRuntime | None = None
            with self._lock:
                existing_id = self._sessions_by_path.get(key)
                if existing_id is not None:
                    existing_runtime = self._projects[existing_id]
                    existing = existing_runtime.snapshot()
                    if existing.state in {
                        ProjectSessionState.STARTING,
                        ProjectSessionState.ACTIVE,
                    }:
                        if workspace is not None:
                            self._project_registry.record_opened(
                                project_id=existing.project_id,
                                name=existing.name,
                                project_path=resolved,
                                opened_at=datetime.now(UTC).isoformat(),
                                workspace=workspace,
                            )
                        return existing
                    terminal_runtime = existing_runtime
                    self._projects.pop(existing_id, None)
                    self._sessions_by_path.pop(key, None)
            if terminal_runtime is not None:
                terminal_runtime.close(
                    self.config.project_call_timeout_seconds
                )
            with self._lock:
                selected_blender = blender_executable
                if enable_blender_worker and selected_blender is None:
                    blender_runtime = self._external_runtime_store.get(
                        BLENDER_RUNTIME_ID
                    )
                    if blender_runtime.available:
                        selected_blender = Path(
                            blender_runtime.resolved_path or ""
                        )
                extension_factories = ()
                if enable_geospatial_worker:
                    from verdflow.core_host import GeospatialExtensionFactory

                    extension_factories = (
                        GeospatialExtensionFactory(
                            runtime_root=(
                                resolved / "runtime" / "geospatial-worker"
                            ),
                            cache_root=(
                                resolved / "cache" / "geospatial-inputs"
                            ),
                        ),
                    )
                runtime = ProjectRuntime(
                    CoreHostConfig(
                        project_path=resolved,
                        enable_blender_worker=enable_blender_worker,
                        blender_executable=selected_blender,
                        enable_model_api_worker=enable_model_api_worker,
                    ),
                    library_path=self.config.library_path,
                    create_name=create_name,
                    idle_cycle_seconds=self.config.project_idle_cycle_seconds,
                    model_api_provider_registry=(
                        self._provider_store.registry()
                        if enable_model_api_worker
                        else None
                    ),
                    model_api_secret_resolver=(
                        self._secret_store
                        if enable_model_api_worker
                        else None
                    ),
                    extension_factories=extension_factories,
                )
                self._projects[runtime.session_id] = runtime
                self._sessions_by_path[key] = runtime.session_id
            try:
                info = runtime.start(self.config.project_start_timeout_seconds)
                self._project_registry.record_opened(
                    project_id=info.project_id,
                    name=info.name,
                    project_path=resolved,
                    opened_at=info.opened_at,
                    workspace=workspace,
                )
                return info
            except BaseException as error:
                close_error: BaseException | None = None
                try:
                    runtime.close(self.config.project_call_timeout_seconds)
                except BaseException as failure:
                    close_error = failure
                with self._lock:
                    self._projects.pop(runtime.session_id, None)
                    self._sessions_by_path.pop(key, None)
                try:
                    self._project_registry.record_failed_open(
                        project_path=resolved,
                        attempted_at=datetime.now(UTC).isoformat(),
                        error=str(error),
                        workspace=workspace,
                    )
                except BaseException:
                    pass
                if close_error is not None:
                    raise SystemHostError(
                        "Project startup failed and runtime cleanup also failed: "
                        f"{error}; {close_error}"
                    ) from error
                raise

    def _require_running(self) -> None:
        if self.state is not SystemHostState.RUNNING:
            raise SystemHostError("SystemHost is not running")

    def _library_runtime(self) -> LibraryRuntime:
        self._require_running()
        with self._lock:
            library = self._library
        if library is None:
            raise SystemHostError("User Library runtime is unavailable")
        return library

    def _project_runtime(self, session_id: str) -> ProjectRuntime:
        self._require_running()
        with self._lock:
            runtime = self._projects.get(session_id)
        if runtime is None:
            raise ProjectSessionNotFoundError(session_id)
        return runtime

    def _release_instance_guard(self) -> None:
        if not self._instance_guard_acquired:
            return
        self._instance_guard.release(
            self.config.project_call_timeout_seconds
        )
        self._instance_guard_acquired = False

    @staticmethod
    def _path_key(path: Path) -> str:
        return str(path.resolve()).casefold()

    @staticmethod
    def _plant_run_seed(
        value: int | None,
        idempotency_key: str | None,
    ) -> int:
        if value is not None:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("random_seed must be an integer")
            return value
        if idempotency_key is None:
            return secrets.randbits(63)
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)

    @staticmethod
    def _effective_binding_inputs(
        store: Any,
        strategy_id: str,
        strategy_version: str,
        target_type: str,
        catalog_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        strategies = [
            item
            for item in store.list_strategies()
            if item.package_id == strategy_id
            and item.package_version == strategy_version
        ]
        if not strategies:
            raise SystemHostError(
                f"Strategy version is not installed: {strategy_id}@{strategy_version}"
            )
        strategy = inspect_strategy_package(
            store.strategy_package_root(strategies[0].package_hash)
        ).strategy
        current = store.get_asset_catalog(target_type, catalog_id)
        if current is None:
            raise AssetCatalogNotFoundError(f"{target_type}/{catalog_id}")
        catalog_document = store.load_asset_catalog_document(
            target_type,
            catalog_id,
        )
        source = {
            "kind": "catalog",
            "catalog_id": current.catalog_id,
            "generation": current.generation,
            "semantic_hash": current.semantic_hash,
        }
        catalog = json.loads(catalog_document.canonical_json.decode("utf-8"))
        if not isinstance(catalog, dict):
            raise LibraryIntegrityError("Catalog canonical JSON must be an object")
        return strategy, catalog, source

    @staticmethod
    def _validated_plant_offset(
        value: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        if len(value) != 3 or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        ):
            raise ValueError("offset must contain three finite numbers")
        return tuple(float(item) for item in value)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _required_library_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @classmethod
    def _optional_library_text(
        cls,
        value: str | None,
        field: str,
    ) -> str | None:
        if value is None:
            return None
        return cls._required_library_text(value, field)

    @staticmethod
    def _json_object(payload: bytes) -> dict[str, Any]:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise LibraryIntegrityError(
                "Stored Catalog document must be a JSON object"
            )
        return dict(value)

    def _provider_info(self, provider: Any) -> ModelProviderInfo:
        descriptor = provider.descriptor
        capabilities = provider.capabilities
        return ModelProviderInfo(
            provider_id=descriptor.provider_id,
            display_name=descriptor.display_name,
            protocol=descriptor.protocol.value,
            provider_version=descriptor.provider_version,
            default_model=descriptor.default_model,
            endpoint=provider.endpoint,
            model_discovery_endpoint=provider.model_discovery_endpoint,
            model_discovery_supported=(
                provider.model_discovery_endpoint is not None
            ),
            builtin=provider.builtin,
            secret_configured=self._secret_store.contains(provider.secret_ref),
            context_window_tokens=capabilities.context_window_tokens,
            max_output_tokens=capabilities.max_output_tokens,
            default_max_output_tokens=capabilities.default_max_output_tokens,
            reasoning_mode=capabilities.reasoning_mode.value,
        )

    @staticmethod
    def _external_runtime_info(
        runtime: ExternalRuntimeResolution,
    ) -> ExternalRuntimeInfo:
        return ExternalRuntimeInfo(
            runtime_id=runtime.runtime_id,
            display_name=runtime.display_name,
            path_kind=runtime.path_kind,
            configured_path=runtime.configured_path,
            resolved_path=runtime.resolved_path,
            resolution_source=runtime.resolution_source,
            available=runtime.available,
            detail_code=runtime.detail_code,
            required_names=runtime.required_names,
        )

    def _set_strategy_trust(
        self,
        package_hash: str,
        *,
        trusted: bool,
        reason: str | None,
    ) -> StrategyLibrarySummary:
        self._require_running()
        library = self._library_runtime()

        def decide(store: Any) -> StrategyLibrarySummary:
            info = store.get_strategy_by_hash(package_hash)
            if info is None:
                raise LibraryResourceNotFoundError(
                    "strategy",
                    package_hash,
                )
            operation = (
                store.trust_strategy if trusted else store.revoke_strategy
            )
            operation(
                package_hash,
                decided_by="studio",
                reason=reason,
            )
            return strategy_summary(store, info)

        return library.call(
            decide,
            timeout_seconds=self.config.project_call_timeout_seconds,
        )


def _thumbnail_media_type(path: Path) -> str:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Thumbnail exceeds the 16 MiB limit")
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Thumbnail must be a PNG, JPEG, or WebP image")
