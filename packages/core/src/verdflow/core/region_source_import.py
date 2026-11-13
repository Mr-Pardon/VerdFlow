"""Finalize a direct Region source into canonical scene and region facts."""

from __future__ import annotations

from contextlib import closing, nullcontext
import errno
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory, TemporaryFile
from typing import Any, Callable, Generator, Iterator, TextIO
from uuid import uuid4

from verdflow.contracts import (
    ArtifactRef,
    CoordinateReferenceKind,
    JobCommandRef,
    JobInfo,
    JobLease,
    JobStatus,
    PlantRunExecutionStatus,
    RegionSetInfo,
    SceneRevisionInfo,
)
from verdflow.planting import canonical_json_bytes
from verdflow.project_store import ProjectQueryStore, ProjectStore

from .job_runtime import JobRuntime
from .region_boundary_spool import RegionBoundarySpool
from .region_import import RegionSetImportError, RegionSetImportService


class RegionSourceImportError(RuntimeError):
    """Raised when a direct Region source result cannot be finalized."""


REGION_SOURCE_REVISE_COMMAND = "verdflow.revise-region-source-selection"
REGION_SOURCE_CONFIRM_COMMAND = "verdflow.confirm-region-source-selection"
REGION_SOURCE_CONFIRM_ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING)
RegionSelectionRefiner = Callable[
    [dict[str, Any], dict[str, Any], Callable[[], None]], Generator[None, None, dict[str, Any]]
]
_INLINE_SELECTION_BYTES = 4 * 1024**2
_BOUNDARY_SPOOL_DOCUMENT_BYTES = 32 * 1024**2


def region_source_revision_lock(store: ProjectStore | ProjectQueryStore) -> str | None:
    if store.current_planting_composition() is not None:
        return "已有确认的种植批次，当前范围仅供回看。"
    execution_id = store.project_workflow_state().plant_run_execution_id
    if execution_id and store.get_plant_run_execution(execution_id).status in {
        PlantRunExecutionStatus.QUEUED, PlantRunExecutionStatus.RUNNING,
        PlantRunExecutionStatus.CANCEL_REQUESTED, PlantRunExecutionStatus.AWAITING_CONFIRMATION,
    }:
        return "当前有未结束的种植任务，暂不能调整提取范围。"
    return None


