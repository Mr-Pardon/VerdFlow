"""Immutable runtime snapshot persistence for reproducible planting runs."""

from __future__ import annotations

import sqlite3
import uuid

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    AssetAssignmentSummary,
    TargetAssetUseSnapshotInfo,
    EffectiveBindingSnapshotInfo,
    StrategyPackageSnapshotInfo,
)

from .database import ProjectDatabase
from .errors import (
    ArtifactNotFoundError,
    PlantingRunNotFoundError,
    PointSetNotFoundError,
    SnapshotConflictError,
    SnapshotNotFoundError,
)


class ProjectSnapshotRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create_strategy_package_snapshot(
        self,
        *,
        planting_run_id: str,
        package_artifact_id: str,
        created_at: str,
    ) -> StrategyPackageSnapshotInfo:
        with self.database.transaction() as connection:
            run = connection.execute(
                """
                SELECT strategy_id, strategy_version, strategy_package_hash
                FROM planting_runs WHERE planting_run_id = ?
                """,
                (planting_run_id,),
            ).fetchone()
            if run is None:
                raise PlantingRunNotFoundError(planting_run_id)
            existing = self._package_by_run(connection, planting_run_id)
            if existing is not None:
                if existing.package_artifact_id != package_artifact_id:
                    raise SnapshotConflictError(
                        "PlantingRun already references another strategy package snapshot"
                )
                return existing
            self._require_artifact(
                connection,
                package_artifact_id,
                schema_id="verdflow.strategy-package",
                schema_version="1",
            )
            snapshot = self._package_by_hash(
                connection,
                run["strategy_package_hash"],
            )
            if snapshot is None:
                snapshot = StrategyPackageSnapshotInfo(
                    strategy_package_snapshot_id=f"sps_{uuid.uuid4().hex}",
                    strategy_id=run["strategy_id"],
                    strategy_version=run["strategy_version"],
                    package_hash=run["strategy_package_hash"],
                    package_artifact_id=package_artifact_id,
                    created_at=created_at,
                )
                connection.execute(
                    """
                    INSERT INTO strategy_package_snapshots(
                        strategy_package_snapshot_id, strategy_id,
                        strategy_version, package_hash,
                        package_artifact_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.strategy_package_snapshot_id,
                        snapshot.strategy_id,
                        snapshot.strategy_version,
                        snapshot.package_hash,
                        snapshot.package_artifact_id,
                        snapshot.created_at,
                    ),
                )
            elif (
                snapshot.strategy_id != run["strategy_id"]
                or snapshot.strategy_version != run["strategy_version"]
                or snapshot.package_artifact_id != package_artifact_id
            ):
                raise SnapshotConflictError(
                    "Strategy package hash identifies different package facts"
                )
            connection.execute(
                """
                INSERT INTO planting_run_strategy_snapshots(
                    planting_run_id, strategy_package_snapshot_id
                ) VALUES (?, ?)
                """,
                (planting_run_id, snapshot.strategy_package_snapshot_id),
            )
            return snapshot

    def get_strategy_package_snapshot(
        self,
        strategy_package_snapshot_id: str,
    ) -> StrategyPackageSnapshotInfo:
        row = self.database.connection.execute(
            """
            SELECT strategy_package_snapshot_id,
                   strategy_id, strategy_version, package_hash,
                   package_artifact_id, created_at
            FROM strategy_package_snapshots
            WHERE strategy_package_snapshot_id = ?
            """,
            (strategy_package_snapshot_id,),
        ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(strategy_package_snapshot_id)
        return _package_from_row(row)

    def get_strategy_package_snapshot_by_run(
        self,
        planting_run_id: str,
    ) -> StrategyPackageSnapshotInfo | None:
        return self._package_by_run(
            self.database.connection,
            planting_run_id,
        )

    def create_target_asset_use_snapshot(
        self,
        *,
        target_type: str,
        provider_id: str,
        source_kind: str,
        source_id: str,
        source_generation: int | None,
        source_hash: str,
        selected_asset_count: int,
        semantic_hash: str,
        snapshot_artifact_id: str,
        created_at: str,
    ) -> TargetAssetUseSnapshotInfo:
        values = self._required_values(
            target_type=target_type,
            provider_id=provider_id,
            source_kind=source_kind,
            source_id=source_id,
            source_hash=source_hash,
            semantic_hash=semantic_hash,
        )
        if values["source_kind"] not in {"catalog", "profile", "draft"}:
            raise ValueError("source_kind must be catalog, profile or draft")
        if values["source_kind"] == "catalog":
            if source_generation is None or source_generation < 1:
                raise ValueError("Catalog source_generation must be positive")
        elif source_generation is not None:
            raise ValueError("Profile source_generation must be omitted")
        if selected_asset_count <= 0:
            raise ValueError("selected_asset_count must be positive")
        with self.database.transaction() as connection:
            existing = self._asset_use_by_hash(
                connection,
                values["semantic_hash"],
            )
            expected = (
                values["target_type"],
                values["provider_id"],
                values["source_kind"],
                values["source_id"],
                source_generation,
                values["source_hash"],
                selected_asset_count,
                snapshot_artifact_id,
            )
            if existing is not None:
                actual = (
                    existing.target_type,
                    existing.provider_id,
                    existing.source_kind,
                    existing.source_id,
                    existing.source_generation,
                    existing.source_hash,
                    existing.selected_asset_count,
                    existing.snapshot_artifact_id,
                )
                if actual != expected:
                    raise SnapshotConflictError(
                        "Target asset use hash identifies different facts"
                    )
                return existing
            self._require_artifact(
                connection,
                snapshot_artifact_id,
                schema_id="verdflow.target-asset-use-snapshot",
                schema_version="1",
            )
            snapshot = TargetAssetUseSnapshotInfo(
                target_asset_use_snapshot_id=f"taus_{uuid.uuid4().hex}",
                source_generation=source_generation,
                selected_asset_count=selected_asset_count,
                snapshot_artifact_id=snapshot_artifact_id,
                created_at=created_at,
                **values,
            )
            connection.execute(
                """
                INSERT INTO target_asset_use_snapshots(
                    target_asset_use_snapshot_id, target_type, provider_id,
                    source_kind, source_id, source_generation, source_hash,
                    selected_asset_count, semantic_hash,
                    snapshot_artifact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.target_asset_use_snapshot_id,
                    snapshot.target_type,
                    snapshot.provider_id,
                    snapshot.source_kind,
                    snapshot.source_id,
                    snapshot.source_generation,
                    snapshot.source_hash,
                    snapshot.selected_asset_count,
                    snapshot.semantic_hash,
                    snapshot.snapshot_artifact_id,
                    snapshot.created_at,
                ),
            )
            return snapshot

    def get_target_asset_use_snapshot(
        self,
        target_asset_use_snapshot_id: str,
    ) -> TargetAssetUseSnapshotInfo:
        row = self.database.connection.execute(
            """
            SELECT target_asset_use_snapshot_id, target_type, provider_id,
                   source_kind, source_id, source_generation, source_hash,
                   selected_asset_count, semantic_hash,
                   snapshot_artifact_id, created_at
            FROM target_asset_use_snapshots
            WHERE target_asset_use_snapshot_id = ?
            """,
            (target_asset_use_snapshot_id,),
        ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(target_asset_use_snapshot_id)
        return _asset_use_from_row(row)

    def create_effective_binding_snapshot(
        self,
        *,
        strategy_package_snapshot_id: str,
        target_asset_use_snapshot_id: str,
        target_type: str,
        source_profile_id: str | None,
        source_profile_hash: str | None,
        override_applied: bool,
        semantic_hash: str,
        snapshot_artifact_id: str,
        created_at: str,
    ) -> EffectiveBindingSnapshotInfo:
        target_type = self._required_values(target_type=target_type)["target_type"]
        source = (source_profile_id, source_profile_hash)
        if any(value is None for value in source) and any(
            value is not None for value in source
        ):
            raise ValueError("source profile fields must be all present or all absent")
        with self.database.transaction() as connection:
            package = self._package_by_id(connection, strategy_package_snapshot_id)
            if package is None:
                raise SnapshotNotFoundError(strategy_package_snapshot_id)
            asset_use = self._asset_use_by_id(
                connection,
                target_asset_use_snapshot_id,
            )
            if asset_use is None:
                raise SnapshotNotFoundError(target_asset_use_snapshot_id)
            if asset_use.target_type != target_type:
                raise SnapshotConflictError(
                    "Effective binding target differs from target asset use"
                )
            existing = self._effective_by_hash(connection, semantic_hash)
            expected = (
                strategy_package_snapshot_id,
                target_asset_use_snapshot_id,
                target_type,
                source_profile_id,
                source_profile_hash,
                bool(override_applied),
                snapshot_artifact_id,
            )
            if existing is not None:
                actual = (
                    existing.strategy_package_snapshot_id,
                    existing.target_asset_use_snapshot_id,
                    existing.target_type,
                    existing.source_profile_id,
                    existing.source_profile_hash,
                    existing.override_applied,
                    existing.snapshot_artifact_id,
                )
                if actual != expected:
                    raise SnapshotConflictError(
                        "Effective binding semantic hash identifies different facts"
                    )
                return existing
            self._require_artifact(
                connection,
                snapshot_artifact_id,
                schema_id="verdflow.effective-binding-snapshot",
                schema_version="1",
            )
            snapshot = EffectiveBindingSnapshotInfo(
                effective_binding_snapshot_id=f"ebs_{uuid.uuid4().hex}",
                strategy_package_snapshot_id=strategy_package_snapshot_id,
                target_asset_use_snapshot_id=target_asset_use_snapshot_id,
                target_type=target_type,
                source_profile_id=source_profile_id,
                source_profile_hash=source_profile_hash,
                override_applied=bool(override_applied),
                semantic_hash=semantic_hash,
                snapshot_artifact_id=snapshot_artifact_id,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO effective_binding_snapshots(
                    effective_binding_snapshot_id,
                    strategy_package_snapshot_id,
                    target_asset_use_snapshot_id, target_type,
                    source_profile_id, source_profile_hash,
                    override_applied, semantic_hash,
                    snapshot_artifact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.effective_binding_snapshot_id,
                    snapshot.strategy_package_snapshot_id,
                    snapshot.target_asset_use_snapshot_id,
                    snapshot.target_type,
                    snapshot.source_profile_id,
                    snapshot.source_profile_hash,
                    int(snapshot.override_applied),
                    snapshot.semantic_hash,
                    snapshot.snapshot_artifact_id,
                    snapshot.created_at,
                ),
            )
            return snapshot

    def get_effective_binding_snapshot(
        self,
        effective_binding_snapshot_id: str,
    ) -> EffectiveBindingSnapshotInfo:
        row = self.database.connection.execute(
            """
            SELECT effective_binding_snapshot_id,
                   strategy_package_snapshot_id,
                   target_asset_use_snapshot_id, target_type,
                   source_profile_id, source_profile_hash,
                   override_applied, semantic_hash,
                   snapshot_artifact_id, created_at
            FROM effective_binding_snapshots
            WHERE effective_binding_snapshot_id = ?
            """,
            (effective_binding_snapshot_id,),
        ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(effective_binding_snapshot_id)
        return _effective_from_row(row)

    def create_assignment_set(
        self,
        *,
        point_set_id: str,
        effective_binding_snapshot_id: str,
        producer_job_id: str,
        target_type: str,
        resolver_id: str,
        resolver_version: str,
        random_seed: int,
        semantic_hash: str,
        assignment_artifact_id: str,
        point_count: int,
        assets: tuple[AssetAssignmentSummary, ...],
        created_at: str,
    ) -> AssetAssignmentSetInfo:
        values = self._required_values(
            target_type=target_type,
            resolver_id=resolver_id,
            resolver_version=resolver_version,
            semantic_hash=semantic_hash,
        )
        self._validate_assignment_summary(point_count, assets)
        if random_seed < 0:
            raise ValueError("random_seed cannot be negative")
        with self.database.transaction() as connection:
            existing = self._assignment_by_job(connection, producer_job_id)
            if existing is not None:
                self._assert_assignment_match(
                    connection,
                    existing,
                    point_set_id=point_set_id,
                    effective_binding_snapshot_id=effective_binding_snapshot_id,
                    producer_job_id=producer_job_id,
                    random_seed=random_seed,
                    assignment_artifact_id=assignment_artifact_id,
                    point_count=point_count,
                    assets=assets,
                    **values,
                )
                return existing
            self._require_succeeded_job(connection, producer_job_id)
            point = connection.execute(
                "SELECT point_count FROM point_sets WHERE point_set_id = ?",
                (point_set_id,),
            ).fetchone()
            if point is None:
                raise PointSetNotFoundError(point_set_id)
            if int(point["point_count"]) != point_count:
                raise SnapshotConflictError(
                    "Assignment count differs from PointSet count"
                )
            effective = self._effective_by_id(
                connection,
                effective_binding_snapshot_id,
            )
            if effective is None:
                raise SnapshotNotFoundError(effective_binding_snapshot_id)
            if effective.target_type != values["target_type"]:
                raise SnapshotConflictError(
                    "Assignment target differs from effective binding target"
                )
            self._require_artifact(
                connection,
                assignment_artifact_id,
                schema_id="verdflow.asset-assignment-set",
                schema_version="1",
            )
            snapshot = AssetAssignmentSetInfo(
                asset_assignment_set_id=f"aas_{uuid.uuid4().hex}",
                point_set_id=point_set_id,
                effective_binding_snapshot_id=effective_binding_snapshot_id,
                producer_job_id=producer_job_id,
                target_type=values["target_type"],
                resolver_id=values["resolver_id"],
                resolver_version=values["resolver_version"],
                random_seed=random_seed,
                semantic_hash=values["semantic_hash"],
                assignment_artifact_id=assignment_artifact_id,
                point_count=point_count,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO asset_assignment_sets(
                    asset_assignment_set_id, point_set_id,
                    effective_binding_snapshot_id, producer_job_id,
                    target_type, resolver_id, resolver_version, random_seed,
                    semantic_hash, assignment_artifact_id, point_count,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.asset_assignment_set_id,
                    snapshot.point_set_id,
                    snapshot.effective_binding_snapshot_id,
                    snapshot.producer_job_id,
                    snapshot.target_type,
                    snapshot.resolver_id,
                    snapshot.resolver_version,
                    snapshot.random_seed,
                    snapshot.semantic_hash,
                    snapshot.assignment_artifact_id,
                    snapshot.point_count,
                    snapshot.created_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO asset_assignment_set_assets(
                    asset_assignment_set_id, asset_id, point_count
                ) VALUES (?, ?, ?)
                """,
                [
                    (snapshot.asset_assignment_set_id, item.asset_id, item.point_count)
                    for item in assets
                ],
            )
            return snapshot

    def get_assignment_set(
        self,
        asset_assignment_set_id: str,
    ) -> AssetAssignmentSetInfo:
        row = self.database.connection.execute(
            _ASSIGNMENT_SELECT + " WHERE asset_assignment_set_id = ?",
            (asset_assignment_set_id,),
        ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(asset_assignment_set_id)
        return _assignment_from_row(row)

    def get_assignment_set_by_job(
        self,
        producer_job_id: str,
    ) -> AssetAssignmentSetInfo | None:
        return self._assignment_by_job(
            self.database.connection,
            producer_job_id,
        )

    def list_assignment_assets(
        self,
        asset_assignment_set_id: str,
    ) -> tuple[AssetAssignmentSummary, ...]:
        if self.database.connection.execute(
            "SELECT 1 FROM asset_assignment_sets WHERE asset_assignment_set_id = ?",
            (asset_assignment_set_id,),
        ).fetchone() is None:
            raise SnapshotNotFoundError(asset_assignment_set_id)
        return self._assignment_assets(
            self.database.connection,
            asset_assignment_set_id,
        )

    @staticmethod
    def _package_by_run(
        connection: object,
        planting_run_id: str,
    ) -> StrategyPackageSnapshotInfo | None:
        row = connection.execute(
            """
            SELECT snapshot.strategy_package_snapshot_id,
                   snapshot.strategy_id, snapshot.strategy_version,
                   snapshot.package_hash, snapshot.package_artifact_id,
                   snapshot.created_at
            FROM planting_run_strategy_snapshots AS link
            JOIN strategy_package_snapshots AS snapshot
              ON snapshot.strategy_package_snapshot_id =
                 link.strategy_package_snapshot_id
            WHERE link.planting_run_id = ?
            """,
            (planting_run_id,),
        ).fetchone()
        return _package_from_row(row) if row is not None else None

    @staticmethod
    def _package_by_id(connection: object, snapshot_id: str):
        row = connection.execute(
            "SELECT * FROM strategy_package_snapshots WHERE strategy_package_snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return _package_from_row(row) if row is not None else None

    @staticmethod
    def _package_by_hash(connection: object, package_hash: str):
        row = connection.execute(
            "SELECT * FROM strategy_package_snapshots WHERE package_hash = ?",
            (package_hash,),
        ).fetchone()
        return _package_from_row(row) if row is not None else None

    @staticmethod
    def _asset_use_by_hash(connection: object, value: str):
        row = connection.execute(
            "SELECT * FROM target_asset_use_snapshots WHERE semantic_hash = ?",
            (value,),
        ).fetchone()
        return _asset_use_from_row(row) if row is not None else None

    @staticmethod
    def _asset_use_by_id(connection: object, snapshot_id: str):
        row = connection.execute(
            "SELECT * FROM target_asset_use_snapshots "
            "WHERE target_asset_use_snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return _asset_use_from_row(row) if row is not None else None

    @staticmethod
    def _effective_by_hash(connection: object, value: str):
        row = connection.execute(
            "SELECT * FROM effective_binding_snapshots WHERE semantic_hash = ?",
            (value,),
        ).fetchone()
        return _effective_from_row(row) if row is not None else None

    @staticmethod
    def _effective_by_id(connection: object, snapshot_id: str):
        row = connection.execute(
            "SELECT * FROM effective_binding_snapshots WHERE effective_binding_snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return _effective_from_row(row) if row is not None else None

    @staticmethod
    def _assignment_by_job(connection: object, job_id: str):
        row = connection.execute(
            _ASSIGNMENT_SELECT + " WHERE producer_job_id = ?",
            (job_id,),
        ).fetchone()
        return _assignment_from_row(row) if row is not None else None

    @staticmethod
    def _assignment_assets(connection: object, assignment_set_id: str):
        rows = connection.execute(
            """
            SELECT asset_id, point_count
            FROM asset_assignment_set_assets
            WHERE asset_assignment_set_id = ? ORDER BY asset_id
            """,
            (assignment_set_id,),
        )
        return tuple(
            AssetAssignmentSummary(row["asset_id"], int(row["point_count"]))
            for row in rows
        )

    def _assert_assignment_match(
        self,
        connection: object,
        existing: AssetAssignmentSetInfo,
        *,
        assets: tuple[AssetAssignmentSummary, ...],
        **expected_values: object,
    ) -> None:
        expected = (
            expected_values["point_set_id"],
            expected_values["effective_binding_snapshot_id"],
            expected_values["producer_job_id"],
            expected_values["target_type"],
            expected_values["resolver_id"],
            expected_values["resolver_version"],
            expected_values["random_seed"],
            expected_values["semantic_hash"],
            expected_values["assignment_artifact_id"],
            expected_values["point_count"],
            tuple(sorted(assets, key=lambda item: item.asset_id)),
        )
        actual = (
            existing.point_set_id,
            existing.effective_binding_snapshot_id,
            existing.producer_job_id,
            existing.target_type,
            existing.resolver_id,
            existing.resolver_version,
            existing.random_seed,
            existing.semantic_hash,
            existing.assignment_artifact_id,
            existing.point_count,
            self._assignment_assets(connection, existing.asset_assignment_set_id),
        )
        if actual != expected:
            raise SnapshotConflictError(
                "Producer Job already identifies another assignment set"
            )

    @staticmethod
    def _require_succeeded_job(connection: object, job_id: str) -> None:
        row = connection.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None or row["status"] != "succeeded":
            raise SnapshotConflictError(
                "Assignment producer Job must have succeeded"
            )

    @staticmethod
    def _require_artifact(
        connection: object,
        artifact_id: str,
        *,
        schema_id: str,
        schema_version: str,
    ) -> None:
        row = connection.execute(
            "SELECT schema_id, schema_version FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact_id)
        if row["schema_id"] != schema_id or row["schema_version"] != schema_version:
            raise SnapshotConflictError(
                f"Artifact {artifact_id} does not satisfy {schema_id} v{schema_version}"
            )

    @staticmethod
    def _required_values(**values: str) -> dict[str, str]:
        normalized = {key: value.strip() for key, value in values.items()}
        for key, value in normalized.items():
            if not value:
                raise ValueError(f"{key} is required")
        return normalized

    @staticmethod
    def _validate_assignment_summary(
        point_count: int,
        assets: tuple[AssetAssignmentSummary, ...],
    ) -> None:
        if point_count < 0:
            raise ValueError("point_count cannot be negative")
        if any(item.point_count <= 0 or not item.asset_id.strip() for item in assets):
            raise ValueError("Assignment asset summaries must be positive")
        if len({item.asset_id for item in assets}) != len(assets):
            raise ValueError("Assignment asset summaries must be unique")
        if sum(item.point_count for item in assets) != point_count:
            raise ValueError("Assignment asset counts must equal point_count")


_ASSIGNMENT_SELECT = """
SELECT asset_assignment_set_id, point_set_id,
       effective_binding_snapshot_id, producer_job_id, target_type,
       resolver_id, resolver_version, random_seed, semantic_hash,
       assignment_artifact_id, point_count, created_at
FROM asset_assignment_sets
"""


def _package_from_row(row: sqlite3.Row) -> StrategyPackageSnapshotInfo:
    return StrategyPackageSnapshotInfo(**dict(row))


def _asset_use_from_row(row: sqlite3.Row) -> TargetAssetUseSnapshotInfo:
    values = dict(row)
    values["selected_asset_count"] = int(values["selected_asset_count"])
    if values["source_generation"] is not None:
        values["source_generation"] = int(values["source_generation"])
    return TargetAssetUseSnapshotInfo(**values)


def _effective_from_row(row: sqlite3.Row) -> EffectiveBindingSnapshotInfo:
    values = dict(row)
    values["override_applied"] = bool(values["override_applied"])
    return EffectiveBindingSnapshotInfo(**values)


def _assignment_from_row(row: sqlite3.Row) -> AssetAssignmentSetInfo:
    values = dict(row)
    values["random_seed"] = int(values["random_seed"])
    values["point_count"] = int(values["point_count"])
    return AssetAssignmentSetInfo(**values)
