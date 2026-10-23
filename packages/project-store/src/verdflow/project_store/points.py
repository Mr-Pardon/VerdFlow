"""Immutable PlantingRun and PointSet persistence."""

from __future__ import annotations

import math
import uuid

from verdflow.contracts import (
    PlantingRunInfo,
    PointRoleSummary,
    PointSetInfo,
    PointSetKind,
)

from .database import ProjectDatabase
from .errors import (
    ArtifactNotFoundError,
    PlantingRunNotFoundError,
    PointSetConflictError,
    PointSetNotFoundError,
    RegionSetNotFoundError,
    SceneRevisionNotFoundError,
)


class PointRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create_generated_point_set(
        self,
        *,
        scene_revision_id: str,
        region_set_id: str,
        producer_job_id: str,
        strategy_id: str,
        strategy_version: str,
        strategy_package_hash: str,
        generator_id: str,
        generator_version: str,
        random_seed: int,
        input_hash: str,
        coordinate_frame_id: str,
        point_artifact_id: str,
        point_count: int,
        bounds: tuple[float, float, float, float, float, float] | None,
        roles: tuple[PointRoleSummary, ...],
        created_at: str,
    ) -> tuple[PlantingRunInfo, PointSetInfo]:
        values = self._required_values(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_package_hash=strategy_package_hash,
            generator_id=generator_id,
            generator_version=generator_version,
            input_hash=input_hash,
        )
        if (
            not isinstance(random_seed, int)
            or isinstance(random_seed, bool)
            or random_seed < -(2**63)
            or random_seed > 2**63 - 1
        ):
            raise ValueError(
                "random_seed must fit a signed 64-bit integer"
            )
        self._validate_summary(point_count, bounds, roles)
        with self.database.transaction() as connection:
            existing_run = connection.execute(
                """
                SELECT planting_run_id, scene_revision_id, region_set_id,
                       producer_job_id, strategy_id, strategy_version,
                       strategy_package_hash, generator_id,
                       generator_version, random_seed, input_hash, created_at
                FROM planting_runs
                WHERE producer_job_id = ?
                """,
                (producer_job_id,),
            ).fetchone()
            existing_set = connection.execute(
                """
                SELECT point_set_id, planting_run_id, parent_point_set_id,
                       producer_job_id, kind, producer_id, producer_version,
                       input_hash, coordinate_frame_id, point_artifact_id,
                       resolution_policy, point_count, sampled_count,
                       hit_count, miss_count, min_x, min_y, min_z,
                       max_x, max_y, max_z, created_at
                FROM point_sets
                WHERE producer_job_id = ?
                """,
                (producer_job_id,),
            ).fetchone()
            if existing_run is not None or existing_set is not None:
                if existing_run is None or existing_set is None:
                    raise PointSetConflictError(
                        "Generated PointSet persistence is incomplete"
                    )
                run = _planting_run_from_row(existing_run)
                point_set = _point_set_from_row(existing_set)
                self._assert_generated_match(
                    run,
                    point_set,
                    scene_revision_id=scene_revision_id,
                    region_set_id=region_set_id,
                    strategy_id=values["strategy_id"],
                    strategy_version=values["strategy_version"],
                    strategy_package_hash=values[
                        "strategy_package_hash"
                    ],
                    generator_id=values["generator_id"],
                    generator_version=values["generator_version"],
                    random_seed=random_seed,
                    input_hash=values["input_hash"],
                    coordinate_frame_id=coordinate_frame_id,
                    point_artifact_id=point_artifact_id,
                    point_count=point_count,
                    bounds=bounds,
                    roles=roles,
                )
                return run, point_set

            scene = connection.execute(
                """
                SELECT coordinate_frame_id
                FROM scene_revisions
                WHERE scene_revision_id = ?
                """,
                (scene_revision_id,),
            ).fetchone()
            if scene is None:
                raise SceneRevisionNotFoundError(scene_revision_id)
            region_set = connection.execute(
                """
                SELECT scene_revision_id, coordinate_frame_id
                FROM region_sets
                WHERE region_set_id = ?
                """,
                (region_set_id,),
            ).fetchone()
            if region_set is None:
                raise RegionSetNotFoundError(region_set_id)
            if region_set["scene_revision_id"] != scene_revision_id:
                raise PointSetConflictError(
                    "PlantingRun RegionSet differs from SceneRevision"
                )
            if (
                scene["coordinate_frame_id"] != coordinate_frame_id
                or region_set["coordinate_frame_id"]
                != coordinate_frame_id
            ):
                raise PointSetConflictError(
                    "PointSet coordinate frame differs from its inputs"
                )
            self._require_succeeded_job(connection, producer_job_id)
            self._require_artifact(connection, point_artifact_id)

            run = PlantingRunInfo(
                planting_run_id=f"planting_{uuid.uuid4().hex}",
                scene_revision_id=scene_revision_id,
                region_set_id=region_set_id,
                producer_job_id=producer_job_id,
                strategy_id=values["strategy_id"],
                strategy_version=values["strategy_version"],
                strategy_package_hash=values["strategy_package_hash"],
                generator_id=values["generator_id"],
                generator_version=values["generator_version"],
                random_seed=random_seed,
                input_hash=values["input_hash"],
                created_at=created_at,
            )
            point_set = _new_point_set(
                planting_run_id=run.planting_run_id,
                parent_point_set_id=None,
                producer_job_id=producer_job_id,
                kind=PointSetKind.GENERATED,
                producer_id=run.generator_id,
                producer_version=run.generator_version,
                input_hash=run.input_hash,
                coordinate_frame_id=coordinate_frame_id,
                point_artifact_id=point_artifact_id,
                resolution_policy="generated_position",
                point_count=point_count,
                sampled_count=0,
                hit_count=0,
                miss_count=0,
                bounds=bounds,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO planting_runs(
                    planting_run_id, scene_revision_id, region_set_id,
                    producer_job_id, strategy_id, strategy_version,
                    strategy_package_hash, generator_id,
                    generator_version, random_seed, input_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.planting_run_id,
                    run.scene_revision_id,
                    run.region_set_id,
                    run.producer_job_id,
                    run.strategy_id,
                    run.strategy_version,
                    run.strategy_package_hash,
                    run.generator_id,
                    run.generator_version,
                    run.random_seed,
                    run.input_hash,
                    run.created_at,
                ),
            )
            self._insert_point_set(connection, point_set, roles)
        return run, point_set

    def create_elevation_point_set(
        self,
        *,
        parent_point_set_id: str,
        producer_job_id: str,
        producer_id: str,
        producer_version: str,
        input_hash: str,
        coordinate_frame_id: str,
        point_artifact_id: str,
        resolution_policy: str,
        point_count: int,
        hit_count: int,
        miss_count: int,
        bounds: tuple[float, float, float, float, float, float] | None,
        roles: tuple[PointRoleSummary, ...],
        created_at: str,
    ) -> PointSetInfo:
        values = self._required_values(
            producer_id=producer_id,
            producer_version=producer_version,
            input_hash=input_hash,
            resolution_policy=resolution_policy,
        )
        self._validate_summary(point_count, bounds, roles)
        if hit_count < 0 or miss_count < 0:
            raise ValueError("Elevation counts cannot be negative")
        if hit_count + miss_count != point_count:
            raise ValueError(
                "Elevation hit and miss counts must equal point_count"
            )
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT point_set_id, planting_run_id, parent_point_set_id,
                       producer_job_id, kind, producer_id, producer_version,
                       input_hash, coordinate_frame_id, point_artifact_id,
                       resolution_policy, point_count, sampled_count,
                       hit_count, miss_count, min_x, min_y, min_z,
                       max_x, max_y, max_z, created_at
                FROM point_sets
                WHERE producer_job_id = ?
                """,
                (producer_job_id,),
            ).fetchone()
            if existing is not None:
                point_set = _point_set_from_row(existing)
                expected = (
                    parent_point_set_id,
                    PointSetKind.ELEVATION_RESOLVED,
                    values["producer_id"],
                    values["producer_version"],
                    values["input_hash"],
                    coordinate_frame_id,
                    point_artifact_id,
                    values["resolution_policy"],
                    point_count,
                    point_count,
                    hit_count,
                    miss_count,
                    bounds,
                    roles,
                )
                actual = (
                    point_set.parent_point_set_id,
                    point_set.kind,
                    point_set.producer_id,
                    point_set.producer_version,
                    point_set.input_hash,
                    point_set.coordinate_frame_id,
                    point_set.point_artifact_id,
                    point_set.resolution_policy,
                    point_set.point_count,
                    point_set.sampled_count,
                    point_set.hit_count,
                    point_set.miss_count,
                    _bounds_from_point_set(point_set),
                    self._list_roles(connection, point_set.point_set_id),
                )
                if actual != expected:
                    raise PointSetConflictError(
                        "Producer Job is already bound to another PointSet"
                    )
                return point_set

            parent_row = connection.execute(
                """
                SELECT point_set_id, planting_run_id, parent_point_set_id,
                       producer_job_id, kind, producer_id, producer_version,
                       input_hash, coordinate_frame_id, point_artifact_id,
                       resolution_policy, point_count, sampled_count,
                       hit_count, miss_count, min_x, min_y, min_z,
                       max_x, max_y, max_z, created_at
                FROM point_sets
                WHERE point_set_id = ?
                """,
                (parent_point_set_id,),
            ).fetchone()
            if parent_row is None:
                raise PointSetNotFoundError(parent_point_set_id)
            parent = _point_set_from_row(parent_row)
            if parent.kind is not PointSetKind.GENERATED:
                raise PointSetConflictError(
                    "Elevation must derive from a generated PointSet"
                )
            if parent.coordinate_frame_id != coordinate_frame_id:
                raise PointSetConflictError(
                    "Elevation PointSet coordinate frame differs from parent"
                )
            if parent.point_count != point_count:
                raise PointSetConflictError(
                    "Elevation PointSet count differs from parent"
                )
            parent_roles = self._list_roles(
                connection,
                parent.point_set_id,
            )
            if parent_roles != roles:
                raise PointSetConflictError(
                    "Elevation sampling cannot change role allocation"
                )
            self._require_succeeded_job(connection, producer_job_id)
            self._require_artifact(connection, point_artifact_id)
            point_set = _new_point_set(
                planting_run_id=parent.planting_run_id,
                parent_point_set_id=parent.point_set_id,
                producer_job_id=producer_job_id,
                kind=PointSetKind.ELEVATION_RESOLVED,
                producer_id=values["producer_id"],
                producer_version=values["producer_version"],
                input_hash=values["input_hash"],
                coordinate_frame_id=coordinate_frame_id,
                point_artifact_id=point_artifact_id,
                resolution_policy=values["resolution_policy"],
                point_count=point_count,
                sampled_count=point_count,
                hit_count=hit_count,
                miss_count=miss_count,
                bounds=bounds,
                created_at=created_at,
            )
            self._insert_point_set(connection, point_set, roles)
        return point_set

    def get_planting_run(self, planting_run_id: str) -> PlantingRunInfo:
        row = self.database.connection.execute(
            """
            SELECT planting_run_id, scene_revision_id, region_set_id,
                   producer_job_id, strategy_id, strategy_version,
                   strategy_package_hash, generator_id, generator_version,
                   random_seed, input_hash, created_at
            FROM planting_runs
            WHERE planting_run_id = ?
            """,
            (planting_run_id,),
        ).fetchone()
        if row is None:
            raise PlantingRunNotFoundError(planting_run_id)
        return _planting_run_from_row(row)

    def get_planting_run_by_job(
        self,
        producer_job_id: str,
    ) -> PlantingRunInfo | None:
        row = self.database.connection.execute(
            """
            SELECT planting_run_id, scene_revision_id, region_set_id,
                   producer_job_id, strategy_id, strategy_version,
                   strategy_package_hash, generator_id, generator_version,
                   random_seed, input_hash, created_at
            FROM planting_runs
            WHERE producer_job_id = ?
            """,
            (producer_job_id,),
        ).fetchone()
        return _planting_run_from_row(row) if row is not None else None

    def get_point_set(self, point_set_id: str) -> PointSetInfo:
        row = self.database.connection.execute(
            """
            SELECT point_set_id, planting_run_id, parent_point_set_id,
                   producer_job_id, kind, producer_id, producer_version,
                   input_hash, coordinate_frame_id, point_artifact_id,
                   resolution_policy, point_count, sampled_count,
                   hit_count, miss_count, min_x, min_y, min_z,
                   max_x, max_y, max_z, created_at
            FROM point_sets
            WHERE point_set_id = ?
            """,
            (point_set_id,),
        ).fetchone()
        if row is None:
            raise PointSetNotFoundError(point_set_id)
        return _point_set_from_row(row)

    def get_point_set_by_job(
        self,
        producer_job_id: str,
    ) -> PointSetInfo | None:
        row = self.database.connection.execute(
            """
            SELECT point_set_id, planting_run_id, parent_point_set_id,
                   producer_job_id, kind, producer_id, producer_version,
                   input_hash, coordinate_frame_id, point_artifact_id,
                   resolution_policy, point_count, sampled_count,
                   hit_count, miss_count, min_x, min_y, min_z,
                   max_x, max_y, max_z, created_at
            FROM point_sets
            WHERE producer_job_id = ?
            """,
            (producer_job_id,),
        ).fetchone()
        return _point_set_from_row(row) if row is not None else None

    def list_point_sets(
        self,
        planting_run_id: str,
    ) -> tuple[PointSetInfo, ...]:
        if self.database.connection.execute(
            "SELECT 1 FROM planting_runs WHERE planting_run_id = ?",
            (planting_run_id,),
        ).fetchone() is None:
            raise PlantingRunNotFoundError(planting_run_id)
        rows = self.database.connection.execute(
            """
            SELECT point_set_id, planting_run_id, parent_point_set_id,
                   producer_job_id, kind, producer_id, producer_version,
                   input_hash, coordinate_frame_id, point_artifact_id,
                   resolution_policy, point_count, sampled_count,
                   hit_count, miss_count, min_x, min_y, min_z,
                   max_x, max_y, max_z, created_at
            FROM point_sets
            WHERE planting_run_id = ?
            ORDER BY created_at, point_set_id
            """,
            (planting_run_id,),
        )
        return tuple(_point_set_from_row(row) for row in rows)

    def list_point_set_roles(
        self,
        point_set_id: str,
    ) -> tuple[PointRoleSummary, ...]:
        if self.database.connection.execute(
            "SELECT 1 FROM point_sets WHERE point_set_id = ?",
            (point_set_id,),
        ).fetchone() is None:
            raise PointSetNotFoundError(point_set_id)
        return self._list_roles(
            self.database.connection,
            point_set_id,
        )

    def _assert_generated_match(
        self,
        run: PlantingRunInfo,
        point_set: PointSetInfo,
        *,
        scene_revision_id: str,
        region_set_id: str,
        strategy_id: str,
        strategy_version: str,
        strategy_package_hash: str,
        generator_id: str,
        generator_version: str,
        random_seed: int,
        input_hash: str,
        coordinate_frame_id: str,
        point_artifact_id: str,
        point_count: int,
        bounds: tuple[float, float, float, float, float, float] | None,
        roles: tuple[PointRoleSummary, ...],
    ) -> None:
        expected = (
            scene_revision_id,
            region_set_id,
            strategy_id,
            strategy_version,
            strategy_package_hash,
            generator_id,
            generator_version,
            random_seed,
            input_hash,
            run.planting_run_id,
            None,
            PointSetKind.GENERATED,
            generator_id,
            generator_version,
            input_hash,
            coordinate_frame_id,
            point_artifact_id,
            "generated_position",
            point_count,
            0,
            0,
            0,
            bounds,
            roles,
        )
        actual = (
            run.scene_revision_id,
            run.region_set_id,
            run.strategy_id,
            run.strategy_version,
            run.strategy_package_hash,
            run.generator_id,
            run.generator_version,
            run.random_seed,
            run.input_hash,
            point_set.planting_run_id,
            point_set.parent_point_set_id,
            point_set.kind,
            point_set.producer_id,
            point_set.producer_version,
            point_set.input_hash,
            point_set.coordinate_frame_id,
            point_set.point_artifact_id,
            point_set.resolution_policy,
            point_set.point_count,
            point_set.sampled_count,
            point_set.hit_count,
            point_set.miss_count,
            _bounds_from_point_set(point_set),
            self._list_roles(
                self.database.connection,
                point_set.point_set_id,
            ),
        )
        if actual != expected:
            raise PointSetConflictError(
                "Producer Job is already bound to another PlantingRun"
            )

    @staticmethod
    def _insert_point_set(
        connection: object,
        point_set: PointSetInfo,
        roles: tuple[PointRoleSummary, ...],
    ) -> None:
        connection.execute(
            """
            INSERT INTO point_sets(
                point_set_id, planting_run_id, parent_point_set_id,
                producer_job_id, kind, producer_id, producer_version,
                input_hash, coordinate_frame_id, point_artifact_id,
                resolution_policy, point_count, sampled_count,
                hit_count, miss_count, min_x, min_y, min_z,
                max_x, max_y, max_z, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            """,
            (
                point_set.point_set_id,
                point_set.planting_run_id,
                point_set.parent_point_set_id,
                point_set.producer_job_id,
                point_set.kind.value,
                point_set.producer_id,
                point_set.producer_version,
                point_set.input_hash,
                point_set.coordinate_frame_id,
                point_set.point_artifact_id,
                point_set.resolution_policy,
                point_set.point_count,
                point_set.sampled_count,
                point_set.hit_count,
                point_set.miss_count,
                point_set.min_x,
                point_set.min_y,
                point_set.min_z,
                point_set.max_x,
                point_set.max_y,
                point_set.max_z,
                point_set.created_at,
            ),
        )
        connection.executemany(
            """
            INSERT INTO point_set_roles(
                point_set_id, role_key, point_count
            ) VALUES (?, ?, ?)
            """,
            [
                (
                    point_set.point_set_id,
                    role.role_key,
                    role.point_count,
                )
                for role in roles
            ],
        )

    @staticmethod
    def _list_roles(
        connection: object,
        point_set_id: str,
    ) -> tuple[PointRoleSummary, ...]:
        rows = connection.execute(
            """
            SELECT role_key, point_count
            FROM point_set_roles
            WHERE point_set_id = ?
            ORDER BY role_key
            """,
            (point_set_id,),
        )
        return tuple(
            PointRoleSummary(
                role_key=row["role_key"],
                point_count=int(row["point_count"]),
            )
            for row in rows
        )

    @staticmethod
    def _require_succeeded_job(connection: object, job_id: str) -> None:
        row = connection.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None or row["status"] != "succeeded":
            raise PointSetConflictError(
                "PointSet producer Job must have succeeded"
            )

    @staticmethod
    def _require_artifact(connection: object, artifact_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone() is None:
            raise ArtifactNotFoundError(artifact_id)

    @staticmethod
    def _required_values(**values: str) -> dict[str, str]:
        normalized = {key: value.strip() for key, value in values.items()}
        missing = [key for key, value in normalized.items() if not value]
        if missing:
            raise ValueError(f"{missing[0]} is required")
        return normalized

    @staticmethod
    def _validate_summary(
        point_count: int,
        bounds: tuple[float, float, float, float, float, float] | None,
        roles: tuple[PointRoleSummary, ...],
    ) -> None:
        if point_count < 0:
            raise ValueError("point_count cannot be negative")
        if point_count == 0 and bounds is not None:
            raise ValueError("Empty PointSet cannot have bounds")
        if point_count > 0:
            if bounds is None:
                raise ValueError("Non-empty PointSet requires bounds")
            if not all(math.isfinite(value) for value in bounds):
                raise ValueError("PointSet bounds must be finite")
            if (
                bounds[0] > bounds[3]
                or bounds[1] > bounds[4]
                or bounds[2] > bounds[5]
            ):
                raise ValueError("PointSet bounds are invalid")
        normalized_roles = tuple(
            sorted(roles, key=lambda item: item.role_key)
        )
        if roles != normalized_roles:
            raise ValueError("Point roles must be ordered by role_key")
        keys = [role.role_key.strip() for role in roles]
        if any(not key for key in keys):
            raise ValueError("role_key is required")
        if len(keys) != len(set(keys)):
            raise ValueError("role_key must be unique within a PointSet")
        if any(
            not isinstance(role.point_count, int)
            or isinstance(role.point_count, bool)
            or role.point_count <= 0
            for role in roles
        ):
            raise ValueError("Role point_count must be positive")
        if sum(role.point_count for role in roles) != point_count:
            raise ValueError(
                "Role point counts must equal PointSet point_count"
            )


def _new_point_set(
    *,
    planting_run_id: str,
    parent_point_set_id: str | None,
    producer_job_id: str,
    kind: PointSetKind,
    producer_id: str,
    producer_version: str,
    input_hash: str,
    coordinate_frame_id: str,
    point_artifact_id: str,
    resolution_policy: str,
    point_count: int,
    sampled_count: int,
    hit_count: int,
    miss_count: int,
    bounds: tuple[float, float, float, float, float, float] | None,
    created_at: str,
) -> PointSetInfo:
    values = bounds or (None, None, None, None, None, None)
    return PointSetInfo(
        point_set_id=f"points_{uuid.uuid4().hex}",
        planting_run_id=planting_run_id,
        parent_point_set_id=parent_point_set_id,
        producer_job_id=producer_job_id,
        kind=kind,
        producer_id=producer_id,
        producer_version=producer_version,
        input_hash=input_hash,
        coordinate_frame_id=coordinate_frame_id,
        point_artifact_id=point_artifact_id,
        resolution_policy=resolution_policy,
        point_count=point_count,
        sampled_count=sampled_count,
        hit_count=hit_count,
        miss_count=miss_count,
        min_x=values[0],
        min_y=values[1],
        min_z=values[2],
        max_x=values[3],
        max_y=values[4],
        max_z=values[5],
        created_at=created_at,
    )


def _planting_run_from_row(row: object) -> PlantingRunInfo:
    return PlantingRunInfo(
        planting_run_id=row["planting_run_id"],
        scene_revision_id=row["scene_revision_id"],
        region_set_id=row["region_set_id"],
        producer_job_id=row["producer_job_id"],
        strategy_id=row["strategy_id"],
        strategy_version=row["strategy_version"],
        strategy_package_hash=row["strategy_package_hash"],
        generator_id=row["generator_id"],
        generator_version=row["generator_version"],
        random_seed=int(row["random_seed"]),
        input_hash=row["input_hash"],
        created_at=row["created_at"],
    )


def _point_set_from_row(row: object) -> PointSetInfo:
    return PointSetInfo(
        point_set_id=row["point_set_id"],
        planting_run_id=row["planting_run_id"],
        parent_point_set_id=row["parent_point_set_id"],
        producer_job_id=row["producer_job_id"],
        kind=PointSetKind(row["kind"]),
        producer_id=row["producer_id"],
        producer_version=row["producer_version"],
        input_hash=row["input_hash"],
        coordinate_frame_id=row["coordinate_frame_id"],
        point_artifact_id=row["point_artifact_id"],
        resolution_policy=row["resolution_policy"],
        point_count=int(row["point_count"]),
        sampled_count=int(row["sampled_count"]),
        hit_count=int(row["hit_count"]),
        miss_count=int(row["miss_count"]),
        min_x=_optional_float(row["min_x"]),
        min_y=_optional_float(row["min_y"]),
        min_z=_optional_float(row["min_z"]),
        max_x=_optional_float(row["max_x"]),
        max_y=_optional_float(row["max_y"]),
        max_z=_optional_float(row["max_z"]),
        created_at=row["created_at"],
    )


def _bounds_from_point_set(
    point_set: PointSetInfo,
) -> tuple[float, float, float, float, float, float] | None:
    if point_set.point_count == 0:
        return None
    assert point_set.min_x is not None
    assert point_set.min_y is not None
    assert point_set.min_z is not None
    assert point_set.max_x is not None
    assert point_set.max_y is not None
    assert point_set.max_z is not None
    return (
        point_set.min_x,
        point_set.min_y,
        point_set.min_z,
        point_set.max_x,
        point_set.max_y,
        point_set.max_z,
    )


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None
