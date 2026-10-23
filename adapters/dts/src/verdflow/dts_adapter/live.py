"""Transport-neutral state and callback correlation for DTS Live sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json
import math
from threading import Condition, RLock
from typing import Any, Iterable, Sequence
from uuid import uuid4

from verdflow.contracts import require_sha256_hash


DTS_LIVE_ELEVATION_PROVIDER_ID = "verdflow.dts.live-elevation"
DTS_LIVE_CONNECTOR_VERSION = "1"


class DtsLiveCommandKind(StrEnum):
    BEGIN_POLYGON_SELECTION = "begin_polygon_selection"
    SAMPLE_ELEVATION = "sample_elevation"
    IMPORT_XML = "import_xml"


class DtsLiveOperationStage(StrEnum):
    SELECTION_PENDING = "selection_pending"
    SELECTION_READY = "selection_ready"
    ELEVATION_PENDING = "elevation_pending"
    ELEVATION_READY = "elevation_ready"
    DELIVERY_PENDING = "delivery_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DtsLiveExecutionSnapshot:
    strategy_package_hash: str
    strategy_id: str
    strategy_version: str
    target_type: str
    asset_source_kind: str
    asset_source_id: str
    asset_source_generation: int | None
    asset_source_hash: str
    effective_binding_semantic_hash: str
    profile_id: str | None = None
    profile_semantic_hash: str | None = None
    binding_overrides: dict[str, Any] | None = None
    random_seed: int | None = None
    generation_args: dict[str, Any] | None = None

    def validate(self) -> None:
        for value in (
            self.strategy_package_hash,
            self.asset_source_hash,
            self.effective_binding_semantic_hash,
        ):
            require_sha256_hash(value)
        if self.profile_semantic_hash is not None:
            require_sha256_hash(self.profile_semantic_hash)
        for field in (
            "strategy_id",
            "strategy_version",
            "target_type",
            "asset_source_kind",
            "asset_source_id",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} is required")
            if value != value.strip():
                raise ValueError(f"{field} cannot contain outer whitespace")
        if self.target_type != "dts":
            raise ValueError("DTS Live execution snapshot must target dts")
        if self.asset_source_kind not in {"catalog", "profile", "draft"}:
            raise ValueError("DTS Live asset source kind is invalid")
        if self.asset_source_kind == "catalog":
            if (
                not isinstance(self.asset_source_generation, int)
                or isinstance(self.asset_source_generation, bool)
                or self.asset_source_generation < 1
            ):
                raise ValueError("Catalog source generation must be positive")
        elif self.asset_source_generation is not None:
            raise ValueError("Profile source cannot have a Catalog generation")
        if self.asset_source_kind == "draft" and self.asset_source_id != self.asset_source_hash:
            raise ValueError("Draft source identity must equal its frozen semantic hash")
        profile_fields = (
            self.profile_id,
            self.profile_semantic_hash,
        )
        if any(value is not None for value in profile_fields):
            if not all(
                isinstance(value, str)
                and bool(value.strip())
                and value == value.strip()
                for value in profile_fields
            ):
                raise ValueError("DTS Live Profile reference must be complete")
            if self.binding_overrides is not None and not isinstance(self.binding_overrides, dict):
                raise ValueError("Profile binding overrides must be an object")
            if (
                self.asset_source_kind != "profile"
                or self.asset_source_id != self.profile_id
            ):
                raise ValueError("Profile source must match the Profile reference")
        elif not isinstance(self.binding_overrides, dict) or not self.binding_overrides:
            raise ValueError("DTS Live execution requires Profile or role mappings")
        if self.random_seed is not None and (
            not isinstance(self.random_seed, int)
            or isinstance(self.random_seed, bool)
        ):
            raise ValueError("DTS Live random seed must be an integer")
        if self.generation_args is not None and not isinstance(
            self.generation_args,
            dict,
        ):
            raise ValueError("DTS Live generation args must be an object")
        try:
            json.dumps(self.generation_args or {}, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "DTS Live generation args must contain valid JSON values"
            ) from error


@dataclass(frozen=True, slots=True)
class DtsLiveCorrelation:
    connector_session_id: str
    operation_id: str
    generation_token: int
    request_id: str


@dataclass(frozen=True, slots=True)
class DtsLiveCommand:
    sequence: int
    kind: DtsLiveCommandKind
    correlation: DtsLiveCorrelation
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DtsLiveRegion:
    region_id: str
    name: str
    coordinates: tuple[tuple[float, float, float], ...]
    bbox: tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class DtsLiveSelection:
    correlation: DtsLiveCorrelation
    regions: tuple[DtsLiveRegion, ...]


@dataclass(frozen=True, slots=True)
class DtsLiveElevationBatch:
    region_id: str
    name: str
    bbox: tuple[float, float, float, float, float, float]
    start: int
    count: int


@dataclass(frozen=True, slots=True)
class DtsElevationSample:
    point_id: str
    position: tuple[float, float, float] | None


@dataclass(frozen=True, slots=True)
class DtsLiveRegionFailure:
    region_id: str
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class DtsLiveDeliveryReceipt:
    correlation: DtsLiveCorrelation
    export_id: str
    succeeded: bool
    message: str | None = None


@dataclass(frozen=True, slots=True)
class DtsLiveSessionSnapshot:
    connector_session_id: str
    generation_token: int
    operation_id: str | None
    stage: DtsLiveOperationStage | None
    execution: DtsLiveExecutionSnapshot | None
    pending_request_id: str | None
    pending_command_kind: DtsLiveCommandKind | None
    selection_coordinate_count: int
    elevation_point_count: int
    export_id: str | None
    error: str | None
    selection_regions: tuple[DtsLiveRegion, ...] = ()
    sampling_failures: tuple[DtsLiveRegionFailure, ...] = ()


@dataclass(slots=True)
class _Operation:
    operation_id: str
    generation_token: int
    execution: DtsLiveExecutionSnapshot
    stage: DtsLiveOperationStage
    pending: DtsLiveCommand | None = None
    selection: DtsLiveSelection | None = None
    point_ids: tuple[str, ...] = ()
    elevation: tuple[DtsElevationSample, ...] = ()
    export_id: str | None = None
    receipt: DtsLiveDeliveryReceipt | None = None
    error: str | None = None
    elevation_batches: tuple[DtsLiveElevationBatch, ...] = ()
    elevation_chunks: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    sampling_failures: tuple[DtsLiveRegionFailure, ...] = ()


class DtsLiveSession:
    """Enforce one current operation and reject stale Explorer callbacks."""

    def __init__(self, connector_session_id: str | None = None) -> None:
        self.connector_session_id = _text(
            connector_session_id or uuid4().hex,
            "connector_session_id",
        )
        self._condition = Condition(RLock())
        self._generation_token = 0
        self._next_sequence = 1
        self._operation: _Operation | None = None
        self._commands: list[DtsLiveCommand] = []

    def begin_selection(
        self,
        execution: DtsLiveExecutionSnapshot,
        regions: object = None,
    ) -> DtsLiveCommand | DtsLiveSelection:
        execution.validate()
        normalized = normalize_live_regions(regions) if regions is not None else None
        with self._condition:
            if self._operation is not None and self._operation.stage not in {
                DtsLiveOperationStage.COMPLETED,
                DtsLiveOperationStage.CANCELLED,
                DtsLiveOperationStage.SUPERSEDED,
                DtsLiveOperationStage.FAILED,
            }:
                self._operation.stage = DtsLiveOperationStage.SUPERSEDED
                self._operation.pending = None
            self._generation_token += 1
            operation = _Operation(
                operation_id=uuid4().hex,
                generation_token=self._generation_token,
                execution=execution,
                stage=DtsLiveOperationStage.SELECTION_PENDING,
            )
            self._operation = operation
            if normalized is not None:
                selection = DtsLiveSelection(
                    correlation=DtsLiveCorrelation(
                        self.connector_session_id, operation.operation_id,
                        operation.generation_token, uuid4().hex,
                    ),
                    regions=normalized,
                )
                operation.selection = selection
                operation.stage = DtsLiveOperationStage.SELECTION_READY
                self._condition.notify_all()
                return selection
            command = self._queue(
                operation,
                DtsLiveCommandKind.BEGIN_POLYGON_SELECTION,
                {},
            )
            self._condition.notify_all()
            return command

    def begin_regeneration(
        self,
        execution: DtsLiveExecutionSnapshot,
    ) -> DtsLiveSelection:
        """Start a new operation from the last accepted polygon."""

        execution.validate()
        with self._condition:
            previous = self._operation
            if previous is None or previous.selection is None:
                raise ValueError("DTS Live regeneration requires a previous selection")
            if previous.stage not in {
                DtsLiveOperationStage.COMPLETED,
                DtsLiveOperationStage.CANCELLED,
                DtsLiveOperationStage.SUPERSEDED,
                DtsLiveOperationStage.FAILED,
            }:
                raise ValueError("DTS Live operation must finish before regeneration")

            self._generation_token += 1
            operation = _Operation(
                operation_id=uuid4().hex,
                generation_token=self._generation_token,
                execution=execution,
                stage=DtsLiveOperationStage.SELECTION_READY,
            )
            correlation = DtsLiveCorrelation(
                connector_session_id=self.connector_session_id,
                operation_id=operation.operation_id,
                generation_token=operation.generation_token,
                request_id=uuid4().hex,
            )
            selection = DtsLiveSelection(
                correlation=correlation,
                regions=previous.selection.regions,
            )
            operation.selection = selection
            self._operation = operation
            self._condition.notify_all()
            return selection

    def accept_selection(
        self,
        correlation: DtsLiveCorrelation,
        coordinates: object,
    ) -> DtsLiveSelection:
        normalized = normalize_live_regions([{
            "region_id": "dts_live_" + correlation.operation_id[:16],
            "name": "DTS Live Selection",
            "coordinates": coordinates,
        }])
        with self._condition:
            operation = self._require_pending(
                correlation,
                DtsLiveCommandKind.BEGIN_POLYGON_SELECTION,
            )
            selection = DtsLiveSelection(
                correlation=correlation,
                regions=normalized,
            )
            operation.selection = selection
            operation.pending = None
            operation.stage = DtsLiveOperationStage.SELECTION_READY
            self._condition.notify_all()
            return selection

    def current_selection(
        self,
        operation_id: str,
        generation_token: int,
    ) -> DtsLiveSelection:
        with self._condition:
            operation = self._require_current(operation_id, generation_token)
            if operation.selection is None:
                raise ValueError("DTS Live selection is not ready")
            return operation.selection

    def current_execution(
        self,
        operation_id: str,
        generation_token: int,
    ) -> DtsLiveExecutionSnapshot:
        with self._condition:
            return self._require_current(
                operation_id,
                generation_token,
            ).execution

    def request_elevation(
        self,
        *,
        operation_id: str,
        generation_token: int,
        point_set_id: str,
        point_ids: Sequence[str],
        lines: Sequence[dict[str, object]],
        batches: Sequence[DtsLiveElevationBatch] = (),
    ) -> DtsLiveCommand:
        point_set = _text(point_set_id, "point_set_id")
        frozen_ids = tuple(_text(value, "point_id") for value in point_ids)
        if not frozen_ids or len(set(frozen_ids)) != len(frozen_ids):
            raise ValueError("point_ids must be non-empty and unique")
        normalized_lines = tuple(_line(value) for value in lines)
        if len(normalized_lines) != len(frozen_ids):
            raise ValueError("Elevation lines must match frozen Point IDs")
        cursor = 0
        if len({batch.region_id for batch in batches}) != len(batches):
            raise ValueError("Elevation batches must have unique region IDs")
        for batch in batches:
            if batch.start != cursor or batch.count < 1:
                raise ValueError("Elevation batches must partition the frozen Point IDs")
            cursor += batch.count
        if batches and cursor != len(frozen_ids):
            raise ValueError("Elevation batch count differs from frozen Point IDs")
        with self._condition:
            operation = self._require_current(operation_id, generation_token)
            if operation.stage is not DtsLiveOperationStage.SELECTION_READY:
                raise ValueError("DTS Live operation is not ready for elevation")
            operation.point_ids = frozen_ids
            operation.elevation_batches = tuple(batches)
            operation.elevation_chunks.clear()
            operation.stage = DtsLiveOperationStage.ELEVATION_PENDING
            command = self._queue(
                operation,
                DtsLiveCommandKind.SAMPLE_ELEVATION,
                {"point_set_id": point_set, "lines": list(normalized_lines),
                 "batches": [asdict(batch) for batch in batches],
                 "stream_results": bool(batches)},
            )
            self._condition.notify_all()
            return command

    def accept_elevation_chunk(
        self, correlation: DtsLiveCorrelation, indices: object, intersections: object,
    ) -> int:
        positions = tuple(_intersection_positions(intersections))
        if not isinstance(indices, list) or not indices or len(indices) > 2000:
            raise ValueError("Elevation chunk requires 1..2000 point indices")
        if any(type(i) is not int for i in indices) or len(set(indices)) != len(indices):
            raise ValueError("Elevation chunk indices must be unique integers")
        if len(positions) != len(indices) or any(p is None for p in positions):
            raise ValueError("Elevation chunk must contain matching valid hits")
        with self._condition:
            operation = self._require_pending(correlation, DtsLiveCommandKind.SAMPLE_ELEVATION)
            if any(i < 0 or i >= len(operation.point_ids) for i in indices):
                raise ValueError("Elevation chunk index is out of range")
            if any(i in operation.elevation_chunks and operation.elevation_chunks[i] != p
                   for i, p in zip(indices, positions)):
                raise ValueError("Elevation chunk conflicts with an accepted point")
            operation.elevation_chunks.update(zip(indices, positions))
            return len(indices)

    def accept_elevation(
        self,
        correlation: DtsLiveCorrelation,
        intersections: object,
        *,
        failed_regions: object = None,
        chunked: bool = False,
    ) -> tuple[DtsElevationSample, ...]:
        positions = () if chunked else tuple(_intersection_positions(intersections))
        with self._condition:
            operation = self._require_pending(
                correlation,
                DtsLiveCommandKind.SAMPLE_ELEVATION,
            )
            if chunked:
                positions = tuple(operation.elevation_chunks.get(i) for i in range(len(operation.point_ids)))
            failures: list[DtsLiveRegionFailure] = []
            excluded: set[int] = set()
            if failed_regions is not None:
                if not isinstance(failed_regions, list):
                    raise ValueError("failed_regions must be an array")
                batches = {b.region_id: b for b in operation.elevation_batches}
                seen: set[str] = set()
                for failure in failed_regions:
                    if not isinstance(failure, dict):
                        raise ValueError("Invalid region failure")
                    region_id = _text(failure.get("region_id"), "region_id")
                    if region_id not in batches or region_id in seen:
                        raise ValueError("Unknown or duplicate failed region")
                    seen.add(region_id)
                    batch = batches[region_id]
                    reason = _text(failure.get("reason"), "reason")[:2000]
                    failures.append(DtsLiveRegionFailure(region_id, batch.name, reason))
                    excluded.update(range(batch.start, batch.start + batch.count))
            if len(positions) != len(operation.point_ids):
                raise ValueError(
                    "DTS intersection count differs from frozen Point IDs"
                )
            if any(position is None and i not in excluded for i, position in enumerate(positions)):
                raise ValueError("DTS elevation requires every point to hit")
            samples = tuple(
                DtsElevationSample(point_id=point_id, position=None if i in excluded else position)
                for i, (point_id, position) in enumerate(zip(
                    operation.point_ids,
                    positions,
                    strict=True,
                ))
            )
            operation.elevation = samples
            operation.sampling_failures = tuple(failures)
            operation.elevation_chunks.clear()
            operation.pending = None
            operation.stage = DtsLiveOperationStage.ELEVATION_READY
            self._condition.notify_all()
            return samples

    def wait_for_elevation(
        self,
        correlation: DtsLiveCorrelation,
        *,
        timeout_seconds: float,
    ) -> tuple[DtsElevationSample, ...] | None:
        if (
            not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be finite and non-negative")
        with self._condition:
            operation = self._require_current(
                correlation.operation_id,
                correlation.generation_token,
            )
            self._require_issued(correlation, DtsLiveCommandKind.SAMPLE_ELEVATION)
            if operation.stage is DtsLiveOperationStage.ELEVATION_READY:
                return operation.elevation
            if operation.stage is not DtsLiveOperationStage.ELEVATION_PENDING:
                raise ValueError("DTS Live elevation was cancelled or superseded")
            ready = self._condition.wait_for(
                lambda: self._elevation_wait_finished(correlation),
                timeout=float(timeout_seconds),
            )
            if not ready:
                return None
            operation = self._require_current(
                correlation.operation_id,
                correlation.generation_token,
            )
            if operation.stage is DtsLiveOperationStage.ELEVATION_READY:
                return operation.elevation
            raise ValueError(
                "DTS Live elevation was cancelled or superseded"
            )

    def request_delivery(
        self,
        *,
        operation_id: str,
        generation_token: int,
        export_id: str,
        xml_path: str,
    ) -> DtsLiveCommand:
        export = _text(export_id, "export_id")
        path = _text(xml_path, "xml_path")
        with self._condition:
            operation = self._require_current(operation_id, generation_token)
            if operation.stage is not DtsLiveOperationStage.ELEVATION_READY:
                raise ValueError("DTS Live operation is not ready for delivery")
            operation.export_id = export
            operation.stage = DtsLiveOperationStage.DELIVERY_PENDING
            command = self._queue(
                operation,
                DtsLiveCommandKind.IMPORT_XML,
                {"export_id": export, "xml_path": path},
            )
            self._condition.notify_all()
            return command

    def accept_delivery(
        self,
        correlation: DtsLiveCorrelation,
        *,
        export_id: str,
        succeeded: bool,
        message: str | None = None,
    ) -> DtsLiveDeliveryReceipt:
        export = _text(export_id, "export_id")
        if not isinstance(succeeded, bool):
            raise ValueError("succeeded must be a boolean")
        with self._condition:
            operation = self._require_pending(
                correlation,
                DtsLiveCommandKind.IMPORT_XML,
            )
            if export != operation.export_id:
                raise ValueError("Delivery receipt Export differs from current operation")
            receipt = DtsLiveDeliveryReceipt(
                correlation=correlation,
                export_id=export,
                succeeded=succeeded,
                message=message.strip() if isinstance(message, str) and message.strip() else None,
            )
            operation.receipt = receipt
            operation.pending = None
            operation.stage = (
                DtsLiveOperationStage.COMPLETED
                if succeeded
                else DtsLiveOperationStage.FAILED
            )
            operation.error = None if succeeded else (receipt.message or "DTS import failed")
            self._condition.notify_all()
            return receipt

    def wait_for_delivery(
        self,
        correlation: DtsLiveCorrelation,
        *,
        timeout_seconds: float,
    ) -> DtsLiveDeliveryReceipt | None:
        if (
            not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be finite and non-negative")
        with self._condition:
            operation = self._require_current(
                correlation.operation_id,
                correlation.generation_token,
            )
            self._require_issued(correlation, DtsLiveCommandKind.IMPORT_XML)
            if operation.stage in {
                DtsLiveOperationStage.COMPLETED,
                DtsLiveOperationStage.FAILED,
            } and operation.receipt is not None:
                return operation.receipt
            if operation.stage is not DtsLiveOperationStage.DELIVERY_PENDING:
                raise ValueError("DTS Live delivery was cancelled or superseded")
            ready = self._condition.wait_for(
                lambda: self._delivery_wait_finished(correlation),
                timeout=float(timeout_seconds),
            )
            if not ready:
                return None
            operation = self._require_current(
                correlation.operation_id,
                correlation.generation_token,
            )
            if operation.stage in {
                DtsLiveOperationStage.COMPLETED,
                DtsLiveOperationStage.FAILED,
            } and operation.receipt is not None:
                return operation.receipt
            raise ValueError("DTS Live delivery was cancelled or superseded")

    def cancel_current(self) -> DtsLiveSessionSnapshot:
        with self._condition:
            if self._operation is not None and self._operation.stage not in {
                DtsLiveOperationStage.COMPLETED,
                DtsLiveOperationStage.CANCELLED,
                DtsLiveOperationStage.SUPERSEDED,
                DtsLiveOperationStage.FAILED,
            }:
                self._operation.stage = DtsLiveOperationStage.CANCELLED
                self._operation.pending = None
                self._operation.elevation_chunks.clear()
                self._condition.notify_all()
            return self.snapshot()

    def fail_current(
        self,
        operation_id: str,
        generation_token: int,
        message: str,
    ) -> DtsLiveSessionSnapshot:
        failure = _text(message, "message")
        with self._condition:
            operation = self._require_current(operation_id, generation_token)
            if operation.stage not in {
                DtsLiveOperationStage.COMPLETED,
                DtsLiveOperationStage.CANCELLED,
                DtsLiveOperationStage.SUPERSEDED,
            }:
                operation.stage = DtsLiveOperationStage.FAILED
                operation.pending = None
                operation.elevation_chunks.clear()
                operation.error = failure
                self._condition.notify_all()
            return self.snapshot()

    def commands_after(
        self,
        sequence: int,
        *,
        timeout_seconds: float = 0.0,
    ) -> tuple[DtsLiveCommand, ...]:
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(
            float(timeout_seconds)
        ) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be a finite non-negative number")
        with self._condition:
            if timeout_seconds:
                self._condition.wait_for(
                    lambda: any(item.sequence > sequence for item in self._commands),
                    timeout=float(timeout_seconds),
                )
            return tuple(item for item in self._commands if item.sequence > sequence)

    def snapshot(self) -> DtsLiveSessionSnapshot:
        with self._condition:
            operation = self._operation
            return DtsLiveSessionSnapshot(
                connector_session_id=self.connector_session_id,
                generation_token=self._generation_token,
                operation_id=operation.operation_id if operation else None,
                stage=operation.stage if operation else None,
                execution=operation.execution if operation else None,
                pending_request_id=(
                    operation.pending.correlation.request_id
                    if operation and operation.pending
                    else None
                ),
                pending_command_kind=(
                    operation.pending.kind
                    if operation and operation.pending
                    else None
                ),
                selection_coordinate_count=(
                    sum(len(region.coordinates) for region in operation.selection.regions)
                    if operation and operation.selection
                    else 0
                ),
                elevation_point_count=(
                    len(operation.elevation) if operation else 0
                ),
                export_id=operation.export_id if operation else None,
                error=operation.error if operation else None,
                selection_regions=(
                    operation.selection.regions
                    if operation and operation.selection else ()
                ),
                sampling_failures=operation.sampling_failures if operation else (),
            )

    def _queue(
        self,
        operation: _Operation,
        kind: DtsLiveCommandKind,
        payload: dict[str, Any],
    ) -> DtsLiveCommand:
        if operation.pending is not None:
            raise ValueError("DTS Live operation already has a pending command")
        correlation = DtsLiveCorrelation(
            connector_session_id=self.connector_session_id,
            operation_id=operation.operation_id,
            generation_token=operation.generation_token,
            request_id=uuid4().hex,
        )
        command = DtsLiveCommand(
            sequence=self._next_sequence,
            kind=kind,
            correlation=correlation,
            payload=payload,
        )
        self._next_sequence += 1
        operation.pending = command
        self._commands.append(command)
        return command

    def _elevation_wait_finished(
        self,
        correlation: DtsLiveCorrelation,
    ) -> bool:
        operation = self._operation
        return (
            operation is None
            or operation.operation_id != correlation.operation_id
            or operation.generation_token != correlation.generation_token
            or operation.stage is not DtsLiveOperationStage.ELEVATION_PENDING
            or operation.pending is None
            or operation.pending.correlation.request_id
            != correlation.request_id
        )

    def _delivery_wait_finished(
        self,
        correlation: DtsLiveCorrelation,
    ) -> bool:
        operation = self._operation
        return (
            operation is None
            or operation.operation_id != correlation.operation_id
            or operation.generation_token != correlation.generation_token
            or operation.stage
            is not DtsLiveOperationStage.DELIVERY_PENDING
        )

    def _require_pending(
        self,
        correlation: DtsLiveCorrelation,
        kind: DtsLiveCommandKind,
    ) -> _Operation:
        operation = self._require_current(
            correlation.operation_id,
            correlation.generation_token,
        )
        if correlation.connector_session_id != self.connector_session_id:
            raise ValueError("DTS callback Session is stale")
        pending = operation.pending
        if pending is None or pending.kind is not kind:
            raise ValueError("DTS callback does not match a pending command")
        if pending.correlation.request_id != correlation.request_id:
            raise ValueError("DTS callback request is stale")
        return operation

    def _require_issued(
        self,
        correlation: DtsLiveCorrelation,
        kind: DtsLiveCommandKind,
    ) -> None:
        if correlation.connector_session_id != self.connector_session_id:
            raise ValueError("DTS callback belongs to another session")
        if not any(
            command.kind is kind and command.correlation == correlation
            for command in reversed(self._commands)
        ):
            raise ValueError("DTS callback request was not issued by this session")

    def _require_current(
        self,
        operation_id: str,
        generation_token: int,
    ) -> _Operation:
        operation = self._operation
        if operation is None:
            raise ValueError("DTS Live operation is unavailable")
        if operation.operation_id != operation_id:
            raise ValueError("DTS callback operation is stale")
        if operation.generation_token != generation_token:
            raise ValueError("DTS callback generation is stale")
        return operation


def normalize_live_regions(value: object) -> tuple[DtsLiveRegion, ...]:
    """Validate and freeze drawn faces without starting a planting operation."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("DTS selection requires at least one region")
    regions: list[DtsLiveRegion] = []
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("DTS region must be an object")
        region_id = _text(item.get("region_id"), "region_id")
        if region_id in ids:
            raise ValueError("DTS region IDs must be unique")
        ids.add(region_id)
        coordinates = _polygon_coordinates(item.get("coordinates"))
        bounds = tuple(min(p[i] for p in coordinates) for i in range(3)) + tuple(
            max(p[i] for p in coordinates) for i in range(3)
        )
        regions.append(DtsLiveRegion(
            region_id, _text(item.get("name"), "name"), coordinates, bounds,
        ))
    return tuple(regions)


