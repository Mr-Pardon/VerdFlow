"""Finalize canonical path selection without masquerading as Region geometry."""

import json
from verdflow.contracts import JobStatus, PathSetInfo
from verdflow.planting import validate_path_document
from verdflow.project_store import ProjectStore


class PathSetImportService:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def finalize(self, job_id: str) -> PathSetInfo:
        existing = self.store.get_path_set_by_job(job_id)
        if existing:
            return existing
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED or not job.result_artifact_id:
            raise ValueError("路径选择任务尚未成功")
        with self.store.open_artifact(job.command.artifact_id) as stream:
            command = json.load(stream)
        with self.store.open_artifact(job.result_artifact_id) as stream:
            result = json.load(stream)
        if (
            command.get("schema") != "verdflow.path-selection.command"
            or command.get("version") != "1"
            or command.get("operation") != "select_paths"
            or result.get("schema") != "verdflow.path-selection.result"
            or result.get("version") != "1"
            or result.get("status") != "succeeded"
            or result.get("operation") != "select_paths"
            or result.get("result_kind") != "path_selection"
        ):
            raise ValueError("路径选择任务结果不匹配")
        scene = self.store.get_scene_revision(command["scene_revision_id"])
        if (
            command.get("source_id") != scene.source_id
            or result.get("source_id") != scene.source_id
        ):
            raise ValueError("路径选择结果与场景来源不匹配")
        outputs = result.get("output_artifacts", [])
        paths = [item["artifact_id"] for item in outputs if item.get("role") == "paths"]
        if len(paths) != 1:
            raise ValueError("路径选择结果缺少唯一几何快照")
        artifact = self.store.get_artifact(paths[0])
        if artifact.schema_id != "verdflow.paths" or artifact.schema_version != "1":
            raise ValueError("路径快照格式不匹配")
        with self.store.open_artifact(artifact.artifact_id) as stream:
            document = validate_path_document(json.load(stream))
        return self.store.create_path_set(
            scene_revision_id=scene.scene_revision_id,
            producer_job_id=job_id,
            input_hash=self.store.get_artifact(job.command.artifact_id).content_hash,
            coordinate_frame_id=scene.coordinate_frame_id,
            geometry_artifact_id=artifact.artifact_id,
            path_count=len(document["paths"]),
        )
