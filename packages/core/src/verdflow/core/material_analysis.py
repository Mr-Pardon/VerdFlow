"""Submit and validate provider-neutral material analysis candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from verdflow.contracts import (
    MATERIAL_CLASSIFICATION_CANDIDATE_SCHEMA,
    MATERIAL_CLASSIFICATION_CANDIDATE_VERSION,
    MATERIAL_CLASSIFICATION_PROMPT_POLICY_VERSION,
    MATERIAL_CLASSIFICATION_TASK,
    MODEL_ANALYSIS_COMMAND_SCHEMA,
    MODEL_ANALYSIS_COMMAND_VERSION,
    MODEL_ANALYSIS_RESULT_SCHEMA,
    MODEL_ANALYSIS_RESULT_VERSION,
    InferenceProtocol,
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    JobStatus,
    MaterialClassificationCandidate,
    MaterialClassification,
    MaterialConfirmationMode,
    material_classification_candidate_from_document,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime


class MaterialAnalysisError(RuntimeError):
    """A completed model result cannot become a trusted candidate view."""


class MaterialConfirmationRequired(MaterialAnalysisError):
    """Manual policy requires an explicit user confirmation."""


@dataclass(frozen=True, slots=True)
class MaterialConfirmationResult:
    candidate_job_id: str
    confirmation_mode: MaterialConfirmationMode
    rule_pack_artifact_id: str
    rule_pack: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelAnalysisProviderDefinition:
    provider_id: str
    provider_version: str
    protocol: InferenceProtocol
    default_model: str | None
    command_type: str
    command_version: str
    max_attempts: int = 1

    def validate(self) -> None:
        for field_name in (
            "provider_id",
            "provider_version",
            "command_type",
            "command_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            if value != value.strip():
                raise ValueError(f"{field_name} cannot contain outer whitespace")
        if self.default_model is not None:
            if not isinstance(self.default_model, str) or not self.default_model.strip():
                raise ValueError("default_model must be text or null")
            if self.default_model != self.default_model.strip():
                raise ValueError("default_model cannot contain outer whitespace")
        if not isinstance(self.protocol, InferenceProtocol):
            raise ValueError("protocol is invalid")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True, slots=True)
class MaterialAnalysisRequest:
    scene_revision_id: str
    provider_id: str
    model: str | None = None
    confirmation_mode: MaterialConfirmationMode = MaterialConfirmationMode.MANUAL


class MaterialAnalysisService:
    """Freeze MaterialFacts references and validate provider candidate output."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        providers: tuple[ModelAnalysisProviderDefinition, ...],
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._providers: dict[str, ModelAnalysisProviderDefinition] = {}
        for provider in providers:
            provider.validate()
            if provider.provider_id in self._providers:
                raise ValueError(
                    f"Duplicate model analysis provider: {provider.provider_id}"
                )
            self._providers[provider.provider_id] = provider
        if not self._providers:
            raise ValueError("At least one model analysis provider is required")

    def submit(
        self,
        request: MaterialAnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        scene_revision_id = _required_text(
            request.scene_revision_id,
            "scene_revision_id",
        )
        provider_id = _required_text(request.provider_id, "provider_id")
        try:
            provider = self._providers[provider_id]
        except KeyError as error:
            raise ValueError(
                f"Model analysis provider is not registered: {provider_id}"
            ) from error
        model = _required_text(
            request.model or provider.default_model,
            "model",
        )
        if not isinstance(request.confirmation_mode, MaterialConfirmationMode):
            raise ValueError("confirmation_mode is invalid")
        scene = self._store.get_scene_revision(scene_revision_id)
        scene_manifest = self._read_json(
            scene.scene_manifest_artifact_id,
            "Scene manifest",
        )
        facts = scene_manifest.get("facts")
        if not isinstance(facts, dict):
            raise ValueError("Scene manifest facts are required")
        material_facts_artifact_id = _required_text(
            facts.get("material_fact_report_artifact_id"),
            "material_fact_report_artifact_id",
        )
        self._store.get_artifact(material_facts_artifact_id)
        task_identity = {
            "task_type": MATERIAL_CLASSIFICATION_TASK,
            "scene_revision_id": scene.scene_revision_id,
            "input_artifact_id": material_facts_artifact_id,
            "provider_id": provider.provider_id,
            "provider_version": provider.provider_version,
            "protocol": provider.protocol.value,
            "model": model,
            "prompt_policy_version": (
                MATERIAL_CLASSIFICATION_PROMPT_POLICY_VERSION
            ),
            "confirmation_mode": request.confirmation_mode.value,
        }
        digest = semantic_hash(task_identity).removeprefix("sha256:")
        task_id = f"analysis_{digest[:24]}"
        command = {
            "schema": MODEL_ANALYSIS_COMMAND_SCHEMA,
            "version": MODEL_ANALYSIS_COMMAND_VERSION,
            "operation": "classify_materials",
            "task_id": task_id,
            **task_identity,
            "result_schema": {
                "id": MATERIAL_CLASSIFICATION_CANDIDATE_SCHEMA,
                "version": MATERIAL_CLASSIFICATION_CANDIDATE_VERSION,
            },
        }
        command_artifact = self._store.put_bytes(
            canonical_json_bytes(command),
            media_type="application/vnd.verdflow.command+json",
            schema_id=MODEL_ANALYSIS_COMMAND_SCHEMA,
            schema_version=MODEL_ANALYSIS_COMMAND_VERSION,
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=provider.command_type,
                command_version=provider.command_version,
                artifact_id=command_artifact.artifact_id,
            ),
            idempotency_key=idempotency_key or f"material-analysis:{digest}",
            max_attempts=provider.max_attempts,
            retention_class=JobRetentionClass.CANDIDATE,
        )

    def load_candidate(self, job_id: str) -> MaterialClassificationCandidate:
        job = self._store.get_job(_required_text(job_id, "job_id"))
        if job.status is not JobStatus.SUCCEEDED:
            raise MaterialAnalysisError("Material analysis Job has not succeeded")
        if job.result_artifact_id is None:
            raise MaterialAnalysisError("Material analysis Job has no result")
        command = self._read_json(job.command.artifact_id, "Analysis command")
        result = self._read_json(job.result_artifact_id, "Analysis result")
        if result.get("schema") != MODEL_ANALYSIS_RESULT_SCHEMA:
            raise MaterialAnalysisError("Material analysis result schema is unsupported")
        if str(result.get("version")) != MODEL_ANALYSIS_RESULT_VERSION:
            raise MaterialAnalysisError("Material analysis result version is unsupported")
        if result.get("result_kind") != "material_classification_candidate":
            raise MaterialAnalysisError("Material analysis result kind is unsupported")
        candidate_document = result.get("candidate")
        if not isinstance(candidate_document, dict):
            raise MaterialAnalysisError(
                "Material analysis result candidate must be an object"
            )
        try:
            candidate = material_classification_candidate_from_document(
                candidate_document
            )
        except (TypeError, ValueError) as error:
            raise MaterialAnalysisError(str(error)) from error
        self._validate_lineage(command, candidate)
        self._validate_material_closure(command, candidate)
        return candidate

    def finalize_candidate(
        self,
        job_id: str,
        *,
        user_confirmed: bool = False,
    ) -> MaterialConfirmationResult:
        if not isinstance(user_confirmed, bool):
            raise ValueError("user_confirmed must be a boolean")
        candidate = self.load_candidate(job_id)
        job = self._store.get_job(job_id)
        command = self._read_json(job.command.artifact_id, "Analysis command")
        try:
            mode = MaterialConfirmationMode(
                _required_result_text(command, "confirmation_mode")
            )
        except ValueError as error:
            raise MaterialAnalysisError("confirmation_mode is invalid") from error
        if mode is MaterialConfirmationMode.MANUAL and not user_confirmed:
            raise MaterialConfirmationRequired(
                "Material candidate requires explicit user confirmation"
            )
        rule_pack = _material_rule_pack(
            candidate,
            candidate_job_id=job_id,
            confirmation_mode=mode,
        )
        artifact = self._store.put_bytes(
            canonical_json_bytes(rule_pack),
            media_type="application/vnd.verdflow.rule-pack+json",
            schema_id="verdflow.region-rule-pack",
            schema_version="1",
        )
        self._runtime.promote(job_id, retention_class=JobRetentionClass.AUDIT)
        return MaterialConfirmationResult(
            candidate_job_id=job_id,
            confirmation_mode=mode,
            rule_pack_artifact_id=artifact.artifact_id,
            rule_pack=rule_pack,
        )

    def _validate_lineage(
        self,
        command: dict[str, Any],
        candidate: MaterialClassificationCandidate,
    ) -> None:
        expected = {
            "task_id": candidate.task_id,
            "task_type": candidate.task_type,
            "input_artifact_id": candidate.input_ref,
            "provider_id": candidate.invocation.provider_id,
            "provider_version": candidate.invocation.provider_version,
            "protocol": candidate.invocation.protocol.value,
            "model": candidate.invocation.model,
            "prompt_policy_version": candidate.invocation.prompt_policy_version,
        }
        mismatches = [
            field for field, value in expected.items() if command.get(field) != value
        ]
        if mismatches:
            raise MaterialAnalysisError(
                "Material candidate lineage does not match command: "
                + ", ".join(mismatches)
            )

    def _validate_material_closure(
        self,
        command: dict[str, Any],
        candidate: MaterialClassificationCandidate,
    ) -> None:
        facts = self._read_json(
            _required_result_text(command, "input_artifact_id"),
            "Material facts",
        )
        rows = facts.get("materials")
        if not isinstance(rows, list):
            raise MaterialAnalysisError("Material facts materials are required")
        names: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise MaterialAnalysisError("Material facts item is invalid")
            names.append(_required_result_text(row, "name"))
        candidate_names = [item.material_name for item in candidate.materials]
        if candidate_names != names:
            raise MaterialAnalysisError(
                "Material candidate does not preserve the exact input material order"
            )

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        with self._store.open_artifact(artifact_id) as stream:
            try:
                document = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MaterialAnalysisError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(document, dict):
            raise MaterialAnalysisError(f"{label} must be a JSON object")
        return document


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _required_result_text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MaterialAnalysisError(f"{field} is required")
    return value.strip()


