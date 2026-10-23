"""Finalize validated planting decisions into immutable project snapshots."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    AssetAssignmentSummary,
    TargetAssetUseSnapshotInfo,
    EffectiveBindingSnapshotInfo,
    StrategyPackageSnapshotInfo,
)
from verdflow.planting import (
    canonical_json_bytes,
    extract_target_asset_use_snapshot,
    semantic_hash,
    validate_assignment_set,
    validate_effective_binding_snapshot,
    validate_persisted_point_set_summary,
)
from verdflow.project_store import ProjectStore


class ProjectSnapshotFinalizationError(RuntimeError):
    """Raised when domain documents cannot become project evidence."""


@dataclass(frozen=True, slots=True)
class BindingSnapshotResult:
    asset_use: TargetAssetUseSnapshotInfo
    effective_binding: EffectiveBindingSnapshotInfo


class ProjectSnapshotService:
    """Application orchestration over Planting validation and ProjectStore."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def finalize_strategy_package(
        self,
        planting_run_id: str,
        package_artifact_id: str,
    ) -> StrategyPackageSnapshotInfo:
        return self.store.create_strategy_package_snapshot(
            planting_run_id=planting_run_id,
            package_artifact_id=package_artifact_id,
        )

    def finalize_effective_binding(
        self,
        planting_run_id: str,
        value: dict[str, Any],
    ) -> BindingSnapshotResult:
        effective = validate_effective_binding_snapshot(value)
        run = self.store.get_planting_run(planting_run_id)
        strategy = effective["strategy"]
        if (
            strategy["strategy_id"] != run.strategy_id
            or strategy["strategy_version"] != run.strategy_version
        ):
            raise ProjectSnapshotFinalizationError(
                "Effective binding strategy differs from PlantingRun"
            )
        package = self.store.get_strategy_package_snapshot_by_run(
            planting_run_id
        )
        if package is None:
            raise ProjectSnapshotFinalizationError(
                "Strategy package must be snapshotted before binding finalization"
            )

        asset_use = extract_target_asset_use_snapshot(effective)
        asset_use_artifact = self.store.put_bytes(
            canonical_json_bytes(asset_use),
            media_type=(
                "application/vnd.verdflow.target-asset-use-snapshot+json"
            ),
            schema_id="verdflow.target-asset-use-snapshot",
            schema_version="1",
        )
        target = asset_use["target"]
        source = asset_use["source"]
        asset_snapshot = self.store.create_target_asset_use_snapshot(
            target_type=target["target_type"],
            provider_id=target["provider_id"],
            source_kind=source["kind"],
            source_id=source.get("catalog_id") or source.get("profile_id") or source["semantic_hash"],
            source_generation=source.get("generation"),
            source_hash=source["semantic_hash"],
            selected_asset_count=len(asset_use["assets"]),
            semantic_hash=asset_use["semantic_hash"],
            snapshot_artifact_id=asset_use_artifact.artifact_id,
        )

        effective_artifact = self.store.put_bytes(
            canonical_json_bytes(effective),
            media_type=(
                "application/vnd.verdflow.effective-binding-snapshot+json"
            ),
            schema_id="verdflow.effective-binding-snapshot",
            schema_version="1",
        )
        source = effective["source_profile"]
        binding = self.store.create_effective_binding_snapshot(
            strategy_package_snapshot_id=(
                package.strategy_package_snapshot_id
            ),
            target_asset_use_snapshot_id=(
                asset_snapshot.target_asset_use_snapshot_id
            ),
            target_type=effective["target"]["target_type"],
            source_profile_id=(source["profile_id"] if source else None),
            source_profile_hash=(source["semantic_hash"] if source else None),
            override_applied=effective["override_applied"],
            semantic_hash=effective["semantic_hash"],
            snapshot_artifact_id=effective_artifact.artifact_id,
        )
        return BindingSnapshotResult(asset_snapshot, binding)

    def finalize_asset_assignments(
        self,
        *,
        point_set_id: str,
        effective_binding_snapshot_id: str,
        producer_job_id: str,
        value: dict[str, Any],
    ) -> AssetAssignmentSetInfo:
        assignment_set = validate_assignment_set(value)
        point_set = self.store.get_point_set(point_set_id)
        effective_info = self.store.get_effective_binding_snapshot(
            effective_binding_snapshot_id
        )
        point_package = self.store.get_strategy_package_snapshot_by_run(
            point_set.planting_run_id
        )
        if point_package is None:
            raise ProjectSnapshotFinalizationError(
                "PointSet PlantingRun has no strategy package snapshot"
            )
        if (
            point_package.strategy_package_snapshot_id
            != effective_info.strategy_package_snapshot_id
        ):
            raise ProjectSnapshotFinalizationError(
                "Effective binding package differs from PointSet PlantingRun"
            )
        point_artifact = self.store.get_artifact(point_set.point_artifact_id)
        if (
            point_artifact.schema_id != "verdflow.points"
            or point_artifact.schema_version != "2"
        ):
            raise ProjectSnapshotFinalizationError(
                "PointSet artifact schema is unsupported"
            )
        points = validate_persisted_point_set_summary(
            self._read_json(point_set.point_artifact_id, "PointSet"),
            expected_point_count=point_set.point_count,
        )
        effective = self._read_json(
            effective_info.snapshot_artifact_id,
            "EffectiveBindingSnapshot",
        )
        effective = validate_effective_binding_snapshot(effective)

        if assignment_set["point_set_hash"] != semantic_hash(points):
            raise ProjectSnapshotFinalizationError(
                "AssignmentSet point_set_hash differs from PointSet"
            )
        if (
            assignment_set["effective_binding_snapshot_hash"]
            != effective_info.semantic_hash
        ):
            raise ProjectSnapshotFinalizationError(
                "AssignmentSet binding hash differs from persisted snapshot"
            )
        if assignment_set["target_type"] != effective_info.target_type:
            raise ProjectSnapshotFinalizationError(
                "AssignmentSet target differs from persisted snapshot"
            )
        if assignment_set["point_count"] != point_set.point_count:
            raise ProjectSnapshotFinalizationError(
                "AssignmentSet count differs from PointSet"
            )
        self._validate_assignment_rows(points, effective, assignment_set)

        artifact = self.store.put_bytes(
            canonical_json_bytes(assignment_set),
            media_type="application/vnd.verdflow.asset-assignment-set+json",
            schema_id="verdflow.asset-assignment-set",
            schema_version="1",
        )
        counts = Counter(
            row["asset_id"] for row in assignment_set["assignments"]
        )
        resolver = assignment_set["resolver"]
        return self.store.create_asset_assignment_set(
            point_set_id=point_set_id,
            effective_binding_snapshot_id=effective_binding_snapshot_id,
            producer_job_id=producer_job_id,
            target_type=assignment_set["target_type"],
            resolver_id=resolver["id"],
            resolver_version=resolver["version"],
            random_seed=assignment_set["random_seed"],
            semantic_hash=assignment_set["semantic_hash"],
            assignment_artifact_id=artifact.artifact_id,
            point_count=assignment_set["point_count"],
            assets=tuple(
                AssetAssignmentSummary(asset_id, counts[asset_id])
                for asset_id in sorted(counts)
            ),
        )

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        try:
            with self.store.open_artifact(artifact_id) as stream:
                value = json.load(stream)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectSnapshotFinalizationError(
                f"{label} artifact is not valid UTF-8 JSON"
            ) from error
        if not isinstance(value, dict):
            raise ProjectSnapshotFinalizationError(
                f"{label} artifact root must be an object"
            )
        return value

    @staticmethod
    def _validate_assignment_rows(
        points: dict[str, Any],
        effective: dict[str, Any],
        assignment_set: dict[str, Any],
    ) -> None:
        point_roles = {
            point["id"]: point["role_key"] for point in points["points"]
        }
        allowed = {
            role_key: {
                candidate["asset_id"]
                for candidate in assignment["candidates"]
            }
            for role_key, assignment in effective["assignments"].items()
        }
        rows = assignment_set["assignments"]
        if {row["point_id"] for row in rows} != set(point_roles):
            raise ProjectSnapshotFinalizationError(
                "AssignmentSet point identifiers differ from PointSet"
            )
        for row in rows:
            expected_role = point_roles[row["point_id"]]
            if row["role_key"] != expected_role:
                raise ProjectSnapshotFinalizationError(
                    f"Assignment role differs for point {row['point_id']}"
                )
            if row["asset_id"] not in allowed.get(expected_role, set()):
                raise ProjectSnapshotFinalizationError(
                    f"Asset is not allowed for role {expected_role}"
                )
