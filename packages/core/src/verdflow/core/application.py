"""Transport-neutral VerdFlow application use-case facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    CompositionExportRecordInfo,
    ExportRecordInfo,
    JobAttemptInfo,
    JobCommandRef,
    JobEvent,
    JobInfo,
    JobStatus,
    MaterialClassificationCandidate,
    MaterialFactsView,
    MaterialSelectionMode,
    MaterialSelectionRequest,
    PointSetInfo,
    ProjectInfo,
    RegionSetInfo,
    SceneRevisionInfo,
    SourceAssemblyMemberInfo,
    SourceAssemblyMemberSpec,
    SourceInfo,
    SourceKind,
)
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .material_selection import MaterialSelectionService
from .point_import import PointSetImportService
from .region_extraction_import import RegionExtractionImportService
from .region_source_import import RegionSourceImportService
from .scene_import import SceneImportService
from .strategy_access import StrategyExecutionAuthorizer

if TYPE_CHECKING:
    from .asset_assignment import (
        AssetAssignmentRequest,
        AssetAssignmentService,
    )
    from .elevation_sampling import (
        ElevationProviderDefinition,
        ElevationSamplingRequest,
        ElevationSamplingService,
    )
    from .exporting import (
        CompositionOutputExportRequest,
        ExportProviderDefinition,
        OutputExportRequest,
        OutputExportService,
    )
    from .planting_generation import (
        PlantingGenerationRequest,
        PlantingGenerationService,
    )
    from .material_analysis import (
        MaterialAnalysisRequest,
        MaterialAnalysisService,
        ModelAnalysisProviderDefinition,
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


@dataclass(frozen=True, slots=True)
class CommandDocument:
    """Immutable bytes submitted as a versioned Job command Artifact."""

    command_type: str
    command_version: str
    content: bytes
    media_type: str = "application/vnd.verdflow.command+json"
    schema_id: str | None = None
    schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    kind: SourceKind
    original_name: str
    artifact_id: str


class JobCancellationPort(Protocol):
    """Notify an active worker after cancellation is persisted."""

    def request_cancel(self, job: JobInfo) -> bool: ...


class ApplicationFacade:
    """Stable use cases shared by CLI, services and future bridges."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        *,
        cancellation_port: JobCancellationPort | None = None,
        strategy_authorizer: StrategyExecutionAuthorizer | None = None,
        region_extraction_providers: tuple[
            RegionExtractionProviderDefinition, ...
        ] = (),
        region_source_providers: tuple[
            RegionSourceProviderDefinition, ...
        ] = (),
        elevation_providers: tuple[ElevationProviderDefinition, ...] = (),
        asset_assignment_service: AssetAssignmentService | None = None,
        export_providers: tuple[ExportProviderDefinition, ...] = (),
        model_analysis_providers: tuple[
            ModelAnalysisProviderDefinition, ...
        ] = (),
    ) -> None:
        if runtime.store is not store:
            raise ValueError("ApplicationFacade runtime must use the same store")
        self.store = store
        self.runtime = runtime
        self.cancellation_port = cancellation_port
        self._scene_import = SceneImportService(store)
        self._material_selection = MaterialSelectionService(store, runtime)
        self._region_extraction_import = RegionExtractionImportService(store)
        self._region_source_import = RegionSourceImportService(store)
        self._region_extraction: RegionExtractionService | None = None
        if region_extraction_providers:
            from .region_extraction import RegionExtractionService

            self._region_extraction = RegionExtractionService(
                store,
                runtime,
                region_extraction_providers,
            )
        self._region_source: RegionSourceService | None = None
        if region_source_providers:
            from .region_source import RegionSourceService

            self._region_source = RegionSourceService(
                store,
                runtime,
                region_source_providers,
            )
        self._material_analysis: MaterialAnalysisService | None = None
        if model_analysis_providers:
            from .material_analysis import MaterialAnalysisService

            self._material_analysis = MaterialAnalysisService(
                store,
                runtime,
                model_analysis_providers,
            )
        self._point_import = PointSetImportService(store)
        self._asset_assignment = asset_assignment_service
        self._output_export: OutputExportService | None = None
        if export_providers:
            from .exporting import OutputExportService

            self._output_export = OutputExportService(
                store,
                runtime,
                export_providers,
            )
        self._planting_generation: PlantingGenerationService | None = None
        if strategy_authorizer is not None:
            from .planting_generation import PlantingGenerationService

            self._planting_generation = PlantingGenerationService(
                store,
                runtime,
                strategy_authorizer,
            )
        self._elevation_sampling: ElevationSamplingService | None = None
        if elevation_providers:
            from .elevation_sampling import ElevationSamplingService

            self._elevation_sampling = ElevationSamplingService(
                store,
                runtime,
                elevation_providers,
            )

    def freeze_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        return self._asset_assignment.freeze_profile(profile_id)

    def freeze_role_assignments(
        self, profile_id: str | None, overrides: dict[str, Any],
    ) -> dict[str, Any]:
        if self._asset_assignment is None:
            raise RuntimeError("Asset assignment service is not configured")
        return self._asset_assignment.freeze_role_assignments(profile_id, overrides)

    def project_info(self) -> ProjectInfo:
        return self.store.project_info()

    def freeze_generation_args(self, profile_id: str | None, args: dict[str, Any]) -> dict[str, Any]:
        if self._asset_assignment is None:
            if profile_id is not None:
                raise RuntimeError("Asset assignment service is not configured")
            return dict(args)
        return self._asset_assignment.freeze_generation_args(profile_id, args)

    def register_source(self, request: SourceRegistration) -> SourceInfo:
        self._require_text(request.original_name, "source original_name")
        self._require_text(request.artifact_id, "source artifact_id")
        return self.store.register_source(
            kind=request.kind,
            original_name=request.original_name,
            artifact_id=request.artifact_id,
        )

    def get_source(self, source_id: str) -> SourceInfo:
        self._require_text(source_id, "source_id")
        return self.store.get_source(source_id)

    def create_source_assembly(
        self,
        *,
        original_name: str,
        members: tuple[SourceAssemblyMemberSpec, ...],
    ) -> SourceInfo:
        self._require_text(original_name, "assembly original_name")
        if not members:
            raise ValueError("assembly members cannot be empty")
        return self.store.create_source_assembly(
            original_name=original_name,
            members=members,
        )

    def source_assembly_members(
        self,
        source_id: str,
    ) -> tuple[SourceAssemblyMemberInfo, ...]:
        self._require_text(source_id, "source_id")
        return self.store.list_source_assembly_members(source_id)

    def submit_command(
        self,
        document: CommandDocument,
        *,
        idempotency_key: str,
        max_attempts: int = 1,
    ) -> JobInfo:
        self._require_text(document.command_type, "command_type")
        self._require_text(document.command_version, "command_version")
        self._require_text(document.media_type, "command media_type")
        if not document.content:
            raise ValueError("command content cannot be empty")
        artifact = self.store.put_bytes(
            document.content,
            media_type=document.media_type,
            schema_id=document.schema_id,
            schema_version=document.schema_version,
        )
        return self.runtime.submit(
            JobCommandRef(
                command_type=document.command_type,
                command_version=document.command_version,
                artifact_id=artifact.artifact_id,
            ),
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )

    def get_job(self, job_id: str) -> JobInfo:
        return self.runtime.get(job_id)

    def job_events(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobEvent, ...]:
        return self.runtime.events(
            job_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def job_attempts(self, job_id: str) -> tuple[JobAttemptInfo, ...]:
        return self.runtime.attempts(job_id)

    def cancel_job(self, job_id: str) -> JobInfo:
        job = self.runtime.request_cancel(job_id)
        if (
            job.status is JobStatus.CANCELLING
            and self.cancellation_port is not None
        ):
            self.cancellation_port.request_cancel(job)
        return job

    def finalize_scene_inspection(self, job_id: str) -> SceneRevisionInfo:
        return self._scene_import.finalize(job_id)

    def get_material_facts(self, scene_revision_id: str) -> MaterialFactsView:
        return self._material_selection.facts(scene_revision_id)

    def finalize_material_selection(
        self,
        scene_revision_id: str,
        request: MaterialSelectionRequest,
        *,
        selection_mode: MaterialSelectionMode,
        candidate_job_id: str | None = None,
    ):
        return self._material_selection.finalize(
            scene_revision_id,
            request,
            selection_mode=selection_mode,
            candidate_job_id=candidate_job_id,
        )

    def finalize_region_extraction(self, job_id: str) -> RegionSetInfo:
        return self._region_extraction_import.finalize(job_id)

    def submit_region_extraction(
        self,
        request: RegionExtractionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._region_extraction is None:
            raise RuntimeError("Region extraction is not configured")
        return self._region_extraction.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def submit_region_source(
        self,
        request: RegionSourceRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._region_source is None:
            raise RuntimeError("Region source is not configured")
        return self._region_source.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def finalize_region_source(
        self,
        job_id: str,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        return self._region_source_import.finalize(job_id)

    def submit_material_analysis(
        self,
        request: MaterialAnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._material_analysis is None:
            raise RuntimeError("Material analysis is not configured")
        return self._material_analysis.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def load_material_candidate(
        self,
        job_id: str,
    ) -> MaterialClassificationCandidate:
        if self._material_analysis is None:
            raise RuntimeError("Material analysis is not configured")
        return self._material_analysis.load_candidate(job_id)

    def finalize_material_candidate(
        self,
        job_id: str,
        *,
        user_confirmed: bool = False,
    ):
        if self._material_analysis is None:
            raise RuntimeError("Material analysis is not configured")
        return self._material_analysis.finalize_candidate(
            job_id,
            user_confirmed=user_confirmed,
        )

    def finalize_generated_points(self, job_id: str) -> PointSetInfo:
        return self._point_import.finalize_generated(job_id)

    def submit_planting_generation(
        self,
        request: PlantingGenerationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._planting_generation is None:
            raise RuntimeError("Strategy execution is not configured")
        return self._planting_generation.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def finalize_elevation(self, job_id: str) -> PointSetInfo:
        return self._point_import.finalize_elevation(job_id)

    def submit_elevation_sampling(
        self,
        request: ElevationSamplingRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._elevation_sampling is None:
            raise RuntimeError("Elevation sampling is not configured")
        return self._elevation_sampling.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def submit_asset_assignment(
        self,
        request: AssetAssignmentRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._asset_assignment is None:
            raise RuntimeError("Asset assignment is not configured")
        return self._asset_assignment.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def finalize_asset_assignment(
        self,
        job_id: str,
    ) -> AssetAssignmentSetInfo:
        if self._asset_assignment is None:
            raise RuntimeError("Asset assignment is not configured")
        return self._asset_assignment.finalize(job_id)

    def submit_output_export(
        self,
        request: OutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._output_export is None:
            raise RuntimeError("Output export is not configured")
        return self._output_export.submit(
            request,
            idempotency_key=idempotency_key,
        )

    def submit_composition_output_export(
        self,
        request: CompositionOutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if self._output_export is None:
            raise RuntimeError("Output export is not configured")
        return self._output_export.submit_composition(
            request,
            idempotency_key=idempotency_key,
        )

    def finalize_output_export(
        self, job_id: str
    ) -> ExportRecordInfo | CompositionExportRecordInfo:
        if self._output_export is None:
            raise RuntimeError("Output export is not configured")
        return self._output_export.finalize(job_id)

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
