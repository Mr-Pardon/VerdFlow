"""Persistence for the single current multi-batch planting result."""

from __future__ import annotations

import json
import sqlite3
import uuid

from verdflow.contracts import PlantingBatchInfo, PlantingCompositionInfo

from .database import ProjectDatabase
from .errors import (
    PlantingCompositionConflictError,
    PlantingCompositionNotFoundError,
)


_COMPOSITION_SELECT = """
SELECT composition_id, project_id, scene_revision_id, region_set_id,
       generation, created_at, updated_at,
       (SELECT COUNT(*) FROM planting_batches batch
        WHERE batch.composition_id = composition.composition_id) AS batch_count
FROM planting_compositions composition
"""

_BATCH_SELECT = """
SELECT batch_id, composition_id, ordinal, plant_run_execution_id,
       generated_point_set_id, asset_assignment_set_id,
       selected_asset_ids_json, created_at
FROM planting_batches
"""


class PlantingCompositionRepository:
    """Persist current composition references without interpreting strategies."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def get_current(self) -> PlantingCompositionInfo | None:
        row = self.database.connection.execute(
            _COMPOSITION_SELECT + " LIMIT 1"
        ).fetchone()
        return _composition_from_row(row) if row is not None else None

    def list_batches(self) -> tuple[PlantingBatchInfo, ...]:
        composition = self.get_current()
        if composition is None:
            return ()
        rows = self.database.connection.execute(
            _BATCH_SELECT
            + " WHERE composition_id = ? ORDER BY ordinal, batch_id",
            (composition.composition_id,),
        ).fetchall()
        return tuple(
            self._batch_from_row(self.database.connection, row) for row in rows
        )

    def commit_batch(
        self,
        *,
        plant_run_execution_id: str,
        selected_region_ids: tuple[str, ...],
        selected_asset_ids: tuple[str, ...] | None,
        continue_planting: bool,
        replacing_batch_id: str | None,
        created_at: str,
    ) -> tuple[PlantingCompositionInfo, PlantingBatchInfo]:
        regions = self._identifiers(selected_region_ids, "selected_region_ids")
        assets = (
            self._identifiers(selected_asset_ids, "selected_asset_ids")
            if selected_asset_ids is not None
            else None
        )
        with self.database.transaction() as connection:
            existing = connection.execute(
                _BATCH_SELECT + " WHERE plant_run_execution_id = ?",
                (plant_run_execution_id,),
            ).fetchone()
            if existing is not None:
                batch = self._batch_from_row(connection, existing)
                if (
                    batch.selected_region_ids != regions
                    or batch.selected_asset_ids != assets
                ):
                    raise PlantingCompositionConflictError(
                        "Plant Run is already committed with different selections"
                    )
                if continue_planting:
                    connection.execute(
                        """
                        UPDATE project_workflow_state
                        SET plant_run_execution_id = NULL,
                            revision = revision + 1, updated_at = ?
                        WHERE plant_run_execution_id = ?
                        """,
                        (created_at, plant_run_execution_id),
                    )
                composition = self._require_composition(
                    connection, batch.composition_id
                )
                return composition, batch

            workflow = connection.execute(
                """
                SELECT project_id, region_set_id, plant_run_execution_id,
                       revision
                FROM project_workflow_state
                LIMIT 1
                """
            ).fetchone()
            if workflow is None or workflow["region_set_id"] is None:
                raise PlantingCompositionConflictError(
                    "Project does not have a current RegionSet"
                )
            if workflow["plant_run_execution_id"] != plant_run_execution_id:
                raise PlantingCompositionConflictError(
                    "Only the current Plant Run can be committed"
                )

            execution = connection.execute(
                """
                SELECT generated_point_set_id, asset_assignment_set_id,
                       status, stage
                FROM plant_run_executions WHERE execution_id = ?
                """,
                (plant_run_execution_id,),
            ).fetchone()
            if (
                execution is None
                or execution["generated_point_set_id"] is None
                or execution["asset_assignment_set_id"] is None
                or execution["status"] not in {
                    "awaiting_confirmation", "queued", "running", "succeeded"
                }
                or execution["stage"] not in {
                    "review", "elevation", "export", "completed"
                }
            ):
                raise PlantingCompositionConflictError(
                    "Plant Run does not have committable reviewed outputs"
                )
            point = connection.execute(
                """
                SELECT point.point_set_id, run.scene_revision_id,
                       run.region_set_id
                FROM point_sets point
                JOIN planting_runs run
                  ON run.planting_run_id = point.planting_run_id
                WHERE point.point_set_id = ? AND point.kind = 'generated'
                """,
                (execution["generated_point_set_id"],),
            ).fetchone()
            if (
                point is None
                or point["region_set_id"] != workflow["region_set_id"]
            ):
                raise PlantingCompositionConflictError(
                    "Plant Run does not belong to the current RegionSet"
                )
            assignment = connection.execute(
                """
                SELECT point_set_id FROM asset_assignment_sets
                WHERE asset_assignment_set_id = ?
                """,
                (execution["asset_assignment_set_id"],),
            ).fetchone()
            if assignment is None or assignment["point_set_id"] != point["point_set_id"]:
                raise PlantingCompositionConflictError(
                    "Plant Run assignment does not belong to its generated PointSet"
                )
            region_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM regions
                WHERE region_set_id = ?
                  AND region_id IN ({})
                """.format(",".join("?" for _ in regions)),
                (workflow["region_set_id"], *regions),
            ).fetchone()["count"]
            if int(region_count) != len(regions):
                raise PlantingCompositionConflictError(
                    "Batch references Regions outside the current RegionSet"
                )
            if assets is not None:
                available = {
                    str(row["asset_id"])
                    for row in connection.execute(
                        """
                        SELECT asset_id FROM asset_assignment_set_assets
                        WHERE asset_assignment_set_id = ?
                        """,
                        (execution["asset_assignment_set_id"],),
                    )
                }
                if set(assets) - available:
                    raise PlantingCompositionConflictError(
                        "Batch asset selection references unknown assignments"
                    )

            composition_row = connection.execute(
                _COMPOSITION_SELECT + " LIMIT 1"
            ).fetchone()
            if composition_row is None:
                if replacing_batch_id is not None:
                    raise PlantingCompositionNotFoundError(
                        replacing_batch_id
                    )
                composition_id = f"cmp_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO planting_compositions(
                        composition_id, project_id, scene_revision_id,
                        region_set_id, generation, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        composition_id,
                        workflow["project_id"],
                        point["scene_revision_id"],
                        point["region_set_id"],
                        created_at,
                        created_at,
                    ),
                )
                ordinal = 0
            else:
                composition = _composition_from_row(composition_row)
                if (
                    composition.scene_revision_id != point["scene_revision_id"]
                    or composition.region_set_id != point["region_set_id"]
                ):
                    raise PlantingCompositionConflictError(
                        "Current composition belongs to different scene facts"
                    )
                composition_id = composition.composition_id
                if replacing_batch_id is None:
                    ordinal = int(
                        connection.execute(
                            """
                            SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ordinal
                            FROM planting_batches WHERE composition_id = ?
                            """,
                            (composition_id,),
                        ).fetchone()["next_ordinal"]
                    )
                else:
                    replaced_row = connection.execute(
                        _BATCH_SELECT
                        + " WHERE composition_id = ? AND batch_id = ?",
                        (composition_id, replacing_batch_id),
                    ).fetchone()
                    if replaced_row is None:
                        raise PlantingCompositionNotFoundError(
                            replacing_batch_id
                        )
                    replaced = self._batch_from_row(connection, replaced_row)
                    if replaced.selected_region_ids != regions:
                        raise PlantingCompositionConflictError(
                            "A replacement batch must keep the same Regions"
                        )
                    ordinal = replaced.ordinal
                if replacing_batch_id is not None:
                    connection.execute(
                        "DELETE FROM planting_batches WHERE batch_id = ?",
                        (replacing_batch_id,),
                    )
                connection.execute(
                    """
                    UPDATE planting_compositions
                    SET generation = generation + 1, updated_at = ?
                    WHERE composition_id = ?
                    """,
                    (created_at, composition_id),
                )

            batch_id = f"pbt_{uuid.uuid4().hex}"
            try:
                connection.execute(
                    """
                    INSERT INTO planting_batches(
                        batch_id, composition_id, ordinal,
                        plant_run_execution_id, generated_point_set_id,
                        asset_assignment_set_id, selected_asset_ids_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        composition_id,
                        ordinal,
                        plant_run_execution_id,
                        point["point_set_id"],
                        execution["asset_assignment_set_id"],
                        json.dumps(assets) if assets is not None else None,
                        created_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO planting_batch_regions(
                        composition_id, batch_id, region_set_id, region_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            composition_id,
                            batch_id,
                            point["region_set_id"],
                            region_id,
                        )
                        for region_id in regions
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PlantingCompositionConflictError(
                    "One or more Regions are already committed"
                ) from error

            if continue_planting:
                cursor = connection.execute(
                    """
                    UPDATE project_workflow_state
                    SET plant_run_execution_id = NULL,
                        revision = revision + 1, updated_at = ?
                    WHERE project_id = ? AND revision = ?
                      AND plant_run_execution_id = ?
                    """,
                    (
                        created_at,
                        workflow["project_id"],
                        workflow["revision"],
                        plant_run_execution_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PlantingCompositionConflictError(
                        "Project workflow revision is stale"
                    )

            composition = self._require_composition(connection, composition_id)
            batch_row = connection.execute(
                _BATCH_SELECT + " WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return composition, self._batch_from_row(connection, batch_row)

    def remove_batch(self, batch_id: str, *, updated_at: str) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT composition_id FROM planting_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise PlantingCompositionNotFoundError(batch_id)
            composition_id = str(row["composition_id"])
            connection.execute(
                "DELETE FROM planting_batches WHERE batch_id = ?",
                (batch_id,),
            )
            count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM planting_batches WHERE composition_id = ?",
                    (composition_id,),
                ).fetchone()["count"]
            )
            if count == 0:
                connection.execute(
                    "DELETE FROM planting_compositions WHERE composition_id = ?",
                    (composition_id,),
                )
            else:
                connection.execute(
                    """
                    UPDATE planting_compositions
                    SET generation = generation + 1, updated_at = ?
                    WHERE composition_id = ?
                    """,
                    (updated_at, composition_id),
                )

    def discard_current_preview(
        self,
        plant_run_execution_id: str,
        *,
        updated_at: str,
    ) -> None:
        """Detach one uncommitted current Plant Run from the workflow."""
        with self.database.transaction() as connection:
            committed = connection.execute(
                """
                SELECT batch_id FROM planting_batches
                WHERE plant_run_execution_id = ?
                """,
                (plant_run_execution_id,),
            ).fetchone()
            if committed is not None:
                raise PlantingCompositionConflictError(
                    "A committed Plant Run cannot be discarded as a preview"
                )

            workflow = connection.execute(
                """
                SELECT project_id, plant_run_execution_id, revision
                FROM project_workflow_state
                LIMIT 1
                """
            ).fetchone()
            if (
                workflow is None
                or workflow["plant_run_execution_id"]
                != plant_run_execution_id
            ):
                raise PlantingCompositionConflictError(
                    "Only the current uncommitted Plant Run can be discarded"
                )

            cursor = connection.execute(
                """
                UPDATE project_workflow_state
                SET plant_run_execution_id = NULL,
                    revision = revision + 1, updated_at = ?
                WHERE project_id = ? AND revision = ?
                  AND plant_run_execution_id = ?
                """,
                (
                    updated_at,
                    workflow["project_id"],
                    workflow["revision"],
                    plant_run_execution_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PlantingCompositionConflictError(
                    "Project workflow revision is stale"
                )

    @staticmethod
    def _identifiers(
        values: tuple[str, ...] | None,
        field: str,
    ) -> tuple[str, ...]:
        if not values:
            raise ValueError(f"{field} must contain at least one identifier")
        normalized = tuple(str(value).strip() for value in values)
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError(f"{field} must contain unique non-empty identifiers")
        return tuple(sorted(normalized))

    @staticmethod
    def _require_composition(
        connection: sqlite3.Connection,
        composition_id: str,
    ) -> PlantingCompositionInfo:
        row = connection.execute(
            _COMPOSITION_SELECT + " WHERE composition_id = ?",
            (composition_id,),
        ).fetchone()
        if row is None:
            raise PlantingCompositionNotFoundError(composition_id)
        return _composition_from_row(row)

    @staticmethod
    def _batch_from_row(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> PlantingBatchInfo:
        regions = tuple(
            str(item["region_id"])
            for item in connection.execute(
                """
                SELECT region_id FROM planting_batch_regions
                WHERE composition_id = ? AND batch_id = ?
                ORDER BY region_id
                """,
                (row["composition_id"], row["batch_id"]),
            )
        )
        raw_assets = row["selected_asset_ids_json"]
        assets = tuple(json.loads(raw_assets)) if raw_assets is not None else None
        return PlantingBatchInfo(
            batch_id=str(row["batch_id"]),
            composition_id=str(row["composition_id"]),
            ordinal=int(row["ordinal"]),
            plant_run_execution_id=str(row["plant_run_execution_id"]),
            generated_point_set_id=str(row["generated_point_set_id"]),
            asset_assignment_set_id=str(row["asset_assignment_set_id"]),
            selected_region_ids=regions,
            selected_asset_ids=assets,
            created_at=str(row["created_at"]),
        )


def _composition_from_row(row: sqlite3.Row) -> PlantingCompositionInfo:
    return PlantingCompositionInfo(
        composition_id=str(row["composition_id"]),
        project_id=str(row["project_id"]),
        scene_revision_id=str(row["scene_revision_id"]),
        region_set_id=str(row["region_set_id"]),
        generation=int(row["generation"]),
        batch_count=int(row["batch_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
