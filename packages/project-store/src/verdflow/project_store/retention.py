"""Reference-aware cleanup for expired transient project facts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from verdflow.contracts import JobStatus

from .database import ProjectDatabase


ARTIFACT_REFERENCE_COLUMNS = (
    ("jobs", "command_artifact_id"),
    ("jobs", "result_artifact_id"),
    ("jobs", "diagnostic_artifact_id"),
    ("job_attempts", "diagnostic_artifact_id"),
    ("sources", "artifact_id"),
    ("scene_revisions", "scene_manifest_artifact_id"),
    ("region_sets", "geometry_artifact_id"),
    ("region_sets", "metrics_artifact_id"),
    ("point_sets", "point_artifact_id"),
    ("strategy_package_snapshots", "package_artifact_id"),
    ("target_asset_use_snapshots", "snapshot_artifact_id"),
    ("effective_binding_snapshots", "snapshot_artifact_id"),
    ("asset_assignment_sets", "assignment_artifact_id"),
    ("exports", "output_artifact_id"),
    ("composition_exports", "output_artifact_id"),
    ("composition_output_executions", "request_artifact_id"),
    ("plant_run_executions", "request_artifact_id"),
    ("plant_run_executions", "continuation_artifact_id"),
    ("material_flow_executions", "request_artifact_id"),
    ("material_flow_executions", "rule_pack_artifact_id"),
    ("scene_preparation_executions", "request_artifact_id"),
)


@dataclass(frozen=True, slots=True)
class RetentionSweepMetadata:
    examined_job_count: int
    removed_job_count: int
    protected_job_count: int
    removed_assignment_count: int
    removed_snapshot_count: int
    removed_artifact_count: int


class RetentionRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def sweep_expired(
        self,
        *,
        now: str,
        orphan_before: str,
        limit: int,
        protected_artifact_ids: tuple[str, ...] = (),
    ) -> RetentionSweepMetadata:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        normalized_protected = tuple(
            sorted(
                {
                    artifact_id.strip()
                    for artifact_id in protected_artifact_ids
                    if isinstance(artifact_id, str) and artifact_id.strip()
                }
            )
        )
        removed_jobs = 0
        protected_jobs = 0
        removed_assignments = 0
        with self.database.transaction() as connection:
            candidates = connection.execute(
                """
                SELECT job_id
                FROM jobs
                WHERE purge_after IS NOT NULL
                  AND purge_after <= ?
                  AND status IN (?, ?, ?, ?)
                ORDER BY purge_after, job_id
                LIMIT ?
                """,
                (
                    now,
                    JobStatus.CANCELLED.value,
                    JobStatus.SUCCEEDED.value,
                    JobStatus.FAILED.value,
                    JobStatus.INTERRUPTED.value,
                    limit,
                ),
            ).fetchall()
            for index, candidate in enumerate(candidates):
                job_id = str(candidate["job_id"])
                savepoint = f"retention_job_{index}"
                connection.execute(f"SAVEPOINT {savepoint}")
                try:
                    assignment_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) AS count
                            FROM asset_assignment_sets
                            WHERE producer_job_id = ?
                            """,
                            (job_id,),
                        ).fetchone()["count"]
                    )
                    connection.execute(
                        """
                        DELETE FROM asset_assignment_sets
                        WHERE producer_job_id = ?
                        """,
                        (job_id,),
                    )
                    cursor = connection.execute(
                        "DELETE FROM jobs WHERE job_id = ?",
                        (job_id,),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            f"Expired Job disappeared during sweep: {job_id}"
                        )
                except sqlite3.IntegrityError:
                    connection.execute(f"ROLLBACK TO {savepoint}")
                    connection.execute(
                        """
                        UPDATE jobs
                        SET retention_class = 'audit',
                            retention_seconds = NULL,
                            purge_after = NULL
                        WHERE job_id = ?
                        """,
                        (job_id,),
                    )
                    protected_jobs += 1
                else:
                    removed_jobs += 1
                    removed_assignments += assignment_count
                finally:
                    connection.execute(f"RELEASE {savepoint}")

            removed_snapshots = self._delete_orphan_snapshots(
                connection,
                orphan_before,
                limit,
            )
            removed_artifacts = self._delete_orphan_artifacts(
                connection,
                orphan_before,
                limit,
                normalized_protected,
            )
        return RetentionSweepMetadata(
            examined_job_count=len(candidates),
            removed_job_count=removed_jobs,
            protected_job_count=protected_jobs,
            removed_assignment_count=removed_assignments,
            removed_snapshot_count=removed_snapshots,
            removed_artifact_count=removed_artifacts,
        )

    @staticmethod
    def _delete_orphan_snapshots(
        connection: sqlite3.Connection,
        orphan_before: str,
        limit: int,
    ) -> int:
        removed = 0
        cursor = connection.execute(
            """
            DELETE FROM effective_binding_snapshots
            WHERE effective_binding_snapshot_id IN (
                SELECT binding.effective_binding_snapshot_id
                FROM effective_binding_snapshots binding
                WHERE binding.created_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM asset_assignment_sets assignments
                      WHERE assignments.effective_binding_snapshot_id =
                            binding.effective_binding_snapshot_id
                  )
                ORDER BY binding.created_at, binding.effective_binding_snapshot_id
                LIMIT ?
            )
            """,
            (orphan_before, limit),
        )
        removed += cursor.rowcount
        cursor = connection.execute(
            """
            DELETE FROM target_asset_use_snapshots
            WHERE target_asset_use_snapshot_id IN (
                SELECT asset_use.target_asset_use_snapshot_id
                FROM target_asset_use_snapshots asset_use
                WHERE asset_use.created_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM effective_binding_snapshots bindings
                      WHERE bindings.target_asset_use_snapshot_id =
                            asset_use.target_asset_use_snapshot_id
                  )
                ORDER BY asset_use.created_at, asset_use.target_asset_use_snapshot_id
                LIMIT ?
            )
            """,
            (orphan_before, limit),
        )
        removed += cursor.rowcount
        cursor = connection.execute(
            """
            DELETE FROM strategy_package_snapshots
            WHERE strategy_package_snapshot_id IN (
                SELECT package.strategy_package_snapshot_id
                FROM strategy_package_snapshots package
                WHERE package.created_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM planting_run_strategy_snapshots links
                      WHERE links.strategy_package_snapshot_id =
                            package.strategy_package_snapshot_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM effective_binding_snapshots bindings
                      WHERE bindings.strategy_package_snapshot_id =
                            package.strategy_package_snapshot_id
                  )
                ORDER BY package.created_at, package.strategy_package_snapshot_id
                LIMIT ?
            )
            """,
            (orphan_before, limit),
        )
        removed += cursor.rowcount
        return removed

    @staticmethod
    def _delete_orphan_artifacts(
        connection: sqlite3.Connection,
        orphan_before: str,
        limit: int,
        protected_artifact_ids: tuple[str, ...],
    ) -> int:
        reference_guards = "\n".join(
            "AND NOT EXISTS ("
            f"SELECT 1 FROM {table_name} "
            f"WHERE {column_name} = artifact.artifact_id)"
            for table_name, column_name in ARTIFACT_REFERENCE_COLUMNS
        )
        protected_guard = ""
        parameters: list[object] = [orphan_before]
        if protected_artifact_ids:
            placeholders = ", ".join("?" for _ in protected_artifact_ids)
            protected_guard = (
                f"AND artifact.artifact_id NOT IN ({placeholders})"
            )
            parameters.extend(protected_artifact_ids)
        parameters.append(limit)
        cursor = connection.execute(
            f"""
            DELETE FROM artifacts
            WHERE artifact_id IN (
                SELECT artifact.artifact_id
                FROM artifacts artifact
                WHERE artifact.created_at <= ?
                  {reference_guards}
                  {protected_guard}
                ORDER BY artifact.created_at, artifact.artifact_id
                LIMIT ?
            )
            """,
            tuple(parameters),
        )
        return cursor.rowcount
