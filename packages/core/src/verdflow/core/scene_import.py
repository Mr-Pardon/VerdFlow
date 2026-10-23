"""Finalize objective scene inspection outputs into canonical project state."""

from __future__ import annotations

import json
from typing import Any

from verdflow.contracts import (
    CoordinateReferenceKind,
    JobStatus,
    SceneRevisionInfo,
)
from verdflow.project_store import ProjectStore


class SceneImportError(RuntimeError):
    """Raised when a completed inspection cannot become a SceneRevision."""


class SceneImportService:
    """Validate Worker facts and create one immutable SceneRevision."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def finalize(self, job_id: str) -> SceneRevisionInfo:
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise SceneImportError("Scene inspection Job has not succeeded")
        if job.result_artifact_id is None:
            raise SceneImportError("Scene inspection Job has no result")
        result = self._read_json(job.result_artifact_id, "Worker result")
        if result.get("result_kind") != "scene_inspection":
            raise SceneImportError(
                "Only scene_inspection results can create a SceneRevision"
            )

        source_id = self._required_text(result, "source_id")
        source = self.store.get_source(source_id)
        source_members = self.store.list_source_assembly_members(
            source.source_id
        )
        outputs = self._outputs_by_role(result)
        inspection_manifest_id = self._required_output(
            outputs,
            "scene_manifest",
        )
        fact_report_id = self._required_output(
            outputs,
            "material_fact_report",
        )
        inspection_manifest = self._read_json(
            inspection_manifest_id,
            "Scene inspection manifest",
        )
        if (
            inspection_manifest.get("schema")
            != "verdflow.scene-inspection-manifest"
        ):
            raise SceneImportError(
                "Scene inspection manifest schema is unsupported"
            )

        coordinate_frame = inspection_manifest.get("coordinate_frame")
        if not isinstance(coordinate_frame, dict):
            raise SceneImportError("Scene coordinate_frame is required")
        if coordinate_frame.get("offset_applied") is not False:
            raise SceneImportError(
                "Imported scene facts must use unshifted source coordinates"
            )
        local_origin = coordinate_frame.get("local_origin")
        if (
            not isinstance(local_origin, list)
            or len(local_origin) != 3
            or not all(isinstance(value, (int, float)) for value in local_origin)
        ):
            raise SceneImportError(
                "Scene coordinate_frame local_origin must contain three numbers"
            )

        producer = result.get("producer")
        if not isinstance(producer, dict):
            raise SceneImportError(
                "Worker result lacks importer provenance"
            )
        importer_id = self._required_text(producer, "id")
        importer_version = self._required_text(producer, "version")
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
                    for member in source_members
                ],
            },
            "producer_job_id": job.job_id,
            "importer": producer,
            "coordinate_frame": coordinate_frame,
            "facts": {
                "scene_inspection_manifest_artifact_id": (
                    inspection_manifest_id
                ),
                "material_fact_report_artifact_id": fact_report_id,
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
        existing = self.store.find_scene_revision_by_manifest(
            manifest_artifact.artifact_id
        )
        if existing is not None:
            self.store.set_active_scene_revision(existing.scene_revision_id)
            return existing

        frame = self.store.create_coordinate_frame(
            name=self._required_text(coordinate_frame, "name"),
            handedness=self._required_text(
                coordinate_frame,
                "handedness",
            ),
            up_axis=self._required_text(coordinate_frame, "up_axis"),
            linear_unit=self._required_text(
                coordinate_frame,
                "linear_unit",
            ),
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
        return self.store.create_scene_revision(
            source_id=source.source_id,
            importer_id=importer_id,
            importer_version=importer_version,
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest_artifact.artifact_id,
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
                raise SceneImportError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(payload, dict):
            raise SceneImportError(f"{label} must be a JSON object")
        return payload

    @staticmethod
    def _outputs_by_role(
        result: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        outputs = result.get("output_artifacts")
        if not isinstance(outputs, list):
            raise SceneImportError("Worker result output_artifacts is required")
        by_role: dict[str, dict[str, Any]] = {}
        for output in outputs:
            if not isinstance(output, dict):
                raise SceneImportError(
                    "Worker output Artifact descriptor is invalid"
                )
            role = SceneImportService._required_text(output, "role")
            if role in by_role:
                raise SceneImportError(
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
            raise SceneImportError(
                f"Worker result lacks output role: {role}"
            ) from error
        return SceneImportService._required_text(output, "artifact_id")

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SceneImportError(f"{field} is required")
        return value.strip()
