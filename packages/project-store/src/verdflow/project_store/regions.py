"""Immutable RegionSet persistence and query summaries."""

from __future__ import annotations

import uuid

from verdflow.contracts import RegionSetInfo, RegionSummary

from .database import ProjectDatabase
from .errors import (
    ArtifactNotFoundError,
    RegionSetConflictError,
    RegionSetNotFoundError,
    SceneRevisionNotFoundError,
)


class RegionRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create_region_set(
        self,
        *,
        scene_revision_id: str,
        producer_job_id: str,
        extractor_id: str,
        extractor_version: str,
        input_hash: str,
        coordinate_frame_id: str,
        geometry_artifact_id: str,
        metrics_artifact_id: str,
        regions: tuple[RegionSummary, ...],
        created_at: str,
    ) -> RegionSetInfo:
        normalized = {
            "extractor_id": extractor_id.strip(),
            "extractor_version": extractor_version.strip(),
            "input_hash": input_hash.strip(),
        }
        missing = [key for key, value in normalized.items() if not value]
        if missing:
            raise ValueError(f"{missing[0]} is required")
        self._validate_regions(regions)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT region_set_id, scene_revision_id, producer_job_id,
                       extractor_id, extractor_version, input_hash,
                       coordinate_frame_id, geometry_artifact_id,
                       metrics_artifact_id, region_count, created_at
                FROM region_sets
                WHERE producer_job_id = ?
                """,
                (producer_job_id,),
            ).fetchone()
            if existing is not None:
                result = _region_set_from_row(existing)
                expected = (
                    scene_revision_id,
                    normalized["extractor_id"],
                    normalized["extractor_version"],
                    normalized["input_hash"],
                    coordinate_frame_id,
                    geometry_artifact_id,
                    metrics_artifact_id,
                    len(regions),
                )
                actual = (
                    result.scene_revision_id,
                    result.extractor_id,
                    result.extractor_version,
                    result.input_hash,
                    result.coordinate_frame_id,
                    result.geometry_artifact_id,
                    result.metrics_artifact_id,
                    result.region_count,
                )
                if actual != expected:
                    raise RegionSetConflictError(
                        "Producer Job is already bound to another RegionSet"
                    )
                return result

            scene = connection.execute(
                """
                SELECT coordinate_frame_id, source_id
                FROM scene_revisions
                WHERE scene_revision_id = ?
                """,
                (scene_revision_id,),
            ).fetchone()
            if scene is None:
                raise SceneRevisionNotFoundError(scene_revision_id)
            if scene["coordinate_frame_id"] != coordinate_frame_id:
                raise RegionSetConflictError(
                    "RegionSet coordinate frame differs from SceneRevision"
                )
            member_ids = {
                region.source_member_id
                for region in regions
                if region.source_member_id is not None
            }
            if member_ids:
                placeholders = ",".join("?" for _ in member_ids)
                rows = connection.execute(
                    f"""
                    SELECT source_member_id
                    FROM source_assembly_members
                    WHERE assembly_source_id = ?
                      AND source_member_id IN ({placeholders})
                    """,
                    (scene["source_id"], *sorted(member_ids)),
                ).fetchall()
                found = {row["source_member_id"] for row in rows}
                unknown = sorted(member_ids - found)
                if unknown:
                    raise RegionSetConflictError(
                        "Region source member differs from SceneRevision: "
                        + unknown[0]
                    )
            job = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?",
                (producer_job_id,),
            ).fetchone()
            if job is None or job["status"] != "succeeded":
                raise RegionSetConflictError(
                    "RegionSet producer Job must have succeeded"
                )
            for artifact_id in (
                geometry_artifact_id,
                metrics_artifact_id,
            ):
                if connection.execute(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone() is None:
                    raise ArtifactNotFoundError(artifact_id)

            region_set = RegionSetInfo(
                region_set_id=f"regions_{uuid.uuid4().hex}",
                scene_revision_id=scene_revision_id,
                producer_job_id=producer_job_id,
                extractor_id=normalized["extractor_id"],
                extractor_version=normalized["extractor_version"],
                input_hash=normalized["input_hash"],
                coordinate_frame_id=coordinate_frame_id,
                geometry_artifact_id=geometry_artifact_id,
                metrics_artifact_id=metrics_artifact_id,
                region_count=len(regions),
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO region_sets(
                    region_set_id, scene_revision_id, producer_job_id,
                    extractor_id, extractor_version, input_hash,
                    coordinate_frame_id, geometry_artifact_id,
                    metrics_artifact_id, region_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    region_set.region_set_id,
                    region_set.scene_revision_id,
                    region_set.producer_job_id,
                    region_set.extractor_id,
                    region_set.extractor_version,
                    region_set.input_hash,
                    region_set.coordinate_frame_id,
                    region_set.geometry_artifact_id,
                    region_set.metrics_artifact_id,
                    region_set.region_count,
                    region_set.created_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO regions(
                    region_set_id, region_id, source_member_id,
                    ordinal, label, tags,
                    classification, strategy_hint, footprint_area_xy,
                    boundary_perimeter_xy, min_x, min_y, min_z,
                    max_x, max_y, max_z, metrics_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    (
                        region_set.region_set_id,
                        region.region_id,
                        region.source_member_id,
                        region.ordinal,
                        region.label,
                        region.tags,
                        region.classification,
                        region.strategy_hint,
                        region.footprint_area_xy,
                        region.boundary_perimeter_xy,
                        region.min_x,
                        region.min_y,
                        region.min_z,
                        region.max_x,
                        region.max_y,
                        region.max_z,
                        region.metrics_json,
                    )
                    for region in regions
                ],
            )
        return region_set

    def get_region_set(self, region_set_id: str) -> RegionSetInfo:
        row = self.database.connection.execute(
            """
            SELECT region_set_id, scene_revision_id, producer_job_id,
                   extractor_id, extractor_version, input_hash,
                   coordinate_frame_id, geometry_artifact_id,
                   metrics_artifact_id, region_count, created_at
            FROM region_sets
            WHERE region_set_id = ?
            """,
            (region_set_id,),
        ).fetchone()
        if row is None:
            raise RegionSetNotFoundError(region_set_id)
        return _region_set_from_row(row)

    def get_region_set_by_job(
        self,
        producer_job_id: str,
    ) -> RegionSetInfo | None:
        row = self.database.connection.execute(
            """
            SELECT region_set_id, scene_revision_id, producer_job_id,
                   extractor_id, extractor_version, input_hash,
                   coordinate_frame_id, geometry_artifact_id,
                   metrics_artifact_id, region_count, created_at
            FROM region_sets
            WHERE producer_job_id = ?
            """,
            (producer_job_id,),
        ).fetchone()
        return _region_set_from_row(row) if row is not None else None

    def list_regions(
        self,
        region_set_id: str,
    ) -> tuple[RegionSummary, ...]:
        if self.database.connection.execute(
            "SELECT 1 FROM region_sets WHERE region_set_id = ?",
            (region_set_id,),
        ).fetchone() is None:
            raise RegionSetNotFoundError(region_set_id)
        rows = self.database.connection.execute(
            """
            SELECT region_id, source_member_id, ordinal, label, tags,
                   classification, strategy_hint, footprint_area_xy,
                   boundary_perimeter_xy, min_x, min_y, min_z,
                   max_x, max_y, max_z, metrics_json
            FROM regions
            WHERE region_set_id = ?
            ORDER BY ordinal
            """,
            (region_set_id,),
        )
        return tuple(_region_from_row(row) for row in rows)

    @staticmethod
    def _validate_regions(regions: tuple[RegionSummary, ...]) -> None:
        ids = [region.region_id.strip() for region in regions]
        if any(not region_id for region_id in ids):
            raise ValueError("region_id is required")
        if len(ids) != len(set(ids)):
            raise ValueError("region_id must be unique within a RegionSet")
        ordinals = [region.ordinal for region in regions]
        if sorted(ordinals) != list(range(len(regions))):
            raise ValueError(
                "Region ordinals must be contiguous and zero-based"
            )
        for region in regions:
            if region.min_x > region.max_x:
                raise ValueError("Region min_x cannot exceed max_x")
            if region.min_y > region.max_y:
                raise ValueError("Region min_y cannot exceed max_y")
            if region.min_z > region.max_z:
                raise ValueError("Region min_z cannot exceed max_z")


def _region_set_from_row(row: object) -> RegionSetInfo:
    return RegionSetInfo(
        region_set_id=row["region_set_id"],
        scene_revision_id=row["scene_revision_id"],
        producer_job_id=row["producer_job_id"],
        extractor_id=row["extractor_id"],
        extractor_version=row["extractor_version"],
        input_hash=row["input_hash"],
        coordinate_frame_id=row["coordinate_frame_id"],
        geometry_artifact_id=row["geometry_artifact_id"],
        metrics_artifact_id=row["metrics_artifact_id"],
        region_count=int(row["region_count"]),
        created_at=row["created_at"],
    )


def _region_from_row(row: object) -> RegionSummary:
    return RegionSummary(
        region_id=row["region_id"],
        ordinal=int(row["ordinal"]),
        label=row["label"],
        tags=row["tags"],
        classification=row["classification"],
        strategy_hint=row["strategy_hint"],
        footprint_area_xy=(
            float(row["footprint_area_xy"])
            if row["footprint_area_xy"] is not None
            else None
        ),
        boundary_perimeter_xy=(
            float(row["boundary_perimeter_xy"])
            if row["boundary_perimeter_xy"] is not None
            else None
        ),
        min_x=float(row["min_x"]),
        min_y=float(row["min_y"]),
        min_z=float(row["min_z"]),
        max_x=float(row["max_x"]),
        max_y=float(row["max_y"]),
        max_z=float(row["max_z"]),
        metrics_json=row["metrics_json"],
        source_member_id=row["source_member_id"],
    )
