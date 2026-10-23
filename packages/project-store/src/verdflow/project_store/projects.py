"""Project metadata repository."""

from __future__ import annotations

from verdflow.contracts import ProjectInfo

from .database import ProjectDatabase
from .errors import ProjectSchemaError


class ProjectRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def get_info(self) -> ProjectInfo:
        row = self.database.connection.execute(
            """
            SELECT project_id, name, schema_version, created_at, updated_at,
                   active_scene_revision_id
            FROM projects
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise ProjectSchemaError("Project row is missing")
        return ProjectInfo(
            project_id=row["project_id"],
            name=row["name"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active_scene_revision_id=row["active_scene_revision_id"],
        )

    def set_active_scene_revision(
        self,
        scene_revision_id: str | None,
        updated_at: str,
    ) -> ProjectInfo:
        with self.database.transaction() as connection:
            if scene_revision_id is not None:
                exists = connection.execute(
                    """
                    SELECT 1 FROM scene_revisions
                    WHERE scene_revision_id = ?
                    """,
                    (scene_revision_id,),
                ).fetchone()
                if exists is None:
                    raise ProjectSchemaError(
                        f"Scene revision does not exist: {scene_revision_id}"
                    )
            connection.execute(
                """
                UPDATE projects
                SET active_scene_revision_id = ?, updated_at = ?
                """,
                (scene_revision_id, updated_at),
            )
        return self.get_info()
