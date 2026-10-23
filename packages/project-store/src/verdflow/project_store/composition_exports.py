"""Immutable multi-batch composition export persistence."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid

from verdflow.contracts import (
    CompositionExportInputInfo,
    CompositionExportRecordInfo,
)

from .database import ProjectDatabase
from .errors import ExportConflictError, ExportNotFoundError


_SELECT = """
SELECT export_id, composition_id, composition_generation, producer_job_id,
       adapter_id, adapter_version, target_type, output_artifact_id,
       offset_x, offset_y, offset_z, record_count, created_at
FROM composition_exports
"""


class CompositionExportRepository:
    """Persist one immutable output over an ordered set of planting batches."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        composition_id: str,
        composition_generation: int,
        producer_job_id: str,
        adapter_id: str,
        adapter_version: str,
        target_type: str,
        output_artifact_id: str,
        offset: tuple[float, float, float],
        record_count: int,
        inputs: tuple[CompositionExportInputInfo, ...],
        created_at: str,
    ) -> CompositionExportRecordInfo:
        if not inputs:
            raise ValueError("Composition export requires at least one input")
        if record_count < 0:
            raise ValueError("record_count cannot be negative")
        normalized_offset = self._offset(offset)
        with self.database.transaction() as connection:
            existing = self._by_job(connection, producer_job_id)
            if existing is not None:
                return existing
            job = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?",
                (producer_job_id,),
            ).fetchone()
            if job is None or job["status"] != "succeeded":
                raise ExportConflictError(
                    "Composition export producer Job has not succeeded"
                )
            composition = connection.execute(
                """
                SELECT generation FROM planting_compositions
                WHERE composition_id = ?
                """,
                (composition_id,),
            ).fetchone()
            if composition is None:
                raise ExportConflictError("Planting composition does not exist")
            if int(composition["generation"]) != composition_generation:
                raise ExportConflictError(
                    "Planting composition changed before export finalization"
                )
            expected_count = 0
            seen_batches: set[str] = set()
            for ordinal, item in enumerate(inputs):
                if item.ordinal != ordinal or item.batch_id in seen_batches:
                    raise ExportConflictError(
                        "Composition export input order is invalid"
                    )
                seen_batches.add(item.batch_id)
                batch = connection.execute(
                    """
                    SELECT generated_point_set_id, asset_assignment_set_id
                    FROM planting_batches
                    WHERE batch_id = ? AND composition_id = ?
                    """,
                    (item.batch_id, composition_id),
                ).fetchone()
                point = connection.execute(
                    """
                    SELECT kind, parent_point_set_id, point_count
                    FROM point_sets WHERE point_set_id = ?
                    """,
                    (item.point_set_id,),
                ).fetchone()
                if (
                    batch is None
                    or point is None
                    or point["kind"] != "elevation_resolved"
                    or point["parent_point_set_id"]
                    != batch["generated_point_set_id"]
                    or item.asset_assignment_set_id
                    != batch["asset_assignment_set_id"]
                ):
                    raise ExportConflictError(
                        "Composition export input lineage is inconsistent"
                    )
                counts = {
                    str(row["asset_id"]): int(row["point_count"])
                    for row in connection.execute(
                        """
                        SELECT asset_id, point_count
                        FROM asset_assignment_set_assets
                        WHERE asset_assignment_set_id = ?
                        """,
                        (item.asset_assignment_set_id,),
                    )
                }
                if item.selected_asset_ids is None:
                    expected_count += int(point["point_count"])
                else:
                    if (
                        not item.selected_asset_ids
                        or set(item.selected_asset_ids) - set(counts)
                    ):
                        raise ExportConflictError(
                            "Composition export asset selection is invalid"
                        )
                    expected_count += sum(
                        counts[asset_id] for asset_id in item.selected_asset_ids
                    )
            if record_count != expected_count:
                raise ExportConflictError(
                    "Composition export count differs from selected inputs"
                )
            artifact = connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                (output_artifact_id,),
            ).fetchone()
            if artifact is None:
                raise ExportConflictError(
                    "Composition export output Artifact does not exist"
                )
            export_id = f"cexp_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO composition_exports(
                    export_id, composition_id, composition_generation,
                    producer_job_id, adapter_id, adapter_version, target_type,
                    output_artifact_id, offset_x, offset_y, offset_z,
                    record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    composition_id,
                    composition_generation,
                    producer_job_id,
                    adapter_id,
                    adapter_version,
                    target_type,
                    output_artifact_id,
                    *normalized_offset,
                    record_count,
                    created_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO composition_export_inputs(
                    export_id, ordinal, batch_id, point_set_id,
                    asset_assignment_set_id, selected_asset_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        export_id,
                        item.ordinal,
                        item.batch_id,
                        item.point_set_id,
                        item.asset_assignment_set_id,
                        json.dumps(item.selected_asset_ids)
                        if item.selected_asset_ids is not None
                        else None,
                    )
                    for item in inputs
                ),
            )
            return self._get(connection, export_id)

    def get(self, export_id: str) -> CompositionExportRecordInfo:
        return self._get(self.database.connection, export_id)

    def get_by_job(
        self, producer_job_id: str
    ) -> CompositionExportRecordInfo | None:
        return self._by_job(self.database.connection, producer_job_id)

    def get_latest(
        self, composition_id: str
    ) -> CompositionExportRecordInfo | None:
        row = self.database.connection.execute(
            _SELECT
            + " WHERE composition_id = ? ORDER BY created_at DESC, export_id DESC LIMIT 1",
            (composition_id,),
        ).fetchone()
        return self._from_row(self.database.connection, row) if row else None

    def list_all(
        self,
        *,
        limit: int = 200,
    ) -> tuple[CompositionExportRecordInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT + " ORDER BY created_at DESC, export_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(
            self._from_row(self.database.connection, row) for row in rows
        )

    def delete(self, export_id: str) -> CompositionExportRecordInfo:
        with self.database.transaction() as connection:
            record = self._get(connection, export_id)
            connection.execute(
                "DELETE FROM composition_exports WHERE export_id = ?",
                (export_id,),
            )
            return record

    @classmethod
    def _get(
        cls, connection: sqlite3.Connection, export_id: str
    ) -> CompositionExportRecordInfo:
        row = connection.execute(
            _SELECT + " WHERE export_id = ?", (export_id,)
        ).fetchone()
        if row is None:
            raise ExportNotFoundError(export_id)
        return cls._from_row(connection, row)

    @classmethod
    def _by_job(
        cls, connection: sqlite3.Connection, producer_job_id: str
    ) -> CompositionExportRecordInfo | None:
        row = connection.execute(
            _SELECT + " WHERE producer_job_id = ?", (producer_job_id,)
        ).fetchone()
        return cls._from_row(connection, row) if row else None

    @staticmethod
    def _from_row(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> CompositionExportRecordInfo:
        inputs = tuple(
            CompositionExportInputInfo(
                ordinal=int(item["ordinal"]),
                batch_id=str(item["batch_id"]),
                point_set_id=str(item["point_set_id"]),
                asset_assignment_set_id=str(
                    item["asset_assignment_set_id"]
                ),
                selected_asset_ids=(
                    tuple(json.loads(item["selected_asset_ids_json"]))
                    if item["selected_asset_ids_json"] is not None
                    else None
                ),
            )
            for item in connection.execute(
                """
                SELECT ordinal, batch_id, point_set_id,
                       asset_assignment_set_id, selected_asset_ids_json
                FROM composition_export_inputs
                WHERE export_id = ? ORDER BY ordinal
                """,
                (row["export_id"],),
            )
        )
        return CompositionExportRecordInfo(
            export_id=str(row["export_id"]),
            composition_id=str(row["composition_id"]),
            composition_generation=int(row["composition_generation"]),
            producer_job_id=str(row["producer_job_id"]),
            adapter_id=str(row["adapter_id"]),
            adapter_version=str(row["adapter_version"]),
            target_type=str(row["target_type"]),
            output_artifact_id=str(row["output_artifact_id"]),
            offset_x=float(row["offset_x"]),
            offset_y=float(row["offset_y"]),
            offset_z=float(row["offset_z"]),
            record_count=int(row["record_count"]),
            inputs=inputs,
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _offset(value: tuple[float, float, float]) -> tuple[float, float, float]:
        if len(value) != 3:
            raise ValueError("offset must contain three values")
        result = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in result):
            raise ValueError("offset values must be finite")
        return result[0], result[1], result[2]
