"""Ephemeral CoreHost runtime for DTS single-region Fast Path operations."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import time

from verdflow.contracts import JobStatus, SourceKind
from verdflow.core import CommandDocument, RegionExtractionRequest
from verdflow.core_host import (
    CoreHost,
    CoreHostConfig,
    GenerateAndAssignRequest,
    PlantRunTimeouts,
    ResolveElevationAndExportRequest,
)
from verdflow.dts_adapter import (
    DTS_ADAPTER_ID,
    DTS_LIVE_ADAPTER_ID,
    DTS_LIVE_SCENE_INSPECTION_COMMAND,
    DtsLiveCorrelation,
    DtsLiveSelection,
)
from verdflow.project_store import ProjectStorageProfile

from .dts_live_connector import DtsLiveSessionNotFoundError, DtsLiveTrustedSessionPort
from .dts_live_extension import DtsLiveCoreHostExtensionFactory
from .runtime import ProjectRuntime


@dataclass(frozen=True, slots=True)
class DtsLivePreparedScene:
    source_id: str
    scene_revision_id: str


@dataclass(frozen=True, slots=True)
class DtsLiveOperationOutcome:
    operation_id: str
    generation_token: int
    region_set_id: str
    generated_point_set_id: str
    elevation_point_set_id: str
    asset_assignment_set_id: str
    export_id: str
    record_count: int


class DtsLiveTemporaryRuntime:
    """Own one unregistered ProjectRuntime for one Connector session."""

    def __init__(
        self,
        *,
        connector_session_id: str,
        project_path: Path,
        library_path: Path,
        port: DtsLiveTrustedSessionPort,
        idle_cycle_seconds: float,
        callback_poll_seconds: float = 1.0,
        delivery_timeout_seconds: float | None = None,
    ) -> None:
        if delivery_timeout_seconds is not None and (
            not math.isfinite(delivery_timeout_seconds)
            or delivery_timeout_seconds <= 0
        ):
            raise ValueError("delivery_timeout_seconds must be positive")
        self.connector_session_id = connector_session_id
        self.project_path = project_path.resolve()
        self.port = port
        self.delivery_timeout_seconds = delivery_timeout_seconds
        self._prepared: DtsLivePreparedScene | None = None
        self._runtime = ProjectRuntime(
            CoreHostConfig(
                project_path=self.project_path,
                project_storage_profile=ProjectStorageProfile.EPHEMERAL,
                enable_blender_worker=False,
                enable_project_maintenance=False,
                runtime_root=self.project_path / "runtime",
            ),
            library_path=library_path,
            create_name=f"DTS Live {connector_session_id[:8]}",
            idle_cycle_seconds=idle_cycle_seconds,
            extension_factories=(
                DtsLiveCoreHostExtensionFactory(
                    port,
                    callback_poll_seconds=callback_poll_seconds,
                ),
            ),
        )

    def start(self, timeout_seconds: float) -> DtsLivePreparedScene:
        self._runtime.start(timeout_seconds)
        prepared = self._runtime.call(
            lambda host: _prepare_scene(host, self.connector_session_id),
            timeout_seconds=timeout_seconds,
        )
        self._prepared = prepared
        return prepared

    def submit(
        self,
        selection: DtsLiveSelection,
    ) -> Future[DtsLiveOperationOutcome]:
        prepared = self._prepared
        if prepared is None:
            raise RuntimeError("DTS Live temporary runtime is not prepared")
        if (
            selection.correlation.connector_session_id
            != self.connector_session_id
        ):
            raise ValueError("DTS Live selection belongs to another session")

        def execute(host: CoreHost) -> DtsLiveOperationOutcome:
            try:
                return _execute_operation(
                    host,
                    self.port,
                    prepared,
                    selection,
                    delivery_timeout_seconds=self.delivery_timeout_seconds,
                )
            except BaseException as error:
                self.port.fail(
                    selection.correlation.operation_id,
                    selection.correlation.generation_token,
                    str(error) or type(error).__name__,
                )
                raise

        return self._runtime.submit(execute)

    def close(self, timeout_seconds: float, *, remove_files: bool = True) -> None:
        try:
            self.port.cancel()
        except DtsLiveSessionNotFoundError:
            pass
        self._runtime.close(timeout_seconds)
        if remove_files and self.project_path.exists():
            shutil.rmtree(self.project_path)


def _prepare_scene(
    host: CoreHost,
    connector_session_id: str,
) -> DtsLivePreparedScene:
    reference = {
        "schema": "verdflow.dts-live-scene-reference",
        "version": "1",
        "connector_session_id": connector_session_id,
        "coordinate_space": "dts_scene_world",
        "offset_applied": False,
    }
    source = host.add_source_bytes(
        _json_bytes(reference),
        original_name="dts-live-scene-reference.json",
        kind=SourceKind.OTHER,
        media_type="application/vnd.verdflow.dts-live-scene-reference+json",
    )
    command = {
        "schema": "verdflow.dts-live.command",
        "version": "1",
        "operation": "inspect_scene",
        "source_id": source.source_id,
        "context": {"connector_session_id": connector_session_id},
    }
    submitted = host.submit_extension_command(
        CommandDocument(
            command_type=DTS_LIVE_SCENE_INSPECTION_COMMAND,
            command_version="1",
            content=_json_bytes(command),
            schema_id="verdflow.dts-live.command",
            schema_version="1",
        ),
        idempotency_key=f"dts-live-scene:{connector_session_id}:v1",
    )
    completed = host.service_loop.run_until_terminal(
        submitted.job_id,
        timeout_seconds=30.0,
    )
    if completed.status is not JobStatus.SUCCEEDED:
        message = completed.error.message if completed.error else completed.status.value
        raise RuntimeError(f"DTS Live scene preparation failed: {message}")
    scene = host.finalize_scene_inspection(completed.job_id)
    return DtsLivePreparedScene(source.source_id, scene.scene_revision_id)


def _execute_operation(
    host: CoreHost,
    port: DtsLiveTrustedSessionPort,
    prepared: DtsLivePreparedScene,
    selection: DtsLiveSelection,
    *,
    delivery_timeout_seconds: float | None,
) -> DtsLiveOperationOutcome:
    correlation = selection.correlation
    execution = port.execution(
        correlation.operation_id,
        correlation.generation_token,
    )
    context = {
        "connector_session_id": correlation.connector_session_id,
        "operation_id": correlation.operation_id,
        "generation_token": correlation.generation_token,
    }
    regions = host.extract_regions(
        RegionExtractionRequest(
            scene_revision_id=prepared.scene_revision_id,
            provider_id=DTS_LIVE_ADAPTER_ID,
            rule_pack={
                "schema": "verdflow.region-rule-pack",
                "version": "1",
                "rules": [
                    {
                        "id": "dts-live-user-selection",
                        "label": "DTS Live user selection",
                        "match": {"selection": "polygon_collection"},
                    }
                ],
            },
            import_settings=context,
        ),
        idempotency_key=(
            f"dts-live-region:{correlation.operation_id}:"
            f"{correlation.generation_token}"
        ),
        timeout_seconds=30.0,
    )
    region_ids = tuple(
        item.region_id
        for item in host.store.list_regions(regions.region_set.region_set_id)
    )
    prepared_run = host.generate_and_assign(
        GenerateAndAssignRequest(
            scene_revision_id=prepared.scene_revision_id,
            region_set_id=regions.region_set.region_set_id,
            selected_region_ids=region_ids,
            strategy_package_hash=execution.strategy_package_hash,
            random_seed=(
                execution.random_seed
                if execution.random_seed is not None
                else correlation.generation_token
            ),
            target_type=execution.target_type,
            catalog_id=(
                execution.asset_source_id
                if execution.asset_source_kind == "catalog"
                else None
            ),
            profile_id=execution.profile_id,
            binding_overrides=execution.binding_overrides or {},
            generation_args=execution.generation_args or {},
        ),
        timeouts=PlantRunTimeouts(assignment_seconds=3600),
    )
    binding = host.store.get_effective_binding_snapshot(
        prepared_run.assignment.assignments.effective_binding_snapshot_id
    )
    if binding.semantic_hash != execution.effective_binding_semantic_hash:
        raise RuntimeError("DTS Live effective binding changed after selection")
    resolved = host.resolve_elevation_and_export(
        ResolveElevationAndExportRequest(
            generated_point_set_id=(
                prepared_run.generation.point_set.point_set_id
            ),
            asset_assignment_set_id=(
                prepared_run.assignment.assignments.asset_assignment_set_id
            ),
            elevation_provider_id=DTS_LIVE_ADAPTER_ID,
            export_provider_id=DTS_ADAPTER_ID,
            offset=(0.0, 0.0, 0.0),
            elevation_options={**context, "ray_half_range": 5000.0},
            require_terrain_hit=True,
        ),
        timeouts=PlantRunTimeouts(elevation_seconds=7 * 24 * 3600, export_seconds=3600),
    )
    export = resolved.output.export
    xml_path = _materialize_export(host, export.export_id, export.output_artifact_id)
    delivery = port.request_delivery(
        operation_id=correlation.operation_id,
        generation_token=correlation.generation_token,
        export_id=export.export_id,
        xml_path=str(xml_path),
    )
    receipt = _wait_for_delivery(
        port,
        delivery.correlation,
        timeout_seconds=delivery_timeout_seconds,
    )
    if not receipt.succeeded:
        raise RuntimeError(receipt.message or "DTS scene import failed")
    return DtsLiveOperationOutcome(
        operation_id=correlation.operation_id,
        generation_token=correlation.generation_token,
        region_set_id=regions.region_set.region_set_id,
        generated_point_set_id=prepared_run.generation.point_set.point_set_id,
        elevation_point_set_id=resolved.elevation.point_set.point_set_id,
        asset_assignment_set_id=(
            prepared_run.assignment.assignments.asset_assignment_set_id
        ),
        export_id=export.export_id,
        record_count=export.record_count,
    )


def _materialize_export(
    host: CoreHost,
    export_id: str,
    artifact_id: str,
) -> Path:
    delivery_root = host.store.root / "runtime" / "dts-live-delivery"
    delivery_root.mkdir(parents=True, exist_ok=True)
    target = delivery_root / f"{export_id}.xml"
    temporary = target.with_suffix(".xml.tmp")
    with host.store.open_artifact(artifact_id) as source, temporary.open("wb") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    temporary.replace(target)
    return target.resolve()


def _wait_for_delivery(
    port: DtsLiveTrustedSessionPort,
    correlation: DtsLiveCorrelation,
    *,
    timeout_seconds: float | None = None,
):
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    while True:
        remaining = deadline - time.monotonic() if deadline is not None else 1.0
        if deadline is not None and remaining <= 0:
            raise TimeoutError("DTS scene import receipt timed out")
        receipt = port.wait_for_delivery(
            correlation,
            timeout_seconds=min(1.0, remaining),
        )
        if receipt is not None:
            return receipt


def _json_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
