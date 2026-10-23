"""Submit and finalize deterministic target asset assignment Jobs."""

from __future__ import annotations

import json
import copy
from dataclasses import dataclass, field
from typing import Any

from verdflow.contracts import (
    ASSET_ASSIGNMENT_COMMAND,
    ASSET_ASSIGNMENT_COMMAND_SCHEMA,
    ASSET_ASSIGNMENT_COMMAND_VERSION,
    AssetAssignmentSetInfo,
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    JobStatus,
    PointSetKind,
)
from verdflow.planting import (
    build_effective_binding_from_draft,
    build_effective_binding_from_profile,
    build_effective_binding_snapshot,
    canonical_json_bytes,
    semantic_hash,
    role_enabled,
    profile_generation_args,
    validate_profile,
)
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .library import (
    AssetCatalogLibraryPort,
    RoleBindingProfileLibraryPort,
    StrategyDocumentPort,
)
from .project_snapshots import ProjectSnapshotService


class AssetAssignmentError(RuntimeError):
    """Raised when an Assignment Job cannot become project evidence."""


@dataclass(frozen=True, slots=True)
class AssetAssignmentRequest:
    point_set_id: str
    target_type: str
    random_seed: int
    profile_id: str | None = None
    catalog_id: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    profile_snapshot: dict[str, Any] | None = None


