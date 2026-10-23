"""Application orchestration from material analysis to Region extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any, Protocol

from verdflow.contracts import (
    JobInfo,
    JobStatus,
    MaterialClassificationCandidate,
    MaterialConfirmationMode,
    RegionSetInfo,
)
from verdflow.core import (
    MaterialAnalysisRequest,
    MaterialConfirmationResult,
    RegionExtractionRequest,
)

if TYPE_CHECKING:
    from verdflow.core import ApplicationFacade, ServiceLoop


class MaterialFlowError(RuntimeError):
    """The explicit analysis-to-extraction flow could not complete."""


@dataclass(frozen=True, slots=True)
class MaterialFlowRequest:
    analysis: MaterialAnalysisRequest
    region_provider_id: str | None = None
    allow_contains: bool = False
    import_settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaterialFlowOutcome:
    analysis_job: JobInfo
    candidate: MaterialClassificationCandidate
    confirmation: MaterialConfirmationResult | None = None
    extraction_job: JobInfo | None = None
    region_set: RegionSetInfo | None = None


@dataclass(frozen=True, slots=True)
class MaterialExtractionSubmission:
    """Result of confirming a candidate and queuing Region extraction."""

    analysis_job: JobInfo
    candidate: MaterialClassificationCandidate
    confirmation: MaterialConfirmationResult
    extraction_job: JobInfo


class MaterialFlowHostPort(Protocol):
    application: "ApplicationFacade"
    service_loop: "ServiceLoop"

    def start(self) -> None: ...


class MaterialFlowCoordinator:
    """Chain existing use cases without introducing a generic workflow engine."""

    def __init__(self, host: MaterialFlowHostPort) -> None:
        self.host = host

    def execute(
        self,
        request: MaterialFlowRequest,
        *,
        analysis_timeout_seconds: float = 300.0,
        extraction_timeout_seconds: float = 1800.0,
    ) -> MaterialFlowOutcome:
        self.host.start()
        analysis_job = self.host.application.submit_material_analysis(request.analysis)
        completed = self._wait(
            analysis_job,
            timeout_seconds=analysis_timeout_seconds,
            stage="Material analysis",
        )
        candidate = self.host.application.load_material_candidate(completed.job_id)
        if request.analysis.confirmation_mode is MaterialConfirmationMode.MANUAL:
            return MaterialFlowOutcome(completed, candidate)
        if request.region_provider_id is None:
            raise MaterialFlowError(
                "Automatic material confirmation requires a Region extraction provider"
            )
        return self._confirm_and_extract(
            completed,
            candidate,
            region_provider_id=request.region_provider_id,
            user_confirmed=False,
            allow_contains=request.allow_contains,
            import_settings=request.import_settings,
            timeout_seconds=extraction_timeout_seconds,
        )

    def confirm_and_extract(
        self,
        analysis_job_id: str,
        *,
        region_provider_id: str,
        allow_contains: bool = False,
        import_settings: dict[str, Any] | None = None,
        timeout_seconds: float = 1800.0,
    ) -> MaterialFlowOutcome:
        submission = self.confirm_and_submit(
            analysis_job_id,
            region_provider_id=region_provider_id,
            allow_contains=allow_contains,
            import_settings=import_settings or {},
        )
        completed = self._wait(
            submission.extraction_job,
            timeout_seconds=timeout_seconds,
            stage="Region extraction",
        )
        region_set = self.host.application.finalize_region_extraction(
            completed.job_id
        )
        return MaterialFlowOutcome(
            analysis_job=submission.analysis_job,
            candidate=submission.candidate,
            confirmation=submission.confirmation,
            extraction_job=completed,
            region_set=region_set,
        )

    def confirm_and_submit(
        self,
        analysis_job_id: str,
        *,
        region_provider_id: str,
        user_confirmed: bool = True,
        allow_contains: bool = False,
        import_settings: dict[str, Any] | None = None,
    ) -> MaterialExtractionSubmission:
        """Confirm a candidate and queue extraction without waiting for Blender."""
        self.host.start()
        job = self.host.application.get_job(analysis_job_id)
        candidate = self.host.application.load_material_candidate(analysis_job_id)
        confirmation = self.host.application.finalize_material_candidate(
            analysis_job_id,
            user_confirmed=user_confirmed,
        )
        scene_revision_id = self._analysis_scene_revision(job)
        extraction = self.host.application.submit_region_extraction(
            RegionExtractionRequest(
                scene_revision_id=scene_revision_id,
                provider_id=region_provider_id,
                rule_pack=confirmation.rule_pack,
                allow_contains=allow_contains,
                import_settings=import_settings or {},
            ),
            idempotency_key=(
                f"material-region-extract:{job.job_id}:"
                f"{confirmation.rule_pack_artifact_id}:{region_provider_id}"
            ),
        )
        return MaterialExtractionSubmission(
            analysis_job=job,
            candidate=candidate,
            confirmation=confirmation,
            extraction_job=extraction,
        )

    def _confirm_and_extract(
        self,
        analysis_job: JobInfo,
        candidate: MaterialClassificationCandidate,
        *,
        region_provider_id: str,
        user_confirmed: bool,
        allow_contains: bool,
        import_settings: dict[str, Any],
        timeout_seconds: float,
    ) -> MaterialFlowOutcome:
        submission = self.confirm_and_submit(
            analysis_job.job_id,
            region_provider_id=region_provider_id,
            user_confirmed=user_confirmed,
            allow_contains=allow_contains,
            import_settings=import_settings,
        )
        completed = self._wait(
            submission.extraction_job,
            timeout_seconds=timeout_seconds,
            stage="Region extraction",
        )
        region_set = self.host.application.finalize_region_extraction(completed.job_id)
        return MaterialFlowOutcome(
            analysis_job=analysis_job,
            candidate=candidate,
            confirmation=submission.confirmation,
            extraction_job=completed,
            region_set=region_set,
        )

    def _wait(self, job: JobInfo, *, timeout_seconds: float, stage: str) -> JobInfo:
        completed = self.host.service_loop.run_until_terminal(
            job.job_id,
            timeout_seconds=timeout_seconds,
        )
        if completed.status is not JobStatus.SUCCEEDED:
            message = (
                completed.error.message
                if completed.error is not None
                else completed.status.value
            )
            raise MaterialFlowError(f"{stage} failed: {message}")
        return completed

    def _analysis_scene_revision(self, job: JobInfo) -> str:
        with self.host.application.store.open_artifact(
            job.command.artifact_id
        ) as stream:
            command = json.load(stream)
        value = command.get("scene_revision_id") if isinstance(command, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise MaterialFlowError("Analysis command scene_revision_id is missing")
        return value.strip()
