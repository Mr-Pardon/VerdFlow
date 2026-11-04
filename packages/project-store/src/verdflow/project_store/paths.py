"""Persistence for bounded path snapshots; geometry stays in immutable artifacts."""

import uuid
from verdflow.contracts import PathSetInfo
from .database import ProjectDatabase


class PathRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        scene_revision_id: str,
        producer_job_id: str,
        input_hash: str,
        coordinate_frame_id: str,
        geometry_artifact_id: str,
        path_count: int,
        created_at: str,
    ) -> PathSetInfo:
        if type(path_count) is not int or path_count < 1 or not input_hash:
            raise ValueError("PathSet requires nonempty paths and input provenance")
        with self.database.transaction() as connection:
            scene = connection.execute(
                "SELECT coordinate_frame_id FROM scene_revisions WHERE scene_revision_id=?",
                (scene_revision_id,),
            ).fetchone()
            job = connection.execute(
                "SELECT status FROM jobs WHERE job_id=?", (producer_job_id,)
            ).fetchone()
            if scene is None or scene["coordinate_frame_id"] != coordinate_frame_id:
                raise ValueError("PathSet coordinate frame differs from SceneRevision")
            if job is None or job["status"] != "succeeded":
                raise ValueError("PathSet producer Job must have succeeded")
            existing = self.by_job(producer_job_id)
            values = (
                scene_revision_id,
                producer_job_id,
                input_hash,
                coordinate_frame_id,
                geometry_artifact_id,
                path_count,
            )
            if existing:
                if (
                    tuple(
                        getattr(existing, key)
                        for key in (
                            "scene_revision_id",
                            "producer_job_id",
                            "input_hash",
                            "coordinate_frame_id",
                            "geometry_artifact_id",
                            "path_count",
                        )
                    )
                    != values
                ):
                    raise ValueError(
                        "PathSet producer is already bound to different input"
                    )
                return existing
            identifier = f"paths_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO path_sets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, *values, created_at),
            )
        return self.get(identifier)

    def get(self, identifier: str) -> PathSetInfo:
        row = self.database.connection.execute(
            "SELECT * FROM path_sets WHERE path_set_id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise ValueError("PathSet was not found")
        return PathSetInfo(**dict(row))

    def by_job(self, job_id: str) -> PathSetInfo | None:
        row = self.database.connection.execute(
            "SELECT * FROM path_sets WHERE producer_job_id=?", (job_id,)
        ).fetchone()
        return PathSetInfo(**dict(row)) if row else None