def _material_rule_pack(
    candidate: MaterialClassificationCandidate,
    *,
    candidate_job_id: str,
    confirmation_mode: MaterialConfirmationMode,
) -> dict[str, Any]:
    groups = (
        (
            MaterialClassification.STREET_TREE_GREEN,
            "model-street-tree-green",
            "Street tree greenbelt",
            "street_greenbelt",
        ),
        (
            MaterialClassification.GENERAL_GREEN,
            "model-general-green",
            "General greenbelt",
            "general_greenbelt",
        ),
    )
    rules: list[dict[str, Any]] = []
    for classification, rule_id, label, greenbelt_type in groups:
        items = [
            item for item in candidate.materials
            if item.classification is classification
        ]
        if not items:
            continue
        minimum_confidence = min(item.confidence for item in items)
        confidence_label = (
            "high" if minimum_confidence >= 0.9
            else "medium" if minimum_confidence >= 0.7
            else "low"
        )
        rules.append(
            {
                "id": rule_id,
                "label": label,
                "name_prefix": label.replace(" ", "_"),
                "tags": f"green,plantable,{greenbelt_type}",
                "confidence": confidence_label,
                "needs_review": False,
                "greenbelt_type": greenbelt_type,
                "classification_source": "model_material_classification",
                "classification_confidence_min": minimum_confidence,
                "match": {
                    "material_names": [item.material_name for item in items],
                },
            }
        )
    if not rules:
        raise MaterialAnalysisError(
            "Material candidate contains no plantable green materials"
        )
    return {
        "schema": "verdflow.region-rule-pack",
        "version": "1",
        "origin": {
            "candidate_job_id": candidate_job_id,
            "task_id": candidate.task_id,
            "confirmation_mode": confirmation_mode.value,
        },
        "rules": rules,
    }