def _polygon_coordinates(value: object) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("DTS selection coordinates must be an array")
    points = tuple(_xyz(item, "selection coordinate") for item in value)
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or len(set(points)) < 3:
        raise ValueError("DTS selection requires at least three unique coordinates")
    x0, y0, _ = points[0]
    area = sum(
        (a[0] - x0) * (b[1] - y0) - (b[0] - x0) * (a[1] - y0)
        for a, b in zip(points, (*points[1:], points[0]))
    )
    if not math.isfinite(area) or abs(area) <= 1e-10:
        raise ValueError("DTS selection must have positive XY area")
    return points


def _line(value: object) -> dict[str, list[float]]:
    if not isinstance(value, dict):
        raise ValueError("DTS elevation line must be an object")
    return {
        "start": list(_xyz(value.get("start"), "line start")),
        "end": list(_xyz(value.get("end"), "line end")),
    }


def _intersection_positions(
    value: object,
) -> Iterable[tuple[float, float, float] | None]:
    value = _unwrap_intersections(value)
    if not isinstance(value, list):
        raise ValueError("DTS intersections must decode to an array")
    for item in value:
        candidate = (item[0] if item else None) if isinstance(item, list) else item
        if candidate is None:
            yield None
            continue
        if not isinstance(candidate, dict):
            raise ValueError("DTS intersection row is invalid")
        point = candidate.get(
            "LineIntersectPoint",
            candidate.get("lineIntersectPoint"),
        )
        if point is None:
            yield None
            continue
        try:
            yield _xyz(point, "intersection position")
        except ValueError:
            yield None


def _unwrap_intersections(value: object) -> object:
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError("DTS intersections are not valid JSON") from error
            continue
        if not isinstance(value, dict):
            return value
        for key in (
            "intersects",
            "LineIntersectPoints",
            "items",
            "result",
            "data",
        ):
            if key in value:
                value = value[key]
                break
        else:
            return value
    return value


def _xyz(value: object, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must contain three coordinates")
    result: list[float] = []
    for item in value:
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
        ):
            raise ValueError(f"{label} must contain finite numbers")
        result.append(float(item))
    return result[0], result[1], result[2]


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
