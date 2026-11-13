"""Finalize canonical planting and elevation outputs into PointSets."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
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
    PointSetReader,
    canonical_point_document,
    encoded_point_rows,
    validate_elevation_headers,
    validate_elevation_row,
    validate_point_header,
    validated_point_rows,
)


class PointSetImportError(RuntimeError):
    """Raised when point outputs cannot become an immutable PointSet."""


class _PointSummary:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.count = 0
        self.hit_count = 0
        self._min = [math.inf] * 3
        self._max = [-math.inf] * 3

    def add(self, point: dict[str, Any]) -> None:
        self.count += 1
        self.counts[point["role_key"]] += 1
        sample = point["terrain_sample"]
        if sample is not None and sample["hit"] is True:
            self.hit_count += 1
        for axis, coordinate in enumerate(point["resolved_position"]):
            value = float(coordinate)
            if value < self._min[axis]:
                self._min[axis] = value
            if value > self._max[axis]:
                self._max[axis] = value

    @property
    def roles(self) -> tuple[PointRoleSummary, ...]:
        return tuple(PointRoleSummary(role_key=key, point_count=count) for key, count in sorted(self.counts.items()))

    @property
    def bounds(self) -> tuple[float, float, float, float, float, float] | None:
        if not self.count:
            return None
        lo, hi = self._min, self._max
        return lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]


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
        reference = command.get("input")
        path_input = isinstance(reference, dict) and reference.get("kind") == "polyline"
        path_set_id = self._required_text(reference, "set_id") if path_input else None
        region_set_id = (
            None if path_input else self._required_text(command, "region_set_id")
        )
        scene = self.store.get_scene_revision(scene_revision_id)
        region_set = (
            self.store.get_path_set(path_set_id)
            if path_input
            else self.store.get_region_set(region_set_id)
        )
        if region_set.scene_revision_id != scene.scene_revision_id:
            raise PointSetImportError("Planting input set differs from SceneRevision")
        strategy = command.get("strategy")
        if not isinstance(strategy, dict):
            raise PointSetImportError("strategy provenance is required")
        random_seed = command.get("random_seed")
        if not isinstance(random_seed, int) or isinstance(random_seed, bool):
            raise PointSetImportError("random_seed must be an integer")

        point_artifact_id = self._output_artifact(result, "points")
        if path_input:
            with self.store.open_artifact(region_set.geometry_artifact_id) as stream:
                rows = json.load(stream)["paths"]
            allowed_regions = {row["id"] for row in rows}
        else:
            allowed_regions = {
                region.region_id for region in self.store.list_regions(region_set_id)
            }
        selected = set(
            reference["selected_ids"] if path_input else command["selected_region_ids"]
        )
        allowed_regions &= selected
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
        point_artifact_id, summary = self._verified_generated_points(
            point_artifact_id,
            strategy=strategy,
            version="3" if path_input else "2",
            input_id_key="path_id" if path_input else "region_id",
            allowed_regions=allowed_regions,
            excluded_roles=frozenset(command.get("excluded_role_keys", ())),
        )
        run, point_set = self.store.create_generated_point_set(
            scene_revision_id=scene.scene_revision_id,
            region_set_id=region_set_id,
            path_set_id=path_set_id,
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
            point_count=summary.count,
            bounds=summary.bounds,
            roles=summary.roles,
        )
        self.store.create_strategy_package_snapshot(
            planting_run_id=run.planting_run_id,
            package_artifact_id=package_artifact_id,
        )
        return point_set

    def _verified_generated_points(
        self, artifact_id: str, *, strategy: dict[str, Any], version: str,
        input_id_key: str, allowed_regions: set[str], excluded_roles: frozenset[str],
    ) -> tuple[str, _PointSummary]:
        summary = _PointSummary()
        scratch_root = self.store.root / "runtime"
        scratch_root.mkdir(exist_ok=True)
        try:
            with (
                TemporaryDirectory(prefix="point-verify-", dir=scratch_root) as temporary,
                self.store.open_artifact(artifact_id) as source,
            ):
                scratch = Path(temporary)
                reader = PointSetReader(source)
                verified = validated_point_rows(
                    reader.rows(), version=version, kind=PointSetKind.GENERATED.value,
                    scratch=scratch / "point-ids.sqlite",
                )

                def retained() -> Iterator[dict[str, Any]]:
                    for point in verified:
                        if point["role_key"] in excluded_roles:
                            continue
                        input_id = self._required_text(point, input_id_key)
                        if input_id not in allowed_regions:
                            raise PointSetImportError(
                                f"Points reference Regions outside the RegionSet: {input_id}"
                            )
                        summary.add(point)
                        yield point

                spool = scratch / "points.json-fragment"
                with closing(verified):
                    if excluded_roles:
                        with spool.open("wb") as output:
                            output.writelines(encoded_point_rows(retained()))
                    else:
                        for _ in retained():
                            pass
                header = reader.header
                self._validate_generated_header(header, version=version)
                self._validate_strategy_ref(header, strategy)
                if excluded_roles:
                    if not summary.count:
                        raise PlantingDocumentError("Enabled roles produced no planting points")
                    header["point_count"] = summary.count

                    def write(stream) -> None:
                        with spool.open("rb") as body:
                            stream.writelines(canonical_point_document(header, iter(lambda: body.read(1024**2), b"")))

                    artifact_id = self.store.write_artifact(
                        write, media_type="application/vnd.verdflow.points+json",
                        schema_id="verdflow.points", schema_version=version,
                    ).artifact_id
        except PlantingDocumentError as error:
            raise PointSetImportError(f"PointSet validation failed: {error}") from error
        return artifact_id, summary

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
        summary = _PointSummary()
        try:
            with (
                self.store.open_artifact(parent.point_artifact_id) as source,
                self.store.open_artifact(point_artifact_id) as output,
            ):
                originals = PointSetReader(source, expected_point_count=parent.point_count)
                sampled = PointSetReader(output, expected_point_count=parent.point_count)
                for index, (original, point) in enumerate(zip(originals.rows(), sampled.rows(), strict=True)):
                    validate_elevation_row(original, point, index=index)
                    summary.add(point)
                validate_elevation_headers(originals.header, sampled.header)
        except PlantingDocumentError as error:
            raise PointSetImportError(
                f"Elevation PointSet validation failed: {error}"
            ) from error
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
            point_count=summary.count,
            hit_count=summary.hit_count,
            miss_count=summary.count - summary.hit_count,
            bounds=summary.bounds,
            roles=summary.roles,
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

    @staticmethod
    def _validate_generated_header(header: dict[str, Any], *, version: str) -> None:
        if header.get("offset_applied") is not False:
            raise PointSetImportError(
                "Point document must use unshifted coordinates"
            )
        if header.get("version") != version:
            raise PointSetImportError("PointSet version differs from planting input kind")
        validate_point_header(header)
        if header["kind"] != PointSetKind.GENERATED.value:
            raise PointSetImportError("Point document kind is inconsistent")
        if header["resolution_policy"] != "generated_position":
            raise PointSetImportError(
                "Point document resolution policy is unsupported"
            )

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
