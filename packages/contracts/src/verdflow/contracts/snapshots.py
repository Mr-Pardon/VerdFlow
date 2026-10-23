"""Immutable project runtime snapshot summaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyPackageSnapshotInfo:
    strategy_package_snapshot_id: str
    strategy_id: str
    strategy_version: str
    package_hash: str
    package_artifact_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TargetAssetUseSnapshotInfo:
    target_asset_use_snapshot_id: str
    target_type: str
    provider_id: str
    source_kind: str
    source_id: str
    source_generation: int | None
    source_hash: str
    selected_asset_count: int
    semantic_hash: str
    snapshot_artifact_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class EffectiveBindingSnapshotInfo:
    effective_binding_snapshot_id: str
    strategy_package_snapshot_id: str
    target_asset_use_snapshot_id: str
    target_type: str
    source_profile_id: str | None
    source_profile_hash: str | None
    override_applied: bool
    semantic_hash: str
    snapshot_artifact_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AssetAssignmentSetInfo:
    asset_assignment_set_id: str
    point_set_id: str
    effective_binding_snapshot_id: str
    producer_job_id: str
    target_type: str
    resolver_id: str
    resolver_version: str
    random_seed: int
    semantic_hash: str
    assignment_artifact_id: str
    point_count: int
    created_at: str


@dataclass(frozen=True, slots=True)
class AssetAssignmentSummary:
    asset_id: str
    point_count: int
