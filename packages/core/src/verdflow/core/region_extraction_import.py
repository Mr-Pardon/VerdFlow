"""Finalize a Region processor result against an existing SceneRevision."""

from __future__ import annotations

import json
from typing import Any

from verdflow.contracts import JobStatus, RegionSetInfo
from verdflow.project_store import ProjectStore

from .region_import import RegionSetImportError, RegionSetImportService


class RegionExtractionImportError(RegionSetImportError):
    """Raised when a Region extraction result cannot be finalized."""


class RegionExtractionImportService:
    """Validate the extraction envelope, then finalize shared Region outputs."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._region_sets = RegionSetImportService(store)

    def finalize(self, job_id: str) -> RegionSetInfo:
        existing = self.store.get_region_set_by_job(job_id)
        if existing is not None:
            return existing
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise RegionExtractionImportError(
                "Region extraction Job has not succeeded"
            )
        if job.result_artifact_id is None:
            raise RegionExtractionImportError("Region extraction Job has no result")

        command = self._read_json(
            job.command.artifact_id,
            "Region extraction command",
        )
        result = self._read_json(job.result_artifact_id, "Worker result")
        if result.get("result_kind") != "region_extraction":
            raise RegionExtractionImportError(
                "Only region_extraction results can finalize Region extraction"
            )
        try:
            return self._region_sets.finalize_result(
                job_id=job.job_id,
                scene_revision_id=self._required_text(
                    command,
                    "scene_revision_id",
                ),
                source_id=self._required_text(command, "source_id"),
                result=result,
                command_artifact_id=job.command.artifact_id,
            )
        except RegionSetImportError as error:
            raise RegionExtractionImportError(str(error)) from error

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        with self.store.open_artifact(artifact_id) as stream:
            try:
                payload = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RegionExtractionImportError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(payload, dict):
            raise RegionExtractionImportError(f"{label} must be a JSON object")
        return payload

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RegionExtractionImportError(f"{field} is required")
        return value.strip()
