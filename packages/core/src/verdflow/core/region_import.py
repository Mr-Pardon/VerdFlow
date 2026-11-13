"""Finalize typed Region-producing results into immutable project state."""

from __future__ import annotations

from contextlib import closing
import json
import math
from typing import Any, BinaryIO, Callable, Iterator

import ijson
from ijson.common import JSONError, ObjectBuilder

from verdflow.contracts import (
    JobStatus,
    RegionSetInfo,
    RegionSummary,
    SourceKind,
)
from verdflow.project_store import ProjectStore


_INLINE_REGION_DOCUMENT_BYTES = 4 * 1024**2
_REGION_SUMMARY_FIELDS = frozenset({
    "id", "bbox", "label", "tags", "classification", "greenbelt_type", "strategy_hint", "source_member_id",
})
_REGION_HEADER_FIELDS = frozenset({"schema", "version", "coordinate_space", "offset_applied", "region_count"})
RegionEvent = tuple[str, Any]
RegionBoundaryReader = Callable[[Iterator[RegionEvent], RegionEvent], Any]


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
        check_progress: Callable[[], None] | None = None,
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
        metrics_by_id: dict[str, dict[str, Any]] = {}
        with closing(self.read_rows(
            metrics_artifact_id, schema="verdflow.region-metrics", label="Region metrics",
            check_progress=check_progress,
        )) as metric_rows:
            for ordinal, metric in enumerate(metric_rows):
                if check_progress is not None and ordinal % 256 == 0:
                    check_progress()
                if not isinstance(metric, dict):
                    raise RegionSetImportError("Region metric must be an object")
                region_id = self._required_text(metric, "id")
                if region_id in metrics_by_id:
                    raise RegionSetImportError(
                        f"Region metric is duplicated: {region_id}"
                    )
                metrics_by_id[region_id] = metric

        with closing(self.read_rows(
            geometry_artifact_id, schema="verdflow.regions", label="Region geometry",
            fields=_REGION_SUMMARY_FIELDS, check_progress=check_progress,
        )) as geometry_rows:
            def summary_rows() -> Iterator[RegionSummary]:
                for index, region in enumerate(geometry_rows):
                    if check_progress is not None and index % 256 == 0:
                        check_progress()
                    yield self._summary(
                        index, region, metrics_by_id,
                        require_source_member=(source.kind is SourceKind.ASSEMBLY),
                        allowed_source_member_ids=assembly_member_ids,
                    )

            summaries = tuple(summary_rows())
        if metrics_by_id:
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
            metric = metrics_by_id.pop(region_id)
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

    def read_rows(
        self, artifact_id: str, *, schema: str, label: str, fields: frozenset[str] | None = None,
        read_boundary: RegionBoundaryReader | None = None,
        check_progress: Callable[[], None] | None = None,
    ) -> Iterator[Any]:
        artifact = self.store.get_artifact(artifact_id)
        with self.store.open_artifact(artifact_id) as stream:
            try:
                if artifact.byte_size <= _INLINE_REGION_DOCUMENT_BYTES:
                    payload = json.load(stream)
                    if not isinstance(payload, dict):
                        raise RegionSetImportError(f"{label} must be a JSON object")
                    self._validate_payload(payload, schema=schema, label=label)
                    rows = payload.get("regions")
                    if not isinstance(rows, list):
                        raise RegionSetImportError(f"{label} rows are required")
                    if payload.get("region_count") != len(rows):
                        raise RegionSetImportError(f"{label} count is inconsistent")
                    yield from rows
                    return

                reader = stream if check_progress is None else _ProgressReader(stream, check_progress)
                events = iter(ijson.basic_parse(reader, use_float=True))
                if next(events) != ("start_map", None):
                    raise RegionSetImportError(f"{label} must be a JSON object")
                header: dict[str, Any] = {}
                has_rows, count = False, 0
                while (field := next(events))[0] != "end_map":
                    key = field[1]
                    first = next(events)
                    if key == "regions":
                        if has_rows or first != ("start_array", None):
                            raise RegionSetImportError(f"{label} must contain one Region array")
                        has_rows = True
                        while (item := next(events))[0] != "end_array":
                            if item != ("start_map", None):
                                raise RegionSetImportError(f"{label} row must be an object")
                            row: dict[str, Any] = {}
                            while (member := next(events))[0] != "end_map":
                                name = member[1]
                                keep = fields is None or name in fields
                                first = next(events)
                                if keep and read_boundary is not None and name == "footprint_boundary_xyz":
                                    value = read_boundary(events, first)
                                else:
                                    value = _read_value(events, first, keep=keep)
                                if keep:
                                    row[name] = value
                            count += 1
                            yield row
                    else:
                        keep = key in _REGION_HEADER_FIELDS
                        value = _read_value(events, first, keep=keep)
                        if keep:
                            if key in header:
                                raise RegionSetImportError(f"{label} contains a duplicate header field")
                            header[key] = value
                if next(events, None) is not None:
                    raise RegionSetImportError(f"{label} contains trailing data")
                self._validate_payload(header, schema=schema, label=label)
                if not has_rows or header.get("region_count") != count:
                    raise RegionSetImportError(f"{label} count is inconsistent")
            except (UnicodeDecodeError, json.JSONDecodeError, JSONError, StopIteration) as error:
                raise RegionSetImportError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error

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


class _ProgressReader:
    def __init__(self, stream: BinaryIO, check: Callable[[], None]) -> None:
        self._stream = stream
        self._check = check
        self._remaining = 1024**2

    def read(self, size: int) -> bytes:
        chunk = self._stream.read(size)
        self._remaining -= len(chunk)
        if self._remaining <= 0:
            self._check()
            self._remaining = 1024**2
        return chunk


def _read_value(events: Iterator[RegionEvent], first: RegionEvent, *, keep: bool) -> Any:
    event, value = first
    if event not in {"start_map", "start_array"}:
        return value if keep else None
    builder = ObjectBuilder() if keep else None
    if builder is not None:
        builder.event(event, value)
    depth = 1
    for event, value in events:
        if event in {"start_map", "start_array"}:
            depth += 1
        elif event in {"end_map", "end_array"}:
            depth -= 1
        if builder is not None:
            builder.event(event, value)
        if not depth:
            return builder.value if builder is not None else None
    raise StopIteration
