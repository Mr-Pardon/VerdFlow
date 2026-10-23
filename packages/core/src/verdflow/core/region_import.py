"""Finalize typed Region-producing results into immutable project state."""

from __future__ import annotations

import json
import math
from typing import Any

from verdflow.contracts import (
    JobStatus,
    RegionSetInfo,
    RegionSummary,
    SourceKind,
)
from verdflow.project_store import ProjectStore


class RegionSetImportError(RuntimeError):
    """Raised when typed Region outputs cannot become a RegionSet."""


class RegionSetImportService:
    """Validate shared Region result artifacts and create a RegionSet."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def finalize_result(
        self,
        *,
        job_id: str,
        scene_revision_id: str,
        source_id: str,
        result: dict[str, Any],
        command_artifact_id: str,
    ) -> RegionSetInfo:
        """Create a RegionSet after a typed entry point validates result kind."""
        existing = self.store.get_region_set_by_job(job_id)
        if existing is not None:
            return existing
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise RegionSetImportError("Region-producing Job has not succeeded")
        if command_artifact_id != job.command.artifact_id:
            raise RegionSetImportError(
                "Region command Artifact differs from producing Job"
            )
        scene_revision = self.store.get_scene_revision(scene_revision_id)
        source = self.store.get_source(scene_revision.source_id)
        assembly_member_ids = {
            member.source_member_id
            for member in self.store.list_source_assembly_members(
                source.source_id
            )
        }
        if source_id != source.source_id:
            raise RegionSetImportError(
                "Region source differs from SceneRevision"
            )
        if self._required_text(result, "source_id") != source.source_id:
            raise RegionSetImportError(
                "Region result Source differs from SceneRevision"
            )

        outputs = self._outputs_by_role(result)
        geometry_artifact_id = self._required_output(outputs, "regions")
        metrics_artifact_id = self._required_output(
            outputs,
            "geometry_metrics",
        )
        geometry = self._read_json(
            geometry_artifact_id,
            "Region geometry",
        )
        metrics = self._read_json(
            metrics_artifact_id,
            "Region metrics",
        )
        self._validate_payload(
            geometry,
            schema="verdflow.regions",
            label="Region geometry",
        )
        self._validate_payload(
            metrics,
            schema="verdflow.region-metrics",
            label="Region metrics",
        )
        geometry_rows = geometry.get("regions")
        metric_rows = metrics.get("regions")
        if not isinstance(geometry_rows, list):
            raise RegionSetImportError("Region geometry rows are required")
        if not isinstance(metric_rows, list):
            raise RegionSetImportError("Region metric rows are required")
        if geometry.get("region_count") != len(geometry_rows):
            raise RegionSetImportError("Region geometry count is inconsistent")
        if metrics.get("region_count") != len(metric_rows):
            raise RegionSetImportError("Region metric count is inconsistent")
        if len(geometry_rows) != len(metric_rows):
            raise RegionSetImportError(
                "Region geometry and metric counts differ"
            )

        metrics_by_id: dict[str, dict[str, Any]] = {}
        for metric in metric_rows:
            if not isinstance(metric, dict):
                raise RegionSetImportError("Region metric must be an object")
            region_id = self._required_text(metric, "id")
            if region_id in metrics_by_id:
                raise RegionSetImportError(
                    f"Region metric is duplicated: {region_id}"
                )
            metrics_by_id[region_id] = metric

        summaries = tuple(
            self._summary(
                index,
                region,
                metrics_by_id,
                require_source_member=(
                    source.kind is SourceKind.ASSEMBLY
                ),
                allowed_source_member_ids=assembly_member_ids,
            )
            for index, region in enumerate(geometry_rows)
        )
        if set(metrics_by_id) != {
            summary.region_id for summary in summaries
        }:
            raise RegionSetImportError(
                "Region geometry and metric identifiers differ"
            )
        producer = result.get("producer")
        if not isinstance(producer, dict):
            raise RegionSetImportError("Worker result lacks producer provenance")
        command_artifact = self.store.get_artifact(command_artifact_id)
        return self.store.create_region_set(
            scene_revision_id=scene_revision.scene_revision_id,
            producer_job_id=job.job_id,
            extractor_id=self._required_text(producer, "id"),
            extractor_version=self._required_text(producer, "version"),
            input_hash=command_artifact.content_hash,
            coordinate_frame_id=scene_revision.coordinate_frame_id,
            geometry_artifact_id=geometry_artifact_id,
            metrics_artifact_id=metrics_artifact_id,
            regions=summaries,
        )

    def _summary(
        self,
        ordinal: int,
        region: object,
        metrics_by_id: dict[str, dict[str, Any]],
        *,
        require_source_member: bool,
        allowed_source_member_ids: set[str],
    ) -> RegionSummary:
        if not isinstance(region, dict):
            raise RegionSetImportError("Region geometry must be an object")
        region_id = self._required_text(region, "id")
        try:
            metric = metrics_by_id[region_id]
        except KeyError as error:
            raise RegionSetImportError(
                f"Region metrics are missing: {region_id}"
            ) from error
        bbox = region.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 6
            or not all(isinstance(value, (int, float)) for value in bbox)
        ):
            raise RegionSetImportError(
                f"Region bbox is invalid: {region_id}"
            )
        classification = region.get(
            "classification",
            region.get("greenbelt_type"),
        )
        strategy_hint = region.get("strategy_hint")
        source_member_id = self._optional_text(
            region.get("source_member_id"),
            "source_member_id",
            region_id,
        )
        metric_source_member_id = self._optional_text(
            metric.get("source_member_id"),
            "source_member_id",
            region_id,
        )
        if source_member_id != metric_source_member_id:
            raise RegionSetImportError(
                f"Region source member differs from metrics: {region_id}"
            )
        if require_source_member and source_member_id is None:
            raise RegionSetImportError(
                f"Assembly Region lacks source_member_id: {region_id}"
            )
        if (
            source_member_id is not None
            and source_member_id not in allowed_source_member_ids
        ):
            raise RegionSetImportError(
                f"Region source_member_id is outside SceneRevision: {region_id}"
            )
        footprint_area = self._optional_float(
            metric.get("footprint_area_xy"),
            "footprint_area_xy",
            region_id,
        )
        if (
            footprint_area is None
            or not math.isfinite(footprint_area)
            or footprint_area <= 0.0
        ):
            raise RegionSetImportError(
                f"footprint_area_xy must be positive for Region {region_id}"
            )
        return RegionSummary(
            region_id=region_id,
            ordinal=ordinal,
            label=str(region.get("label", "")),
            tags=str(region.get("tags", "")),
            classification=(
                str(classification) if classification is not None else None
            ),
            strategy_hint=(
                str(strategy_hint) if strategy_hint is not None else None
            ),
            footprint_area_xy=footprint_area,
            boundary_perimeter_xy=self._optional_float(
                metric.get("boundary_perimeter_xy"),
                "boundary_perimeter_xy",
                region_id,
            ),
            min_x=float(bbox[0]),
            min_y=float(bbox[1]),
            min_z=float(bbox[2]),
            max_x=float(bbox[3]),
            max_y=float(bbox[4]),
            max_z=float(bbox[5]),
            metrics_json=json.dumps(
                metric,
                ensure_ascii=False,
                sort_keys=True,
            ),
            source_member_id=source_member_id,
        )

    @staticmethod
    def _validate_payload(
        payload: dict[str, Any],
        *,
        schema: str,
        label: str,
    ) -> None:
        if payload.get("schema") != schema or str(
            payload.get("version")
        ) != "1":
            raise RegionSetImportError(f"{label} schema is unsupported")
        if payload.get("coordinate_space") != "source_scene_world":
            raise RegionSetImportError(
                f"{label} coordinate space is unsupported"
            )
        if payload.get("offset_applied") is not False:
            raise RegionSetImportError(
                f"{label} must use unshifted coordinates"
            )

    def _read_json(
        self,
        artifact_id: str,
        label: str,
    ) -> dict[str, Any]:
        with self.store.open_artifact(artifact_id) as stream:
            try:
                payload = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RegionSetImportError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(payload, dict):
            raise RegionSetImportError(f"{label} must be a JSON object")
        return payload

    @staticmethod
    def _outputs_by_role(
        result: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        outputs = result.get("output_artifacts")
        if not isinstance(outputs, list):
            raise RegionSetImportError("Worker output_artifacts is required")
        by_role: dict[str, dict[str, Any]] = {}
        for output in outputs:
            if not isinstance(output, dict):
                raise RegionSetImportError("Worker output descriptor is invalid")
            role = RegionSetImportService._required_text(output, "role")
            if role in by_role:
                raise RegionSetImportError(
                    f"Worker output role is duplicated: {role}"
                )
            by_role[role] = output
        return by_role

    @staticmethod
    def _required_output(
        outputs: dict[str, dict[str, Any]],
        role: str,
    ) -> str:
        try:
            output = outputs[role]
        except KeyError as error:
            raise RegionSetImportError(
                f"Worker result lacks output role: {role}"
            ) from error
        return RegionSetImportService._required_text(output, "artifact_id")

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RegionSetImportError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _optional_float(
        value: object,
        field: str,
        region_id: str,
    ) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)):
            raise RegionSetImportError(
                f"{field} is invalid for Region {region_id}"
            )
        return float(value)

    @staticmethod
    def _optional_text(
        value: object,
        field: str,
        region_id: str,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise RegionSetImportError(
                f"{field} is invalid for Region {region_id}"
            )
        return value.strip()
