"""Finalize validated planting decisions into immutable project snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    AssetAssignmentSummary,
    TargetAssetUseSnapshotInfo,
    EffectiveBindingSnapshotInfo,
    PointSetInfo,
    StrategyPackageSnapshotInfo,
)
from verdflow.planting import (
    AssignmentSetReader,
    CanonicalRowHasher,
    PlantingDocumentError,
    PointSetReader,
    canonical_json_bytes,
    extract_target_asset_use_snapshot,
    semantic_hash,
    point_set_row_hasher,
    validate_assignment_header,
    validate_assignment_row,
    validate_assignment_set,
    validate_effective_binding_snapshot,
    validate_persisted_point_set_summary,
    validate_point_header,
)
from verdflow.project_store import ProjectStore


class ProjectSnapshotFinalizationError(RuntimeError):
    """Raised when domain documents cannot become project evidence."""

class _UnorderedAssignments(Exception):
    pass



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
        point_set, effective_info, effective = self._assignment_inputs(
            point_set_id, effective_binding_snapshot_id,
        )
        points = validate_persisted_point_set_summary(
            self._read_json(point_set.point_artifact_id, "PointSet"),
            expected_point_count=point_set.point_count,
        )
        self._check_assignment_header(assignment_set, point_set, effective_info, semantic_hash(points))
        self._validate_assignment_rows(points, effective, assignment_set)
        artifact = self.store.put_bytes(
            canonical_json_bytes(assignment_set),
            media_type="application/vnd.verdflow.asset-assignment-set+json",
            schema_id="verdflow.asset-assignment-set",
            schema_version="1",
        )
        counts = Counter(row["asset_id"] for row in assignment_set["assignments"])
        return self._record_assignments(
            point_set_id, effective_binding_snapshot_id, producer_job_id,
            assignment_set, artifact.artifact_id, counts,
        )

    def finalize_assignment_artifact(
        self, *, point_set_id: str, effective_binding_snapshot_id: str,
        producer_job_id: str, artifact_id: str,
    ) -> AssetAssignmentSetInfo:
        """Verify a Job's immutable output without materializing its array."""
        point, effective_info, effective = self._assignment_inputs(point_set_id, effective_binding_snapshot_id)
        allowed = self._allowed_assets(effective)
        try:
            header, counts, content_hash = self._verify_ordered_assignments(point, artifact_id, effective_info, allowed)
        except _UnorderedAssignments:
            header, counts, content_hash = self._verify_indexed_assignments(point, artifact_id, effective_info, allowed)
        if content_hash != self.store.get_artifact(artifact_id).content_hash:
            # Old valid, noncanonical worker output must still become canonical.
            def write(stream: BinaryIO) -> None:
                hasher = CanonicalRowHasher("assignments", {})
                stream.write(hasher.prefix)
                with self.store.open_artifact(artifact_id) as source:
                    for row in AssignmentSetReader(source, expected_point_count=point.point_count).rows():
                        stream.write(hasher.row(row))
                stream.write(hasher.suffix(header))

            artifact_id = self.store.write_artifact(
                write, media_type="application/vnd.verdflow.asset-assignment-set+json",
                schema_id="verdflow.asset-assignment-set", schema_version="1",
            ).artifact_id
        return self._record_assignments(
            point_set_id, effective_binding_snapshot_id, producer_job_id, header, artifact_id, counts,
        )

    def _verify_ordered_assignments(
        self, point: PointSetInfo, artifact_id: str, binding: EffectiveBindingSnapshotInfo,
        allowed: dict[str, set[str]],
    ) -> tuple[dict[str, Any], Counter[str], str]:
        point_hash = point_set_row_hasher(kind=point.kind.value, count=point.point_count)
        assignment_hash = CanonicalRowHasher("assignments", {})
        counts: Counter[str] = Counter()
        with self.store.open_artifact(point.point_artifact_id) as points_file, self.store.open_artifact(artifact_id) as assignments_file:
            points = PointSetReader(points_file, expected_point_count=point.point_count)
            assignments = AssignmentSetReader(assignments_file, expected_point_count=point.point_count)
            for ordinal, (row, assignment) in enumerate(zip(points.rows(), assignments.rows(), strict=True)):
                if row["id"] != assignment.get("point_id"):
                    raise _UnorderedAssignments
                validate_assignment_row(assignment, index=ordinal)
                self._check_asset_for_point(row, assignment["role_key"], assignment["asset_id"], allowed)
                point_hash.row(row)
                assignment_hash.row(assignment)
                counts[assignment["asset_id"]] += 1
        return self._finish_assignment_artifact(point, binding, points.header, assignments.header, point_hash, assignment_hash, counts)

    def _verify_indexed_assignments(
        self, point: PointSetInfo, artifact_id: str, binding: EffectiveBindingSnapshotInfo,
        allowed: dict[str, set[str]],
    ) -> tuple[dict[str, Any], Counter[str], str]:
        scratch = self.store.root / "runtime"
        scratch.mkdir(exist_ok=True)
        point_hash = point_set_row_hasher(kind=point.kind.value, count=point.point_count)
        assignment_hash = CanonicalRowHasher("assignments", {})
        counts: Counter[str] = Counter()
        try:
            with TemporaryDirectory(prefix="assignment-verify-", dir=scratch) as directory:
                with closing(sqlite3.connect(Path(directory) / "rows.sqlite")) as index:
                    index.execute("PRAGMA cache_size = -2048")
                    index.execute("PRAGMA journal_mode = OFF")
                    index.execute("PRAGMA synchronous = OFF")
                    index.execute("CREATE TABLE rows (id TEXT PRIMARY KEY, role TEXT, asset TEXT) WITHOUT ROWID")
                    with self.store.open_artifact(artifact_id) as source:
                        assignments = AssignmentSetReader(source, expected_point_count=point.point_count)
                        for ordinal, row in enumerate(assignments.rows()):
                            validate_assignment_row(row, index=ordinal)
                            if row["asset_id"] not in allowed.get(row["role_key"], ()):
                                raise ProjectSnapshotFinalizationError(f"Asset is not allowed for role {row['role_key']}")
                            assignment_hash.row(row)
                            counts[row["asset_id"]] += 1
                            index.execute("INSERT INTO rows VALUES (?, ?, ?)", (row["point_id"], row["role_key"], row["asset_id"]))
                    with self.store.open_artifact(point.point_artifact_id) as source:
                        points = PointSetReader(source, expected_point_count=point.point_count)
                        for row in points.rows():
                            key = (row["id"],)
                            matched = index.execute("SELECT role, asset FROM rows WHERE id = ?", key).fetchone()
                            if matched is None:
                                raise ProjectSnapshotFinalizationError("AssignmentSet point identifiers differ from PointSet")
                            index.execute("DELETE FROM rows WHERE id = ?", key)
                            self._check_asset_for_point(row, matched[0], matched[1], allowed)
                            point_hash.row(row)
        except sqlite3.IntegrityError as error:
            raise PlantingDocumentError("AssignmentSet contains a duplicate point identifier") from error
        except sqlite3.Error as error:
            raise ProjectSnapshotFinalizationError("AssignmentSet verification could not write its temporary index") from error
        return self._finish_assignment_artifact(point, binding, points.header, assignments.header, point_hash, assignment_hash, counts)

    def _finish_assignment_artifact(
        self, point: PointSetInfo, binding: EffectiveBindingSnapshotInfo,
        point_header: dict[str, Any], header: dict[str, Any],
        point_hash: CanonicalRowHasher, assignment_hash: CanonicalRowHasher,
        counts: Counter[str],
    ) -> tuple[dict[str, Any], Counter[str], str]:
        validate_point_header(point_header)
        validate_assignment_header(header)
        self._check_assignment_header(header, point, binding, point_hash.finish(point_header))
        hash_payload = {key: value for key, value in header.items() if key != "semantic_hash"}
        if header["semantic_hash"] != assignment_hash.finish(hash_payload):
            raise PlantingDocumentError("AssignmentSet semantic_hash mismatch")
        return header, counts, assignment_hash.finish(header).removeprefix("sha256:")

    def _assignment_inputs(
        self, point_set_id: str, effective_binding_snapshot_id: str,
    ) -> tuple[PointSetInfo, EffectiveBindingSnapshotInfo, dict[str, Any]]:
        point_set = self.store.get_point_set(point_set_id)
        effective_info = self.store.get_effective_binding_snapshot(effective_binding_snapshot_id)
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
            or point_artifact.schema_version not in {"2", "3"}
        ):
            raise ProjectSnapshotFinalizationError(
                "PointSet artifact schema is unsupported"
            )
        effective = self._read_json(
            effective_info.snapshot_artifact_id,
            "EffectiveBindingSnapshot",
        )
        effective = validate_effective_binding_snapshot(effective)
        if effective["semantic_hash"] != effective_info.semantic_hash:
            raise ProjectSnapshotFinalizationError("Effective binding hash differs from its Core record")
        return point_set, effective_info, effective

    @staticmethod
    def _check_assignment_header(
        assignment_set: dict[str, Any], point_set: PointSetInfo,
        effective_info: EffectiveBindingSnapshotInfo, point_hash: str,
    ) -> None:
        if assignment_set["point_set_hash"] != point_hash:
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
    def _record_assignments(
        self, point_set_id: str, effective_binding_snapshot_id: str, producer_job_id: str,
        assignment_set: dict[str, Any], artifact_id: str, counts: Counter[str],
    ) -> AssetAssignmentSetInfo:
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
            assignment_artifact_id=artifact_id,
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
        allowed = ProjectSnapshotService._allowed_assets(effective)
        rows = assignment_set["assignments"]
        if {row["point_id"] for row in rows} != set(point_roles):
            raise ProjectSnapshotFinalizationError(
                "AssignmentSet point identifiers differ from PointSet"
            )
        for row in rows:
            point = {"id": row["point_id"], "role_key": point_roles[row["point_id"]]}
            ProjectSnapshotService._check_asset_for_point(point, row["role_key"], row["asset_id"], allowed)

    @staticmethod
    def _allowed_assets(effective: dict[str, Any]) -> dict[str, set[str]]:
        return {
            role: {candidate["asset_id"] for candidate in assignment["candidates"]}
            for role, assignment in effective["assignments"].items()
        }

    @staticmethod
    def _check_asset_for_point(
        point: dict[str, Any], role: str, asset_id: str, allowed: dict[str, set[str]],
    ) -> None:
        expected_role = point["role_key"]
        if role != expected_role:
            raise ProjectSnapshotFinalizationError(f"Assignment role differs for point {point['id']}")
        if asset_id not in allowed.get(expected_role, ()):
            raise ProjectSnapshotFinalizationError(f"Asset is not allowed for role {expected_role}")
