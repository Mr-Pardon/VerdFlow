"""Finalize a direct Region source into canonical scene and region facts."""

from __future__ import annotations

import json
from typing import Any

from verdflow.contracts import (
    CoordinateReferenceKind,
    JobStatus,
    RegionSetInfo,
    SceneRevisionInfo,
)
from verdflow.project_store import ProjectStore

from .region_import import RegionSetImportError, RegionSetImportService


class RegionSourceImportError(RuntimeError):
    """Raised when a direct Region source result cannot be finalized."""


class RegionSourceImportService:
    """Create one SceneRevision and its first RegionSet from one Job."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._region_sets = RegionSetImportService(store)

    def finalize(
        self,
        job_id: str,
    ) -> tuple[SceneRevisionInfo, RegionSetInfo]:
        existing_regions = self.store.get_region_set_by_job(job_id)
        if existing_regions is not None:
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
        try:
            regions = self._region_sets.finalize_result(
                job_id=job.job_id,
                scene_revision_id=scene.scene_revision_id,
                source_id=source.source_id,
                result=result,
                command_artifact_id=job.command.artifact_id,
            )
        except RegionSetImportError as error:
            raise RegionSourceImportError(str(error)) from error
        self.store.activate_region_source_workflow(
            source_id=source.source_id,
            region_set_id=regions.region_set_id,
        )
        return scene, regions

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