class AssetAssignmentService:
    """Freeze exact library choices, then submit a target-neutral Job."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        strategy_documents: StrategyDocumentPort,
        catalogs: AssetCatalogLibraryPort,
        profiles: RoleBindingProfileLibraryPort,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._strategy_documents = strategy_documents
        self._catalogs = catalogs
        self._profiles = profiles
        self._snapshots = ProjectSnapshotService(store)

    def freeze_role_assignments(self, profile_id: str | None, overrides: dict[str, Any]) -> dict[str, Any]:
        """Freeze role switches and existing candidates before generation starts."""
        assignments = copy.deepcopy(self._load_profile(profile_id)["assignments"] if profile_id else {})
        for key, value in overrides.items():
            if not isinstance(value, dict):
                raise ValueError("Role assignment must be an object")
            assignments[key] = {**assignments.get(key, {}), **copy.deepcopy(value)}
        for assignment in assignments.values():
            assignment["enabled"] = role_enabled(assignment)
        if assignments and not any(item["enabled"] for item in assignments.values()):
            raise ValueError("At least one role must be enabled")
        return assignments

    def freeze_generation_args(self, profile_id: str | None, args: dict[str, Any]) -> dict[str, Any]:
        return profile_generation_args(self._load_profile(profile_id) if profile_id else None, args)

    def freeze_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        return copy.deepcopy(self._load_profile(profile_id)) if profile_id else None

    def submit(
        self,
        request: AssetAssignmentRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        point_set = self._store.get_point_set(
            self._required_text(request.point_set_id, "point_set_id")
        )
        if point_set.kind not in {
            PointSetKind.GENERATED,
            PointSetKind.ELEVATION_RESOLVED,
        }:
            raise ValueError("Assignment input must be a planting PointSet")
        run = self._store.get_planting_run(point_set.planting_run_id)
        package = self._store.get_strategy_package_snapshot_by_run(
            run.planting_run_id
        )
        if package is None or package.package_hash != run.strategy_package_hash:
            raise ValueError("PointSet has no matching strategy package snapshot")

        strategy = self._strategy_documents.load_strategy_document(
            run.strategy_package_hash,
            package.package_artifact_id,
        )
        if not isinstance(strategy, dict):
            raise ValueError("Strategy document must be a JSON object")
        if (
            self._required_text(strategy.get("strategy_id"), "strategy_id")
            != run.strategy_id
            or self._required_text(
                strategy.get("strategy_version"),
                "strategy_version",
            )
            != run.strategy_version
        ):
            raise ValueError("Strategy document differs from the PlantingRun")

        target_type = self._required_text(request.target_type, "target_type")
        if not isinstance(request.overrides, dict):
            raise ValueError("overrides must be a JSON object")
        if request.profile_id is not None:
            profile = (
                validate_profile(copy.deepcopy(request.profile_snapshot))
                if request.profile_snapshot is not None
                else self._load_profile(request.profile_id)
            )
            if profile["profile_id"] != request.profile_id:
                raise ValueError("Frozen Profile identity differs from the request")
            if profile["target"]["target_type"] != target_type:
                raise ValueError("Profile target does not match the request")
            effective = build_effective_binding_from_profile(
                strategy,
                profile,
                overrides=request.overrides,
            )
        elif request.catalog_id is None:
            effective = build_effective_binding_from_draft(
                strategy, request.overrides, target_type=target_type,
            )
        else:
            effective = self._effective_from_catalog(
                request,
                strategy,
                target_type,
            )
        binding = self._snapshots.finalize_effective_binding(
            run.planting_run_id,
            effective,
        ).effective_binding
        command = {
            "schema": ASSET_ASSIGNMENT_COMMAND_SCHEMA,
            "version": ASSET_ASSIGNMENT_COMMAND_VERSION,
            "operation": "assign_assets",
            "point_set_id": point_set.point_set_id,
            "points_artifact_id": point_set.point_artifact_id,
            "effective_binding_snapshot_id": (
                binding.effective_binding_snapshot_id
            ),
            "effective_binding_artifact_id": binding.snapshot_artifact_id,
            "target_type": binding.target_type,
            "random_seed": self._seed(request.random_seed),
        }
        content = canonical_json_bytes(command)
        artifact = self._store.put_bytes(
            content,
            media_type="application/vnd.verdflow.command+json",
            schema_id=ASSET_ASSIGNMENT_COMMAND_SCHEMA,
            schema_version=ASSET_ASSIGNMENT_COMMAND_VERSION,
        )
        default_key = (
            "asset-assignment:"
            + semantic_hash(command).removeprefix("sha256:")
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=ASSET_ASSIGNMENT_COMMAND,
                command_version=ASSET_ASSIGNMENT_COMMAND_VERSION,
                artifact_id=artifact.artifact_id,
            ),
            idempotency_key=idempotency_key or default_key,
            max_attempts=2,
            retention_class=JobRetentionClass.RUN,
        )

    def _effective_from_catalog(
        self,
        request: AssetAssignmentRequest,
        strategy: dict[str, Any],
        target_type: str,
    ) -> dict[str, Any]:
        catalog_id = self._required_text(request.catalog_id, "catalog_id")
        catalog_info = self._catalogs.get_asset_catalog(
            target_type,
            catalog_id,
        )
        if catalog_info is None:
            raise ValueError("Asset Catalog is not installed")
        catalog_document = self._catalogs.load_asset_catalog_document(
            target_type,
            catalog_id,
        )
        if (
            catalog_document.target_type != target_type
            or catalog_document.catalog_id != catalog_id
            or catalog_document.semantic_hash != catalog_info.semantic_hash
        ):
            raise ValueError("Asset Catalog document differs from its metadata")
        catalog = self._decode_canonical(
            catalog_document.canonical_json,
            "Asset Catalog",
        )

        return build_effective_binding_snapshot(
            strategy,
            catalog,
            catalog_generation=catalog_info.generation,
            overrides=request.overrides,
        )

    def _load_profile(self, profile_id: str) -> dict[str, Any]:
        identifier = self._required_text(profile_id, "profile_id")
        info = self._profiles.get_role_binding_profile(identifier)
        if info is None:
            raise ValueError("Profile is not installed")
        document = self._profiles.load_role_binding_profile_document(identifier)
        if (
            document.profile_id != identifier
            or document.semantic_hash != info.semantic_hash
        ):
            raise ValueError("Profile document differs from its metadata")
        return self._decode_canonical(
            document.canonical_json,
            "Profile",
        )

    def finalize(self, job_id: str) -> AssetAssignmentSetInfo:
        existing = self._store.get_asset_assignment_set_by_job(job_id)
        if existing is not None:
            return existing
        job = self._store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise AssetAssignmentError("Assignment producer Job has not succeeded")
        if job.result_artifact_id is None:
            raise AssetAssignmentError("Assignment producer Job has no result")
        command = self._read_json(job.command.artifact_id, "Assignment command")
        self._validate_command(command)
        result = self._read_json(job.result_artifact_id, "Assignment result")
        if result.get("result_kind") != "asset_assignment":
            raise AssetAssignmentError("Expected asset_assignment result")
        assignment_artifact_id = self._output_artifact(result, "assignments")
        artifact = self._store.get_artifact(assignment_artifact_id)
        if (
            artifact.schema_id != "verdflow.asset-assignment-set"
            or artifact.schema_version != "1"
        ):
            raise AssetAssignmentError("Assignment output schema is unsupported")
        assignment = self._read_json(
            assignment_artifact_id,
            "AssignmentSet",
        )
        return self._snapshots.finalize_asset_assignments(
            point_set_id=command["point_set_id"],
            effective_binding_snapshot_id=(
                command["effective_binding_snapshot_id"]
            ),
            producer_job_id=job.job_id,
            value=assignment,
        )

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        with self._store.open_artifact(artifact_id) as stream:
            try:
                value = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AssetAssignmentError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(value, dict):
            raise AssetAssignmentError(f"{label} must be a JSON object")
        return value

    @staticmethod
    def _decode_canonical(payload: bytes, label: str) -> dict[str, Any]:
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{label} is not valid canonical JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be a JSON object")
        return value

    @staticmethod
    def _validate_command(value: dict[str, Any]) -> None:
        if (
            value.get("schema") != ASSET_ASSIGNMENT_COMMAND_SCHEMA
            or str(value.get("version")) != ASSET_ASSIGNMENT_COMMAND_VERSION
            or value.get("operation") != "assign_assets"
        ):
            raise AssetAssignmentError("Assignment command schema is unsupported")

    @staticmethod
    def _output_artifact(result: dict[str, Any], role: str) -> str:
        outputs = result.get("output_artifacts")
        if not isinstance(outputs, list):
            raise AssetAssignmentError("Assignment output_artifacts is required")
        matches = [
            item
            for item in outputs
            if isinstance(item, dict) and item.get("role") == role
        ]
        if len(matches) != 1:
            raise AssetAssignmentError(
                f"Assignment output role must occur once: {role}"
            )
        return AssetAssignmentService._required_text(
            matches[0].get("artifact_id"),
            "artifact_id",
        )

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _seed(value: object) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > 2**63 - 1
        ):
            raise ValueError("random_seed must be between 0 and 2^63-1")
        return value
