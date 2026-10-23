"""Immutable output ExportRecord persistence."""

from __future__ import annotations

import math
import sqlite3
import uuid

from verdflow.contracts import ExportRecordInfo

from .database import ProjectDatabase
from .errors import ExportConflictError, ExportNotFoundError


_EXPORT_SELECT = """
SELECT export_id, producer_job_id, adapter_id, adapter_version, target_type,
       point_set_id, asset_assignment_set_id, output_artifact_id,
       offset_x, offset_y, offset_z, record_count, created_at
FROM exports
"""


class ExportRepository:
    """Persist one immutable export fact per successful producer Job."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        producer_job_id: str,
        adapter_id: str,
        adapter_version: str,
        target_type: str,
        point_set_id: str,
        asset_assignment_set_id: str,
        output_artifact_id: str,
        offset: tuple[float, float, float],
        record_count: int,
        created_at: str,
        selected_asset_ids: tuple[str, ...] | None = None,
        terrain_selected_count: int | None = None,
    ) -> ExportRecordInfo:
        adapter_id = self._required(adapter_id, "adapter_id")
        adapter_version = self._required(adapter_version, "adapter_version")
        target_type = self._required(target_type, "target_type")
        if record_count < 0:
            raise ValueError("record_count cannot be negative")
        normalized_offset = self._offset(offset)
        with self.database.transaction() as connection:
            existing = self._by_job(connection, producer_job_id)
            expected = (
                adapter_id,
                adapter_version,
                target_type,
                point_set_id,
                asset_assignment_set_id,
                output_artifact_id,
                *normalized_offset,
                record_count,
            )
            if existing is not None:
                actual = (
                    existing.adapter_id,
                    existing.adapter_version,
                    existing.target_type,
                    existing.point_set_id,
                    existing.asset_assignment_set_id,
                    existing.output_artifact_id,
                    existing.offset_x,
                    existing.offset_y,
                    existing.offset_z,
                    existing.record_count,
                )
                if actual != expected:
                    raise ExportConflictError(
                        "Producer Job already has a different ExportRecord"
                    )
                return existing
            job = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?",
                (producer_job_id,),
            ).fetchone()
            if job is None or job["status"] != "succeeded":
                raise ExportConflictError("Export producer Job has not succeeded")
            point = connection.execute(
                "SELECT kind, point_count, hit_count, parent_point_set_id "
                "FROM point_sets WHERE point_set_id = ?",
                (point_set_id,),
            ).fetchone()
            if point is None:
                raise ExportConflictError("Export PointSet does not exist")
            if point["kind"] != "elevation_resolved":
                raise ExportConflictError(
                    "Export PointSet must be elevation_resolved"
                )
            assignment = connection.execute(
                """
                SELECT point_set_id, target_type, point_count
                FROM asset_assignment_sets
                WHERE asset_assignment_set_id = ?
                """,
                (asset_assignment_set_id,),
            ).fetchone()
            if assignment is None:
                raise ExportConflictError("Export AssignmentSet does not exist")
            if (
                assignment["point_set_id"]
                not in {point_set_id, point["parent_point_set_id"]}
                or assignment["target_type"] != target_type
                or int(assignment["point_count"]) != int(point["point_count"])
            ):
                raise ExportConflictError(
                    "Export PointSet, AssignmentSet and target facts disagree"
                )
            expected_count = int(point["point_count"])
            if selected_asset_ids is not None:
                counts = {row["asset_id"]: int(row["point_count"]) for row in connection.execute(
                    "SELECT asset_id, point_count FROM asset_assignment_set_assets WHERE asset_assignment_set_id = ?",
                    (asset_assignment_set_id,),
                )}
                if not selected_asset_ids or len(set(selected_asset_ids)) != len(selected_asset_ids) or set(selected_asset_ids) - set(counts):
                    raise ExportConflictError("Export asset selection is invalid")
                expected_count = sum(counts[key] for key in selected_asset_ids)
            if terrain_selected_count is not None:
                if not 0 < terrain_selected_count <= min(expected_count, int(point["hit_count"])):
                    raise ExportConflictError("Export terrain selection count is invalid")
                expected_count = terrain_selected_count
            if record_count != expected_count:
                raise ExportConflictError("Export count differs from selected asset facts")
            artifact = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                (output_artifact_id,),
            ).fetchone()
            if artifact is None:
                raise ExportConflictError("Export output Artifact does not exist")
            record = ExportRecordInfo(
                export_id=f"exp_{uuid.uuid4().hex}",
                producer_job_id=producer_job_id,
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                target_type=target_type,
                point_set_id=point_set_id,
                asset_assignment_set_id=asset_assignment_set_id,
                output_artifact_id=output_artifact_id,
                offset_x=normalized_offset[0],
                offset_y=normalized_offset[1],
                offset_z=normalized_offset[2],
                record_count=record_count,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO exports(
                    export_id, producer_job_id, adapter_id, adapter_version,
                    target_type, point_set_id, asset_assignment_set_id,
                    output_artifact_id, offset_x, offset_y, offset_z,
                    record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.export_id,
                    record.producer_job_id,
                    record.adapter_id,
                    record.adapter_version,
                    record.target_type,
                    record.point_set_id,
                    record.asset_assignment_set_id,
                    record.output_artifact_id,
                    record.offset_x,
                    record.offset_y,
                    record.offset_z,
                    record.record_count,
                    record.created_at,
                ),
            )
            return record

    def get(self, export_id: str) -> ExportRecordInfo:
        row = self.database.connection.execute(
            _EXPORT_SELECT + " WHERE export_id = ?",
            (export_id,),
        ).fetchone()
        if row is None:
            raise ExportNotFoundError(export_id)
        return _from_row(row)

    def get_by_job(self, producer_job_id: str) -> ExportRecordInfo | None:
        return self._by_job(self.database.connection, producer_job_id)

    def get_latest_for_inputs(
        self,
        point_set_id: str,
        asset_assignment_set_id: str,
    ) -> ExportRecordInfo | None:
        row = self.database.connection.execute(
            _EXPORT_SELECT
            + " WHERE point_set_id = ? AND asset_assignment_set_id = ?"
            + " ORDER BY created_at DESC, export_id DESC LIMIT 1",
            (point_set_id, asset_assignment_set_id),
        ).fetchone()
        return _from_row(row) if row is not None else None

    @staticmethod
    def _by_job(
        connection: sqlite3.Connection,
        producer_job_id: str,
    ) -> ExportRecordInfo | None:
        row = connection.execute(
            _EXPORT_SELECT + " WHERE producer_job_id = ?",
            (producer_job_id,),
        ).fetchone()
        return _from_row(row) if row is not None else None

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _offset(value: tuple[float, float, float]) -> tuple[float, float, float]:
        if not isinstance(value, tuple) or len(value) != 3:
            raise ValueError("offset must be a three-value tuple")
        result = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in result):
            raise ValueError("offset values must be finite")
        return result[0], result[1], result[2]


def _from_row(row: sqlite3.Row) -> ExportRecordInfo:
    return ExportRecordInfo(
        export_id=str(row["export_id"]),
        producer_job_id=str(row["producer_job_id"]),
        adapter_id=str(row["adapter_id"]),
        adapter_version=str(row["adapter_version"]),
        target_type=str(row["target_type"]),
        point_set_id=str(row["point_set_id"]),
        asset_assignment_set_id=str(row["asset_assignment_set_id"]),
        output_artifact_id=str(row["output_artifact_id"]),
        offset_x=float(row["offset_x"]),
        offset_y=float(row["offset_y"]),
        offset_z=float(row["offset_z"]),
        record_count=int(row["record_count"]),
        created_at=str(row["created_at"]),
    )
