"""Submit and finalize provider-neutral output export Jobs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    CompositionExportInputInfo,
    CompositionExportRecordInfo,
    ExportRecordInfo,
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    JobStatus,
    OUTPUT_EXPORT_COMMAND_SCHEMA,
    OUTPUT_EXPORT_COMMAND_VERSION,
    PointSetInfo,
    PointSetKind,
)
from verdflow.planting import canonical_json_bytes, semantic_hash, normalize_asset_selection
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime


class OutputExportError(RuntimeError):
    """Raised when an output Job cannot become immutable project evidence."""


@dataclass(frozen=True, slots=True)
class ExportProviderDefinition:
    provider_id: str
    provider_version: str
    target_type: str
    command_type: str
    command_version: str
    output_media_type: str
    output_schema_id: str
    output_schema_version: str
    max_attempts: int = 1
    supports_asset_selection: bool = False
    supports_terrain_hit_selection: bool = False


@dataclass(frozen=True, slots=True)
class OutputExportRequest:
    point_set_id: str
    asset_assignment_set_id: str
    provider_id: str
    offset: tuple[float, float, float]
    selected_asset_ids: tuple[str, ...] | None = None
    require_terrain_hit: bool = False


@dataclass(frozen=True, slots=True)
class CompositionOutputExportInput:
    batch_id: str
    point_set_id: str
    asset_assignment_set_id: str
    selected_asset_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class CompositionOutputExportRequest:
    composition_id: str
    composition_generation: int
    provider_id: str
    offset: tuple[float, float, float]
    inputs: tuple[CompositionOutputExportInput, ...]


class OutputExportService:
    """Validate immutable project inputs and route export to one provider."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        providers: tuple[ExportProviderDefinition, ...],
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._providers: dict[str, ExportProviderDefinition] = {}
        for provider in providers:
            provider_id = self._required(provider.provider_id, "provider_id")
            if provider_id in self._providers:
                raise ValueError(f"Duplicate export provider: {provider_id}")
            if provider.max_attempts < 1:
                raise ValueError("Export provider max_attempts must be positive")
            self._providers[provider_id] = provider

    def submit(
        self,
        request: OutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        point_set = self._store.get_point_set(
            self._required(request.point_set_id, "point_set_id")
        )
        if point_set.kind is not PointSetKind.ELEVATION_RESOLVED:
            raise ValueError("Output export requires an elevation_resolved PointSet")
        assignment = self._store.get_asset_assignment_set(
            self._required(
                request.asset_assignment_set_id,
                "asset_assignment_set_id",
            )
        )
        points, assignments = self._validate_assignment_compatibility(point_set, assignment)
        selected = normalize_asset_selection(request.selected_asset_ids)
        expected_count = points["point_count"]
        if selected is not None:
            selected_set = set(selected)
            available = {row["asset_id"] for row in assignments["assignments"]}
            unknown = selected_set - available
            if unknown:
                raise ValueError("Output references unknown assets: " + ", ".join(sorted(unknown)))
            expected_count = sum(row["asset_id"] in selected_set for row in assignments["assignments"])
        provider = self._providers.get(
            self._required(request.provider_id, "provider_id")
        )
        if provider is None:
            raise ValueError(f"Export provider is not registered: {request.provider_id}")
        if provider.target_type != assignment.target_type:
            raise ValueError("Export provider target differs from AssignmentSet")
        if selected is not None and not provider.supports_asset_selection:
            raise ValueError("Export provider does not support asset selection")
        if not isinstance(request.require_terrain_hit, bool):
            raise ValueError("require_terrain_hit must be boolean")
        if request.require_terrain_hit:
            if not provider.supports_terrain_hit_selection:
                raise ValueError("Export provider does not support terrain hit selection")
            eligible = {point["id"] for point in points["points"]
                        if point.get("terrain_sample", {}).get("hit") is True}
            expected_count = sum(row["point_id"] in eligible and
                                 (selected is None or row["asset_id"] in selected)
                                 for row in assignments["assignments"])
            if not expected_count:
                raise ValueError("No terrain hits are available for export")
        binding = self._store.get_effective_binding_snapshot(
            assignment.effective_binding_snapshot_id
        )
        if binding.target_type != assignment.target_type:
            raise ValueError("AssignmentSet target differs from binding snapshot")
        offset = self._offset(request.offset)
        command = {
            "schema": OUTPUT_EXPORT_COMMAND_SCHEMA,
            "version": OUTPUT_EXPORT_COMMAND_VERSION,
            "operation": "export_output",
            "provider": {
                "id": provider.provider_id,
                "version": provider.provider_version,
            },
            "target_type": assignment.target_type,
            "point_set_id": point_set.point_set_id,
            "points_artifact_id": point_set.point_artifact_id,
            "asset_assignment_set_id": assignment.asset_assignment_set_id,
            "assignment_artifact_id": assignment.assignment_artifact_id,
            "effective_binding_snapshot_id": binding.effective_binding_snapshot_id,
            "effective_binding_artifact_id": binding.snapshot_artifact_id,
            "offset_xyz": list(offset),
            "selected_asset_ids": list(selected) if selected is not None else None,
            "expected_record_count": expected_count,
        }
        if request.require_terrain_hit:
            command["require_terrain_hit"] = True
        artifact = self._store.put_bytes(
            canonical_json_bytes(command),
            media_type="application/vnd.verdflow.command+json",
            schema_id=OUTPUT_EXPORT_COMMAND_SCHEMA,
            schema_version=OUTPUT_EXPORT_COMMAND_VERSION,
        )
        default_key = "output-export:" + semantic_hash(command).removeprefix(
            "sha256:"
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=provider.command_type,
                command_version=provider.command_version,
                artifact_id=artifact.artifact_id,
            ),
            idempotency_key=idempotency_key or default_key,
            max_attempts=provider.max_attempts,
            retention_class=JobRetentionClass.RUN,
        )

    def finalize(
        self, job_id: str
    ) -> ExportRecordInfo | CompositionExportRecordInfo:
        existing = self._store.get_export_record_by_job(job_id)
        if existing is not None:
            return existing
        existing_composition = self._store.get_composition_export_record_by_job(
            job_id
        )
        if existing_composition is not None:
            return existing_composition
        job = self._store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise OutputExportError("Export producer Job has not succeeded")
        if job.result_artifact_id is None:
            raise OutputExportError("Export producer Job has no result")
        command = self._read_json(job.command.artifact_id, "Export command")
        self._validate_command(command)
        provider_ref = command["provider"]
        provider = self._providers.get(provider_ref["id"])
        if (
            provider is None
            or provider.provider_version != provider_ref["version"]
            or provider.command_type != job.command.command_type
            or provider.command_version != job.command.command_version
        ):
            raise OutputExportError("Export provider identity is unavailable")
        result = self._read_json(job.result_artifact_id, "Export result")
        if result.get("result_kind") != "output_export":
            raise OutputExportError("Expected output_export result")
        producer = result.get("producer")
        if not isinstance(producer, dict) or producer != provider_ref:
            raise OutputExportError("Export result producer identity differs")
        output_artifact_id = self._output_artifact(result, "export")
        artifact = self._store.get_artifact(output_artifact_id)
        if (
            artifact.media_type != provider.output_media_type
            or artifact.schema_id != provider.output_schema_id
            or artifact.schema_version != provider.output_schema_version
        ):
            raise OutputExportError("Export output Artifact schema is unsupported")
        record_count = result.get("record_count")
        if (
            not isinstance(record_count, int)
            or isinstance(record_count, bool)
            or record_count < 0
        ):
            raise OutputExportError("Export result record_count is invalid")
        offset = self._offset(tuple(command["offset_xyz"]))
        if record_count != command.get("expected_record_count", record_count):
            raise OutputExportError("Export result count differs from selected inputs")
        if command["operation"] == "export_composition":
            input_records = tuple(
                CompositionExportInputInfo(
                    ordinal=index,
                    batch_id=item["batch_id"],
                    point_set_id=item["point_set_id"],
                    asset_assignment_set_id=item[
                        "asset_assignment_set_id"
                    ],
                    selected_asset_ids=normalize_asset_selection(
                        item.get("selected_asset_ids")
                    ),
                )
                for index, item in enumerate(command["inputs"])
            )
            return self._store.create_composition_export_record(
                composition_id=command["composition_id"],
                composition_generation=command["composition_generation"],
                producer_job_id=job.job_id,
                adapter_id=provider.provider_id,
                adapter_version=provider.provider_version,
                target_type=command["target_type"],
                output_artifact_id=output_artifact_id,
                offset=offset,
                record_count=record_count,
                inputs=input_records,
            )
        return self._store.create_export_record(
            producer_job_id=job.job_id,
            adapter_id=provider.provider_id,
            adapter_version=provider.provider_version,
            target_type=command["target_type"],
            point_set_id=command["point_set_id"],
            asset_assignment_set_id=command["asset_assignment_set_id"],
            output_artifact_id=output_artifact_id,
            offset=offset,
            record_count=record_count,
            selected_asset_ids=normalize_asset_selection(command.get("selected_asset_ids")),
            require_terrain_hit=command.get("require_terrain_hit", False),
        )

    def submit_composition(
        self,
        request: CompositionOutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        composition_id = self._required(
            request.composition_id, "composition_id"
        )
        composition = self._store.current_planting_composition()
        if composition is None or composition.composition_id != composition_id:
            raise ValueError("Planting composition is not current")
        if request.composition_generation != composition.generation:
            raise ValueError("Planting composition generation is stale")
        if not request.inputs:
            raise ValueError("Composition export requires at least one input")
        batches = {
            batch.batch_id: batch
            for batch in self._store.list_planting_batches()
        }
        provider = self._providers.get(
            self._required(request.provider_id, "provider_id")
        )
        if provider is None:
            raise ValueError(
                f"Export provider is not registered: {request.provider_id}"
            )
        command_inputs: list[dict[str, Any]] = []
        expected_count = 0
        seen: set[str] = set()
        for ordinal, item in enumerate(request.inputs):
            batch_id = self._required(item.batch_id, "batch_id")
            if batch_id in seen or batch_id not in batches:
                raise ValueError("Composition export batch set is invalid")
            seen.add(batch_id)
            batch = batches[batch_id]
            if item.asset_assignment_set_id != batch.asset_assignment_set_id:
                raise ValueError("Composition export AssignmentSet is stale")
            point_set = self._store.get_point_set(
                self._required(item.point_set_id, "point_set_id")
            )
            if (
                point_set.kind is not PointSetKind.ELEVATION_RESOLVED
                or point_set.parent_point_set_id
                != batch.generated_point_set_id
            ):
                raise ValueError(
                    "Composition export requires each batch elevation PointSet"
                )
            assignment = self._store.get_asset_assignment_set(
                item.asset_assignment_set_id
            )
            points, assignments = self._validate_assignment_compatibility(
                point_set, assignment
            )
            if provider.target_type != assignment.target_type:
                raise ValueError(
                    "Export provider target differs from a batch AssignmentSet"
                )
            selected = normalize_asset_selection(item.selected_asset_ids)
            count = points["point_count"]
            if selected is not None:
                if not provider.supports_asset_selection:
                    raise ValueError(
                        "Export provider does not support asset selection"
                    )
                available = {
                    row["asset_id"] for row in assignments["assignments"]
                }
                unknown = set(selected) - available
                if unknown:
                    raise ValueError(
                        "Output references unknown assets: "
                        + ", ".join(sorted(unknown))
                    )
                count = sum(
                    row["asset_id"] in set(selected)
                    for row in assignments["assignments"]
                )
            binding = self._store.get_effective_binding_snapshot(
                assignment.effective_binding_snapshot_id
            )
            if binding.target_type != assignment.target_type:
                raise ValueError(
                    "AssignmentSet target differs from binding snapshot"
                )
            expected_count += count
            command_inputs.append(
                {
                    "ordinal": ordinal,
                    "batch_id": batch_id,
                    "point_set_id": point_set.point_set_id,
                    "points_artifact_id": point_set.point_artifact_id,
                    "asset_assignment_set_id": (
                        assignment.asset_assignment_set_id
                    ),
                    "assignment_artifact_id": assignment.assignment_artifact_id,
                    "effective_binding_snapshot_id": (
                        binding.effective_binding_snapshot_id
                    ),
                    "effective_binding_artifact_id": binding.snapshot_artifact_id,
                    "selected_asset_ids": (
                        list(selected) if selected is not None else None
                    ),
                }
            )
        offset = self._offset(request.offset)
        command = {
            "schema": OUTPUT_EXPORT_COMMAND_SCHEMA,
            "version": OUTPUT_EXPORT_COMMAND_VERSION,
            "operation": "export_composition",
            "provider": {
                "id": provider.provider_id,
                "version": provider.provider_version,
            },
            "target_type": provider.target_type,
            "composition_id": composition_id,
            "composition_generation": composition.generation,
            "inputs": command_inputs,
            "offset_xyz": list(offset),
            "expected_record_count": expected_count,
        }
        artifact = self._store.put_bytes(
            canonical_json_bytes(command),
            media_type="application/vnd.verdflow.command+json",
            schema_id=OUTPUT_EXPORT_COMMAND_SCHEMA,
            schema_version=OUTPUT_EXPORT_COMMAND_VERSION,
        )
        default_key = (
            "composition-output-export:"
            + semantic_hash(command).removeprefix("sha256:")
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=provider.command_type,
                command_version=provider.command_version,
                artifact_id=artifact.artifact_id,
            ),
            idempotency_key=idempotency_key or default_key,
            max_attempts=provider.max_attempts,
            retention_class=JobRetentionClass.RUN,
        )

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        with self._store.open_artifact(artifact_id) as stream:
            try:
                value = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise OutputExportError(f"{label} is not valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise OutputExportError(f"{label} must be a JSON object")
        return value

    def _validate_assignment_compatibility(
        self,
        point_set: PointSetInfo,
        assignment: AssetAssignmentSetInfo,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if assignment.point_set_id not in {
            point_set.point_set_id,
            point_set.parent_point_set_id,
        }:
            raise ValueError(
                "AssignmentSet must reference the export PointSet or its "
                "direct generated parent"
            )
        if assignment.point_count != point_set.point_count:
            raise ValueError("AssignmentSet point count differs from export PointSet")
        point_document = self._read_json(
            point_set.point_artifact_id,
            "Export PointSet",
        )
        assignment_document = self._read_json(
            assignment.assignment_artifact_id,
            "AssetAssignmentSet",
        )
        points = point_document.get("points")
        assignments = assignment_document.get("assignments")
        if not isinstance(points, list) or not isinstance(assignments, list):
            raise ValueError("Export inputs do not contain point collections")
        point_ids = {
            item.get("id")
            for item in points
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        assignment_ids = {
            item.get("point_id")
            for item in assignments
            if isinstance(item, dict) and isinstance(item.get("point_id"), str)
        }
        if (
            len(point_ids) != len(points)
            or len(assignment_ids) != len(assignments)
            or point_ids != assignment_ids
        ):
            raise ValueError(
                "AssignmentSet point identities differ from export PointSet"
            )
        return point_document, assignment_document

    @staticmethod
    def _validate_command(value: dict[str, Any]) -> None:
        if (
            value.get("schema") != OUTPUT_EXPORT_COMMAND_SCHEMA
            or str(value.get("version")) != OUTPUT_EXPORT_COMMAND_VERSION
            or value.get("operation")
            not in {"export_output", "export_composition"}
        ):
            raise OutputExportError("Export command schema is unsupported")

    @staticmethod
    def _output_artifact(result: dict[str, Any], role: str) -> str:
        outputs = result.get("output_artifacts")
        if not isinstance(outputs, list):
            raise OutputExportError("Export output_artifacts is required")
        matches = [
            item
            for item in outputs
            if isinstance(item, dict) and item.get("role") == role
        ]
        if len(matches) != 1:
            raise OutputExportError(f"Export output role must occur once: {role}")
        return OutputExportService._required(
            matches[0].get("artifact_id"),
            "artifact_id",
        )

    @staticmethod
    def _required(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _offset(value: tuple[object, ...]) -> tuple[float, float, float]:
        if len(value) != 3:
            raise ValueError("offset must contain exactly three values")
        result = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in result):
            raise ValueError("offset values must be finite")
        return result[0], result[1], result[2]