class RegionSourceImportService:
    """Create one SceneRevision and its first RegionSet from one Job."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._region_sets = RegionSetImportService(store)

    def submit_confirmation(
        self, runtime: JobRuntime, job_id: str, *, included_region_ids: tuple[str, ...],
        expected_region_set_id: str | None, refinement: dict[str, Any] | None = None,
    ) -> JobInfo:
        candidate = self.store.get_job(job_id)
        if candidate.status is not JobStatus.SUCCEEDED or candidate.result_artifact_id is None:
            raise RegionSourceImportError("影像候选尚未就绪。")
        if not included_region_ids:
            raise RegionSourceImportError("请至少保留一个候选区域再确认。")
        self._check_confirmation_base(job_id, expected_region_set_id)
        version = "2" if refinement is not None else "1"
        artifact = self.store.put_bytes(canonical_json_bytes({
            "schema": REGION_SOURCE_CONFIRM_COMMAND, "version": version, "candidate_job_id": job_id,
            "candidate_result_artifact_id": candidate.result_artifact_id,
            "base_region_set_id": expected_region_set_id, "included_region_ids": sorted(included_region_ids),
            **({"refinement": refinement} if refinement is not None else {}),
        }), media_type="application/vnd.verdflow.command+json", schema_id=REGION_SOURCE_CONFIRM_COMMAND, schema_version=version)
        active = self.store.list_jobs(command_types=(REGION_SOURCE_CONFIRM_COMMAND,),
            statuses=REGION_SOURCE_CONFIRM_ACTIVE, limit=1).items
        if active:
            if active[0].command.artifact_id == artifact.artifact_id:
                return active[0]
            raise RegionSourceImportError("已有区域确认任务正在执行。")
        # Terminal attempts must not be reused, even when their timestamps tie.
        return runtime.submit(JobCommandRef(REGION_SOURCE_CONFIRM_COMMAND, version, artifact.artifact_id),
            idempotency_key=f"{REGION_SOURCE_CONFIRM_COMMAND}:{uuid4().hex}",
            max_attempts=2)

    def _check_confirmation_base(self, job_id: str, expected_region_set_id: str | None) -> None:
        if (self.store.project_workflow_state().region_set_id != expected_region_set_id
                or (expected_region_set_id is None and self.store.get_region_set_by_job(job_id) is not None)):
            raise RegionSourceImportError("当前提取范围已更新，请重新打开影像阶段。")
        lock = region_source_revision_lock(self.store)
        if lock:
            raise RegionSourceImportError(lock)

    def run_confirmation(
        self, runtime: JobRuntime, lease: JobLease, check_progress: Callable[[], None],
        refiner: RegionSelectionRefiner | None = None,
    ) -> Generator[None, None, JobInfo]:
        reference = runtime.get(lease.job_id).command
        command = self._read_json(reference.artifact_id, "Region confirmation command")
        job_id = self._required_text(command, "candidate_job_id")
        candidate = self.store.get_job(job_id)
        if (command.get("schema") != REGION_SOURCE_CONFIRM_COMMAND or command.get("version") != reference.command_version
                or reference.command_version not in {"1", "2"}
                or candidate.status is not JobStatus.SUCCEEDED or candidate.result_artifact_id is None
                or candidate.result_artifact_id != command.get("candidate_result_artifact_id")):
            raise RegionSourceImportError("影像候选已过期，请重新加载当前任务。")
        expected = command.get("base_region_set_id")
        if expected is not None and not isinstance(expected, str):
            raise RegionSourceImportError("区域确认任务的数据无效。")
        included = command.get("included_region_ids")
        if not isinstance(included, list) or not included or not all(isinstance(value, str) for value in included):
            raise RegionSourceImportError("请至少保留一个候选区域再确认。")
        self._check_confirmation_base(job_id, expected)
        result_ref = self.store.get_artifact(candidate.result_artifact_id)
        result = self._read_json(result_ref.artifact_id, "Candidate result")
        if result.get("result_kind") != "region_source":
            raise RegionSourceImportError("当前候选不能确认种植范围。")
        selected = self._select_candidate_regions(result, self._outputs_by_role(result), included, check_progress=check_progress)
        if reference.command_version == "2":
            options = command.get("refinement")
            if refiner is None or not isinstance(options, dict):
                raise RegionSourceImportError("当前工作台无法应用影像细化。")
            runtime.heartbeat(lease, progress=0.2, stage="refining_imagery_regions")
            selected = yield from refiner(selected, options, check_progress)
            selected = {**selected, "job_id": lease.job_id, "attempt_number": lease.attempt_count}
        check_progress()
        runtime.heartbeat(lease, progress=0.75, stage="publishing_region_selection")
        with self.store.atomic_write():
            self._check_confirmation_base(job_id, expected)
            if expected is None:
                producer = None
                if reference.command_version == "2":
                    published = self.store.put_bytes(canonical_json_bytes(selected), media_type=result_ref.media_type,
                        schema_id=result_ref.schema_id, schema_version=result_ref.schema_version)
                    producer = runtime.succeed(lease, result_artifact_id=published.artifact_id)
                self._finalize(job_id, included_region_ids=included, selected_result=selected,
                    check_progress=check_progress, confirmation_job_id=lease.job_id, region_producer=producer)
            else:
                self._revise_selection(job_id, expected_region_set_id=expected, included_region_ids=included,
                    runtime=runtime, selected_result=selected, check_progress=check_progress, publication_lease=lease)
            check_progress()
            confirmation = runtime.get(lease.job_id)
            if confirmation.status is JobStatus.SUCCEEDED:
                return confirmation
            published = self.store.put_bytes(canonical_json_bytes(selected), media_type=result_ref.media_type,
                schema_id=result_ref.schema_id, schema_version=result_ref.schema_version)
            check_progress()
            return runtime.succeed(lease, result_artifact_id=published.artifact_id)

    def complete_and_publish(
        self,
        runtime: JobRuntime,
        lease: JobLease,
        result_artifact_id: str,
        check_cancelled: Callable[[], None],
    ) -> JobInfo:
        """Publish successful preparation and its workflow facts together."""
        with self.store.atomic_write():
            check_cancelled()
            if self.store.project_workflow_state().region_set_id is not None:
                raise RegionSourceImportError("项目已有定版区域，不能覆盖现有种植范围。")
            job = runtime.succeed(lease, result_artifact_id=result_artifact_id)
            self.finalize(job.job_id)
            check_cancelled()
        return job

    def revise_selection(
        self, job_id: str, *, expected_region_set_id: str, included_region_ids: tuple[str, ...], runtime: JobRuntime,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        with self.store.atomic_write():
            return self._revise_selection(
                job_id, expected_region_set_id=expected_region_set_id,
                included_region_ids=included_region_ids, runtime=runtime,
            )

    def _revise_selection(
        self, job_id: str, *, expected_region_set_id: str, included_region_ids: tuple[str, ...] | list[str], runtime: JobRuntime,
        selected_result: dict[str, Any] | None = None, check_progress: Callable[[], None] | None = None,
        publication_lease: JobLease | None = None,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        """Derive an immutable RegionSet from the same successful candidate Job."""
        workflow = self.store.project_workflow_state()
        if workflow.region_set_id != expected_region_set_id:
            raise RegionSourceImportError("当前提取范围已更新，请重新打开影像阶段。")
        base = self.store.get_region_set(expected_region_set_id)
        scene = self.store.get_scene_revision(base.scene_revision_id)
        manifest = self._read_json(scene.scene_manifest_artifact_id, "Scene manifest")
        if workflow.source_id != scene.source_id or manifest.get("producer_job_id") != job_id:
            raise RegionSourceImportError("当前提取范围不属于此候选任务。")
        included = set(included_region_ids)
        if not included or len(included) != len(included_region_ids):
            raise RegionSourceImportError("请至少保留一个区域，且不能重复选择。")
        if (selected_result is None and len(included) == base.region_count
                and included == {region.region_id for region in self.store.list_regions(base.region_set_id)}):
            return scene, base
        lock = region_source_revision_lock(self.store)
        if lock:
            raise RegionSourceImportError(lock)
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED or job.result_artifact_id is None:
            raise RegionSourceImportError("影像候选尚未就绪。")
        candidate_result = self.store.get_artifact(job.result_artifact_id)
        result = self._read_json(candidate_result.artifact_id, "Candidate result")
        if result.get("result_kind") != "region_source" or result.get("source_id") != scene.source_id:
            raise RegionSourceImportError("候选数据与当前场景不一致。")
        selected = selected_result if selected_result is not None else self._select_candidate_regions(
            result, self._outputs_by_role(result), included_region_ids,
        )
        result_ref = self.store.put_bytes(canonical_json_bytes(selected), media_type=candidate_result.media_type,
            schema_id=candidate_result.schema_id, schema_version=candidate_result.schema_version)
        if publication_lease is not None:
            revision = runtime.succeed(publication_lease, result_artifact_id=result_ref.artifact_id)
        else:
            command = self.store.put_bytes(canonical_json_bytes({
                "schema": REGION_SOURCE_REVISE_COMMAND, "version": "1", "candidate_job_id": job_id,
                "candidate_result_artifact_id": job.result_artifact_id, "base_region_set_id": base.region_set_id,
                "scene_revision_id": scene.scene_revision_id, "included_region_ids": sorted(included),
            }), media_type="application/vnd.verdflow.command+json", schema_id=REGION_SOURCE_REVISE_COMMAND, schema_version="1")
            revision = runtime.submit(JobCommandRef(REGION_SOURCE_REVISE_COMMAND, "1", command.artifact_id),
                idempotency_key=REGION_SOURCE_REVISE_COMMAND + ":" + command.content_hash, max_attempts=2)
            if revision.status is JobStatus.QUEUED:
                claimed = runtime.claim_next("core-region-source", only_job_id=revision.job_id)
                if claimed is not None:
                    revision = runtime.succeed(claimed.lease, result_artifact_id=result_ref.artifact_id)
        if revision.status is not JobStatus.SUCCEEDED or revision.result_artifact_id != result_ref.artifact_id:
            raise RegionSourceImportError("暂未完成范围修订，请稍后重试。")
        regions = self._region_sets.finalize_result(job_id=revision.job_id, scene_revision_id=scene.scene_revision_id,
            source_id=scene.source_id, result=selected, command_artifact_id=revision.command.artifact_id,
            check_progress=check_progress)
        self.store.activate_region_source_workflow(source_id=scene.source_id, region_set_id=regions.region_set_id)
        return scene, regions

    def finalize(
        self, job_id: str, *, included_region_ids: tuple[str, ...] | None = None,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        with self.store.atomic_write():
            return self._finalize(job_id, included_region_ids=included_region_ids)

    def _finalize(
        self,
        job_id: str,
        *,
        included_region_ids: tuple[str, ...] | list[str] | None = None,
        selected_result: dict[str, Any] | None = None,
        check_progress: Callable[[], None] | None = None,
        confirmation_job_id: str | None = None,
        region_producer: JobInfo | None = None,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        existing_regions = self.store.get_region_set_by_job(job_id)
        if existing_regions is not None:
            if included_region_ids is not None and set(included_region_ids) != {
                row.region_id for row in self.store.list_regions(existing_regions.region_set_id)
            }:
                raise RegionSourceImportError("该候选已确认，不能更改定版的区域范围。")
            scene = self.store.get_scene_revision(
                existing_regions.scene_revision_id
            )
            workflow = self.store.project_workflow_state()
            if workflow.source_id is None or (
                workflow.source_id == scene.source_id
                and workflow.region_set_id == existing_regions.region_set_id
            ):
                self.store.activate_region_source_workflow(
                    source_id=scene.source_id,
                    region_set_id=existing_regions.region_set_id,
                )
            return scene, existing_regions
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise RegionSourceImportError("Region source Job has not succeeded")
        if job.result_artifact_id is None:
            raise RegionSourceImportError("Region source Job has no result")
        command = self._read_json(job.command.artifact_id, "Region source command")
        result = self._read_json(job.result_artifact_id, "Worker result")
        if result.get("result_kind") != "region_source":
            raise RegionSourceImportError(
                "Only region_source results can create direct Regions"
            )

        source_id = self._required_text(command, "source_id")
        if self._required_text(result, "source_id") != source_id:
            raise RegionSourceImportError("Region source result Source differs")
        source = self.store.get_source(source_id)
        outputs = self._outputs_by_role(result)
        source_manifest_id = self._required_output(outputs, "source_manifest")
        source_manifest = self._read_json(
            source_manifest_id,
            "Region source manifest",
        )
        if (
            source_manifest.get("schema") != "verdflow.region-source-manifest"
            or str(source_manifest.get("version")) != "1"
        ):
            raise RegionSourceImportError("Region source manifest is unsupported")
        if self._required_text(source_manifest, "source_id") != source_id:
            raise RegionSourceImportError("Region source manifest Source differs")
        coordinate_frame = source_manifest.get("coordinate_frame")
        if not isinstance(coordinate_frame, dict):
            raise RegionSourceImportError("coordinate_frame is required")
        if coordinate_frame.get("offset_applied") is not False:
            raise RegionSourceImportError("Region source coordinates must be unshifted")
        local_origin = coordinate_frame.get("local_origin")
        if (
            not isinstance(local_origin, list)
            or len(local_origin) != 3
            or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in local_origin
            )
        ):
            raise RegionSourceImportError("local_origin must contain three numbers")
        producer = result.get("producer")
        if not isinstance(producer, dict):
            raise RegionSourceImportError("Worker result lacks producer provenance")
        if selected_result is not None:
            result = selected_result
        elif included_region_ids is not None:
            result = self._select_candidate_regions(result, outputs, included_region_ids)

        canonical_manifest = {
            "schema": "verdflow.scene-manifest",
            "version": "1",
            "source": {
                "source_id": source.source_id,
                "kind": source.kind.value,
                "artifact_id": source.artifact_id,
                "content_hash": source.content_hash,
                "original_name": source.original_name,
                "members": [
                    {
                        "source_member_id": member.source_member_id,
                        "source_id": member.source_id,
                        "source_content_hash": member.source_content_hash,
                        "ordinal": member.ordinal,
                        "source_to_scene_matrix_row_major": list(
                            member.source_to_scene_matrix_row_major
                        ),
                    }
                    for member in self.store.list_source_assembly_members(
                        source.source_id
                    )
                ],
            },
            "producer_job_id": job.job_id,
            "importer": producer,
            "coordinate_frame": coordinate_frame,
            "facts": {
                "region_source_manifest_artifact_id": source_manifest_id,
            },
        }
        if confirmation_job_id is not None:
            canonical_manifest["facts"]["region_confirmation_job_id"] = confirmation_job_id
        elif included_region_ids is not None:
            canonical_manifest["facts"]["included_region_ids"] = sorted(included_region_ids)
        manifest_artifact = self.store.put_bytes(
            json.dumps(
                canonical_manifest,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.scene-manifest+json",
            schema_id="verdflow.scene-manifest",
            schema_version="1",
        )
        scene = self.store.find_scene_revision_by_manifest(
            manifest_artifact.artifact_id
        )
        if scene is None:
            frame = self.store.create_coordinate_frame(
                name=self._required_text(coordinate_frame, "name"),
                handedness=self._required_text(coordinate_frame, "handedness"),
                up_axis=self._required_text(coordinate_frame, "up_axis"),
                linear_unit=self._required_text(coordinate_frame, "linear_unit"),
                crs_kind=CoordinateReferenceKind(
                    self._required_text(coordinate_frame, "crs_kind")
                ),
                crs_definition=(
                    str(coordinate_frame["crs_definition"])
                    if coordinate_frame.get("crs_definition")
                    else None
                ),
                local_origin_x=float(local_origin[0]),
                local_origin_y=float(local_origin[1]),
                local_origin_z=float(local_origin[2]),
            )
            scene = self.store.create_scene_revision(
                source_id=source.source_id,
                importer_id=self._required_text(producer, "id"),
                importer_version=self._required_text(producer, "version"),
                coordinate_frame_id=frame.coordinate_frame_id,
                scene_manifest_artifact_id=manifest_artifact.artifact_id,
            )
        else:
            self.store.set_active_scene_revision(scene.scene_revision_id)
        region_job = region_producer or job
        try:
            regions = self._region_sets.finalize_result(
                job_id=region_job.job_id,
                scene_revision_id=scene.scene_revision_id,
                source_id=source.source_id,
                result=result,
                command_artifact_id=region_job.command.artifact_id,
                check_progress=check_progress,
            )
        except RegionSetImportError as error:
            raise RegionSourceImportError(str(error)) from error
        self.store.activate_region_source_workflow(
            source_id=source.source_id,
            region_set_id=regions.region_set_id,
        )
        return scene, regions

    def _select_candidate_regions(
        self, result: dict[str, Any], outputs: dict[str, dict[str, Any]], included: tuple[str, ...] | list[str],
        *, check_progress: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        selection = set(included)
        if not selection or len(selection) != len(included):
            raise RegionSourceImportError("请至少保留一个区域，且不能重复选择。")
        selected_outputs = dict(outputs)
        try:
            metrics = self._filter_region_artifact(
                self._required_output(outputs, "geometry_metrics"), "verdflow.region-metrics", selection, check_progress,
            )
            if metrics is None:
                # Finalization validates the unchanged geometry against the complete metric IDs.
                return result
            geometry = self._filter_region_artifact(
                self._required_output(outputs, "regions"), "verdflow.regions", selection, check_progress,
            )
        except RegionSetImportError as error:
            raise RegionSourceImportError(str(error)) from error
        except (OSError, sqlite3.Error) as error:
            if getattr(error, "errno", None) == errno.ENOSPC or getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                raise RegionSourceImportError("项目磁盘空间不足，未确认区域。原始候选已保留，释放空间后可重试。") from error
            raise
        if geometry is None:
            raise RegionSourceImportError("候选几何与指标数量不一致，未确认区域。")
        for role, filtered in (("regions", geometry), ("geometry_metrics", metrics)):
            selected_outputs[role] = {**outputs[role], "artifact_id": filtered.artifact_id,
                "content_hash": filtered.content_hash, "byte_size": filtered.byte_size}
        return {**result, "output_artifacts": list(selected_outputs.values())}

    def _filter_region_artifact(
        self, artifact_id: str, schema: str, selection: set[str], check_progress: Callable[[], None] | None,
    ) -> ArtifactRef | None:
        artifact = self.store.get_artifact(artifact_id)
        header = {"coordinate_space": "source_scene_world", "offset_applied": False, "region_count": len(selection)}
        options = dict(media_type=artifact.media_type, schema_id=artifact.schema_id, schema_version=artifact.schema_version)
        found: set[str] = set()
        total = 0

        def selected_rows(boundary: RegionBoundarySpool | None = None) -> Iterator[dict[str, Any]]:
            nonlocal total
            with closing(self._region_sets.read_rows(
                artifact_id, schema=schema, label=schema,
                read_boundary=boundary.read if boundary is not None else None,
                check_progress=check_progress,
            )) as rows:
                for row in rows:
                    if check_progress is not None and total % 256 == 0:
                        check_progress()
                    if not isinstance(row, dict):
                        raise RegionSourceImportError("候选区域数据无效，未确认区域。")
                    total += 1
                    region_id = self._required_text(row, "id")
                    if region_id in selection:
                        if region_id in found:
                            raise RegionSourceImportError("候选数据包含重复区域，未确认区域。")
                        found.add(region_id)
                        yield row
            if found != selection:
                raise RegionSourceImportError("选择包含不属于当前候选结果的区域，请重新加载。")

        if artifact.byte_size <= _INLINE_SELECTION_BYTES:
            rows = list(selected_rows())
            if total == len(selection):
                return None
            return self.store.put_bytes(_region_json({
                **header, "regions": rows, "schema": schema, "version": "1",
            }).encode("utf-8"), **options)

        with TemporaryDirectory(dir=self.store.staging_path, prefix="region-selection-") as temporary:
            path = Path(temporary) / "selected.json"
            spool = (TemporaryFile(mode="w+t", dir=temporary, encoding="utf-8", newline="")
                     if schema == "verdflow.regions" and artifact.byte_size > _BOUNDARY_SPOOL_DOCUMENT_BYTES
                     else nullcontext())
            with spool as scratch, path.open("w", encoding="utf-8") as output, closing(
                selected_rows(RegionBoundarySpool(scratch) if scratch is not None else None)
            ) as rows:
                output.write(_region_json(header)[:-1] + ', "regions": [')
                separator = ""
                for row in rows:
                    output.write(separator)
                    _write_region_row(output, row, check_progress)
                    separator = ", "
                output.write("], " + _region_json({"schema": schema, "version": "1"})[1:])
            if total == len(selection):
                return None
            return self.store.put_file(path, on_chunk=check_progress, **options)

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        with self.store.open_artifact(artifact_id) as stream:
            try:
                payload = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RegionSourceImportError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(payload, dict):
            raise RegionSourceImportError(f"{label} must be a JSON object")
        return payload

    @staticmethod
    def _outputs_by_role(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        outputs = result.get("output_artifacts")
        if not isinstance(outputs, list):
            raise RegionSourceImportError("Worker output_artifacts is required")
        by_role: dict[str, dict[str, Any]] = {}
        for output in outputs:
            if not isinstance(output, dict):
                raise RegionSourceImportError("Worker output descriptor is invalid")
            role = RegionSourceImportService._required_text(output, "role")
            if role in by_role:
                raise RegionSourceImportError(
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
            raise RegionSourceImportError(
                f"Worker result lacks output role: {role}"
            ) from error
        return RegionSourceImportService._required_text(output, "artifact_id")

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RegionSourceImportError(f"{field} is required")
        return value.strip()


def _region_json(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _write_region_row(
    output: TextIO, row: dict[str, Any], check_progress: Callable[[], None] | None,
) -> None:
    boundary = row.get("footprint_boundary_xyz")
    if not isinstance(boundary, RegionBoundarySpool):
        output.write(_region_json(row))
        return
    output.write("{")
    for index, key in enumerate(sorted(row)):
        output.write((", " if index else "") + _region_json(key) + ": ")
        if key == "footprint_boundary_xyz":
            boundary.copy_to(output, check_progress)
        else:
            output.write(_region_json(row[key]))
    output.write("}")
