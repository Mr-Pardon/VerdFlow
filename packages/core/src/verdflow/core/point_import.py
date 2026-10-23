"""Finalize canonical planting and elevation outputs into PointSets."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from verdflow.contracts import (
    JobStatus,
    PointRoleSummary,
    PointSetInfo,
    PointSetKind,
)
from verdflow.project_store import ProjectStore
from verdflow.planting import (
    PlantingDocumentError,
    canonical_json_bytes,
    filter_point_roles,
    validate_elevation_point_set,
    validate_point_set_v2,
)


class PointSetImportError(RuntimeError):
    """Raised when point outputs cannot become an immutable PointSet."""


class PointSetImportService:
    """Finalize generated and elevation-resolved canonical point artifacts."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def finalize_generated(self, job_id: str) -> PointSetInfo:
        existing = self.store.get_point_set_by_job(job_id)
        if existing is not None:
            if existing.kind is not PointSetKind.GENERATED:
                raise PointSetImportError(
                    "Producer Job is already bound to another PointSet kind"
                )
            return existing
        job, command, result = self._completed_job(
            job_id,
            result_kind="planting_generation",
        )
        scene_revision_id = self._required_text(
            command,
            "scene_revision_id",
        )
        region_set_id = self._required_text(command, "region_set_id")
        scene = self.store.get_scene_revision(scene_revision_id)
        region_set = self.store.get_region_set(region_set_id)
        if region_set.scene_revision_id != scene.scene_revision_id:
            raise PointSetImportError(
                "Planting RegionSet differs from SceneRevision"
            )
        strategy = command.get("strategy")
        if not isinstance(strategy, dict):
            raise PointSetImportError("strategy provenance is required")
        random_seed = command.get("random_seed")
        if not isinstance(random_seed, int) or isinstance(random_seed, bool):
            raise PointSetImportError("random_seed must be an integer")

        point_artifact_id = self._output_artifact(result, "points")
        payload = self._read_json(point_artifact_id, "Generated points")
        points = self._validate_document(
            payload,
            expected_kind=PointSetKind.GENERATED,
        )
        self._validate_strategy_ref(payload, strategy)
        excluded_roles = tuple(command.get("excluded_role_keys", ()))
        if excluded_roles:
            payload = filter_point_roles(payload, excluded_roles)
            points = payload["points"]
            point_artifact_id = self.store.put_bytes(
                canonical_json_bytes(payload),
                media_type="application/vnd.verdflow.points+json",
                schema_id="verdflow.points", schema_version="2",
            ).artifact_id
        allowed_regions = {
            region.region_id
            for region in self.store.list_regions(region_set_id)
        }
        unknown_regions = sorted(
            {
                self._required_text(point, "region_id")
                for point in points
            }
            - allowed_regions
        )
        if unknown_regions:
            raise PointSetImportError(
                "Points reference Regions outside the RegionSet: "
                + ", ".join(unknown_regions[:3])
            )
        producer = self._producer(result)
        command_artifact = self.store.get_artifact(
            job.command.artifact_id
        )
        package_artifact_id = self._output_artifact(
            result,
            "strategy_package",
        )
        package_artifact = self.store.get_artifact(package_artifact_id)
        if (
            package_artifact.schema_id != "verdflow.strategy-package"
            or package_artifact.schema_version != "1"
        ):
            raise PointSetImportError(
                "Strategy package output schema is unsupported"
            )
        run, point_set = self.store.create_generated_point_set(
            scene_revision_id=scene.scene_revision_id,
            region_set_id=region_set.region_set_id,
            producer_job_id=job.job_id,
            strategy_id=self._required_text(strategy, "id"),
            strategy_version=self._required_text(strategy, "version"),
            strategy_package_hash=self._required_text(
                strategy,
                "package_hash",
            ),
            generator_id=self._required_text(producer, "id"),
            generator_version=self._required_text(producer, "version"),
            random_seed=random_seed,
            input_hash=command_artifact.content_hash,
            coordinate_frame_id=scene.coordinate_frame_id,
            point_artifact_id=point_artifact_id,
            point_count=len(points),
            bounds=self._resolved_bounds(points),
            roles=self._role_summaries(points),
        )
        self.store.create_strategy_package_snapshot(
            planting_run_id=run.planting_run_id,
            package_artifact_id=package_artifact_id,
        )
        return point_set

    def finalize_elevation(self, job_id: str) -> PointSetInfo:
        existing = self.store.get_point_set_by_job(job_id)
        if existing is not None:
            if existing.kind is not PointSetKind.ELEVATION_RESOLVED:
                raise PointSetImportError(
                    "Producer Job is already bound to another PointSet kind"
                )
            return existing
        job, command, result = self._completed_job(
            job_id,
            result_kind="elevation_sampling",
        )
        parent_id = self._required_text(
            command,
            "parent_point_set_id",
        )
        parent = self.store.get_point_set(parent_id)
        run = self.store.get_planting_run(parent.planting_run_id)
        scene = self.store.get_scene_revision(run.scene_revision_id)
        command_scene = self._required_text(
            command,
            "scene_revision_id",
        )
        if command_scene != scene.scene_revision_id:
            raise PointSetImportError(
                "Elevation SceneRevision differs from PlantingRun"
            )

        point_artifact_id = self._output_artifact(result, "points")
        payload = self._read_json(
            point_artifact_id,
            "Elevation-resolved points",
        )
        parent_payload = self._read_json(
            parent.point_artifact_id,
            "Parent generated points",
        )
        try:
            points = validate_elevation_point_set(
                payload,
                parent_payload,
                expected_point_count=parent.point_count,
            )["points"]
        except PlantingDocumentError as error:
            raise PointSetImportError(
                f"Elevation PointSet validation failed: {error}"
            ) from error
        hit_count = sum(
            1
            for point in points
            if self._terrain_sample(point)["hit"] is True
        )
        producer = self._producer(result)
        command_artifact = self.store.get_artifact(
            job.command.artifact_id
        )
        return self.store.create_elevation_point_set(
            parent_point_set_id=parent.point_set_id,
            producer_job_id=job.job_id,
            producer_id=self._required_text(producer, "id"),
            producer_version=self._required_text(producer, "version"),
            input_hash=command_artifact.content_hash,
            coordinate_frame_id=scene.coordinate_frame_id,
            point_artifact_id=point_artifact_id,
            resolution_policy="sample_hit_else_generated",
            point_count=len(points),
            hit_count=hit_count,
            miss_count=len(points) - hit_count,
            bounds=self._resolved_bounds(points),
            roles=self._role_summaries(points),
        )

    def _completed_job(
        self,
        job_id: str,
        *,
        result_kind: str,
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        job = self.store.get_job(job_id)
        if job.status is not JobStatus.SUCCEEDED:
            raise PointSetImportError("PointSet producer Job has not succeeded")
        if job.result_artifact_id is None:
            raise PointSetImportError("PointSet producer Job has no result")
        command = self._read_json(job.command.artifact_id, "Point command")
        result = self._read_json(job.result_artifact_id, "Worker result")
        if result.get("result_kind") != result_kind:
            raise PointSetImportError(
                f"Expected {result_kind} result"
            )
        return job, command, result

    def _validate_document(
        self,
        payload: dict[str, Any],
        *,
        expected_kind: PointSetKind,
    ) -> list[dict[str, Any]]:
        if payload.get("offset_applied") is not False:
            raise PointSetImportError(
                "Point document must use unshifted coordinates"
            )
        try:
            document = validate_point_set_v2(payload)
        except PlantingDocumentError as error:
            raise PointSetImportError(
                f"PointSet v2 validation failed: {error}"
            ) from error
        if document["kind"] != expected_kind.value:
            raise PointSetImportError("Point document kind is inconsistent")
        expected_policy = (
            "generated_position"
            if expected_kind is PointSetKind.GENERATED
            else "sample_hit_else_generated"
        )
        if document["resolution_policy"] != expected_policy:
            raise PointSetImportError(
                "Point document resolution policy is unsupported"
            )
        return document["points"]

    @staticmethod
    def _validate_strategy_ref(
        payload: dict[str, Any],
        command_strategy: dict[str, Any],
    ) -> None:
        strategy = payload["strategy"]
        expected = (
            PointSetImportService._required_text(command_strategy, "id"),
            PointSetImportService._required_text(command_strategy, "version"),
        )
        actual = (strategy["strategy_id"], strategy["strategy_version"])
        if actual != expected:
            raise PointSetImportError(
                "Point document strategy differs from Job command"
            )

    @staticmethod
    def _role_summaries(
        points: list[dict[str, Any]],
    ) -> tuple[PointRoleSummary, ...]:
        counts = Counter(str(point["role_key"]) for point in points)
        return tuple(
            PointRoleSummary(role_key=key, point_count=counts[key])
            for key in sorted(counts)
        )

    def _resolved_bounds(
        self,
        points: list[dict[str, Any]],
    ) -> tuple[float, float, float, float, float, float] | None:
        if not points:
            return None
        positions = [
            self._vector(point, "resolved_position", 3)
            for point in points
        ]
        return (
            min(position[0] for position in positions),
            min(position[1] for position in positions),
            min(position[2] for position in positions),
            max(position[0] for position in positions),
            max(position[1] for position in positions),
            max(position[2] for position in positions),
        )

    @staticmethod
    def _terrain_sample(point: dict[str, Any]) -> dict[str, Any]:
        sample = point.get("terrain_sample")
        if not isinstance(sample, dict):
            raise PointSetImportError(
                "Elevation PointSet requires terrain_sample"
            )
        if not isinstance(sample.get("hit"), bool):
            raise PointSetImportError("terrain_sample.hit must be boolean")
        return sample

    @staticmethod
    def _vector(
        payload: dict[str, Any],
        field: str,
        length: int,
    ) -> tuple[float, ...]:
        value = payload.get(field)
        if (
            not isinstance(value, list)
            or len(value) != length
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(float(item))
                for item in value
            )
        ):
            raise PointSetImportError(f"{field} is invalid")
        return tuple(float(item) for item in value)

    def _read_json(
        self,
        artifact_id: str,
        label: str,
    ) -> dict[str, Any]:
        with self.store.open_artifact(artifact_id) as stream:
            try:
                payload = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise PointSetImportError(
                    f"{label} is not valid UTF-8 JSON"
                ) from error
        if not isinstance(payload, dict):
            raise PointSetImportError(f"{label} must be a JSON object")
        return payload

    @staticmethod
    def _producer(result: dict[str, Any]) -> dict[str, Any]:
        producer = result.get("producer")
        if not isinstance(producer, dict):
            raise PointSetImportError(
                "Worker result lacks producer provenance"
            )
        return producer

    @staticmethod
    def _output_artifact(
        result: dict[str, Any],
        role: str,
    ) -> str:
        outputs = result.get("output_artifacts")
        if not isinstance(outputs, list):
            raise PointSetImportError("Worker output_artifacts is required")
        matches = [
            output
            for output in outputs
            if isinstance(output, dict) and output.get("role") == role
        ]
        if len(matches) != 1:
            raise PointSetImportError(
                f"Worker output role must occur once: {role}"
            )
        return PointSetImportService._required_text(
            matches[0],
            "artifact_id",
        )

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PointSetImportError(f"{field} is required")
        return value.strip()
