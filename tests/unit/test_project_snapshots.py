from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    JobCommandRef,
    JobRetentionClass,
    PointRoleSummary,
    RegionSummary,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRetentionPolicy,
    JobRetentionSweeper,
    JobRuntime,
)
from verdflow.core.project_snapshots import (  # noqa: E402
    ProjectSnapshotFinalizationError,
    ProjectSnapshotService,
)
from verdflow.planting import (  # noqa: E402
    build_effective_binding_from_profile,
    canonical_json_bytes,
    resolve_asset_assignments,
    semantic_hash,
)
from verdflow.project_store import (  # noqa: E402
    JobNotFoundError,
    ProjectStore,
    SnapshotNotFoundError,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class ManualClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class ProjectSnapshotServiceTests(unittest.TestCase):
    def test_persists_reproducible_binding_and_assignment_graph(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            project_path = root / "snapshots.verdflow"
            with ProjectStore.create(
                project_path,
                "Snapshots",
            ) as store:
                point_set = self._point_set(store)
                service = ProjectSnapshotService(store)
                package_artifact = self._package_artifact(store)
                package = service.finalize_strategy_package(
                    point_set.planting_run_id,
                    package_artifact,
                )
                second_point_set = self._point_set(store)
                second_package = service.finalize_strategy_package(
                    second_point_set.planting_run_id,
                    package_artifact,
                )
                effective_document = build_effective_binding_from_profile(
                    self._strategy(),
                    self._profile(),
                )
                binding = service.finalize_effective_binding(
                    point_set.planting_run_id,
                    effective_document,
                )
                second_binding = service.finalize_effective_binding(
                    second_point_set.planting_run_id,
                    effective_document,
                )
                assignment_document = resolve_asset_assignments(
                    self._points_document(),
                    effective_document,
                    random_seed=42,
                )
                assignment_job = self._succeeded_job(
                    store,
                    "verdflow.assets.resolve",
                )

                first = service.finalize_asset_assignments(
                    point_set_id=point_set.point_set_id,
                    effective_binding_snapshot_id=(
                        binding.effective_binding.effective_binding_snapshot_id
                    ),
                    producer_job_id=assignment_job,
                    value=assignment_document,
                )
                repeated = service.finalize_asset_assignments(
                    point_set_id=point_set.point_set_id,
                    effective_binding_snapshot_id=(
                        binding.effective_binding.effective_binding_snapshot_id
                    ),
                    producer_job_id=assignment_job,
                    value=assignment_document,
                )
                blender_effective = build_effective_binding_from_profile(
                    self._strategy(),
                    self._profile("blender"),
                )
                blender_binding = service.finalize_effective_binding(
                    point_set.planting_run_id,
                    blender_effective,
                )
                blender_assignments = resolve_asset_assignments(
                    self._points_document(),
                    blender_effective,
                    random_seed=42,
                )
                blender_assignment = service.finalize_asset_assignments(
                    point_set_id=point_set.point_set_id,
                    effective_binding_snapshot_id=(
                        blender_binding.effective_binding
                        .effective_binding_snapshot_id
                    ),
                    producer_job_id=self._succeeded_job(
                        store,
                        "verdflow.assets.resolve.blender",
                    ),
                    value=blender_assignments,
                )

                self.assertEqual(first, repeated)
                self.assertEqual(package, second_package)
                self.assertEqual(binding, second_binding)
                self.assertEqual(package.strategy_id, "street.demo")
                self.assertEqual(binding.asset_use.selected_asset_count, 2)
                self.assertEqual(first.point_count, 2)
                self.assertEqual(blender_assignment.target_type, "blender")
                self.assertNotEqual(
                    first.effective_binding_snapshot_id,
                    blender_assignment.effective_binding_snapshot_id,
                )
                self.assertEqual(
                    sum(
                        item.point_count
                        for item in store.list_asset_assignment_summaries(
                            first.asset_assignment_set_id
                        )
                    ),
                    2,
                )
                self.assertEqual(store.collect_garbage().removed_object_count, 0)
                assignment_id = first.asset_assignment_set_id
                assignment_artifact_id = first.assignment_artifact_id

            copied_path = root / "snapshots-copy.verdflow"
            shutil.copytree(project_path, copied_path)
            with ProjectStore.open(copied_path) as copied:
                restored = copied.get_asset_assignment_set(assignment_id)
                self.assertEqual(restored.assignment_artifact_id, assignment_artifact_id)
                with copied.open_artifact(assignment_artifact_id) as stream:
                    self.assertTrue(stream.read())

    def test_expired_candidate_removes_assignment_and_orphan_snapshots(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "candidate-snapshots.verdflow"
            with ProjectStore.create(project_path, "Candidate snapshots") as store:
                point_set = self._point_set(store)
                service = ProjectSnapshotService(store)
                package = service.finalize_strategy_package(
                    point_set.planting_run_id,
                    self._package_artifact(store),
                )
                effective_document = build_effective_binding_from_profile(
                    self._strategy(),
                    self._profile(),
                )
                binding = service.finalize_effective_binding(
                    point_set.planting_run_id,
                    effective_document,
                )
                assignments = resolve_asset_assignments(
                    self._points_document(),
                    effective_document,
                    random_seed=42,
                )
                clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    retention_policy=JobRetentionPolicy(
                        candidate=timedelta(seconds=5),
                    ),
                )
                command = store.put_bytes(
                    b"candidate-assignment",
                    media_type="application/json",
                )
                job = runtime.submit(
                    JobCommandRef(
                        "verdflow.assets.resolve.candidate",
                        "1",
                        command.artifact_id,
                    ),
                    idempotency_key="candidate-assignment",
                    retention_class=JobRetentionClass.CANDIDATE,
                )
                claimed = runtime.claim_next("candidate-worker")
                assert claimed is not None
                runtime.succeed(claimed.lease)
                assignment = service.finalize_asset_assignments(
                    point_set_id=point_set.point_set_id,
                    effective_binding_snapshot_id=(
                        binding.effective_binding.effective_binding_snapshot_id
                    ),
                    producer_job_id=job.job_id,
                    value=assignments,
                )

                clock.advance(timedelta(seconds=6))
                result = JobRetentionSweeper(
                    store,
                    clock=clock,
                    orphan_grace=timedelta(seconds=1),
                ).sweep_once()

                self.assertEqual(result.removed_job_count, 1)
                self.assertEqual(result.removed_assignment_count, 1)
                self.assertEqual(result.removed_snapshot_count, 2)
                with self.assertRaises(JobNotFoundError):
                    store.get_job(job.job_id)
                with self.assertRaises(SnapshotNotFoundError):
                    store.get_asset_assignment_set(
                        assignment.asset_assignment_set_id
                    )
                with self.assertRaises(SnapshotNotFoundError):
                    store.get_effective_binding_snapshot(
                        binding.effective_binding.effective_binding_snapshot_id
                    )
                self.assertEqual(
                    store.get_strategy_package_snapshot(
                        package.strategy_package_snapshot_id
                    ),
                    package,
                )

    def test_expired_job_backing_formal_fact_is_promoted_to_audit(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            project_path = Path(directory) / "protected-candidate.verdflow"
            with ProjectStore.create(project_path, "Protected candidate") as store:
                clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    retention_policy=JobRetentionPolicy(
                        candidate=timedelta(seconds=5),
                    ),
                )
                command = store.put_bytes(
                    b"candidate-point-set",
                    media_type="application/json",
                )
                job = runtime.submit(
                    JobCommandRef(
                        "verdflow.points.generate.candidate",
                        "1",
                        command.artifact_id,
                    ),
                    idempotency_key="candidate-point-set",
                    retention_class=JobRetentionClass.CANDIDATE,
                )
                claimed = runtime.claim_next("candidate-worker")
                assert claimed is not None
                runtime.succeed(claimed.lease)
                self._point_set(store, point_job_id=job.job_id)

                clock.advance(timedelta(seconds=6))
                sweeper = JobRetentionSweeper(
                    store,
                    clock=clock,
                    orphan_grace=timedelta(seconds=1),
                )
                result = sweeper.sweep_once()

                self.assertEqual(result.removed_job_count, 0)
                self.assertEqual(result.protected_job_count, 1)
                protected = runtime.get(job.job_id)
                self.assertEqual(
                    protected.retention_class,
                    JobRetentionClass.AUDIT,
                )
                self.assertIsNone(protected.retention_seconds)
                self.assertIsNone(protected.purge_after)
                self.assertEqual(sweeper.sweep_once().examined_job_count, 0)

    def test_rejects_assignment_for_another_point_set(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "mismatch.verdflow",
                "Mismatch",
            ) as store:
                point_set = self._point_set(store)
                service = ProjectSnapshotService(store)
                service.finalize_strategy_package(
                    point_set.planting_run_id,
                    self._package_artifact(store),
                )
                effective = build_effective_binding_from_profile(
                    self._strategy(),
                    self._profile(),
                )
                binding = service.finalize_effective_binding(
                    point_set.planting_run_id,
                    effective,
                )
                assignments = resolve_asset_assignments(
                    self._points_document(),
                    effective,
                    random_seed=1,
                )
                assignments["point_set_hash"] = "sha256:" + "0" * 64
                assignment_payload = dict(assignments)
                assignment_payload.pop("semantic_hash")
                assignments["semantic_hash"] = semantic_hash(
                    assignment_payload
                )
                job_id = self._succeeded_job(store, "verdflow.assets.resolve")

                with self.assertRaisesRegex(
                    ProjectSnapshotFinalizationError,
                    "point_set_hash",
                ):
                    service.finalize_asset_assignments(
                        point_set_id=point_set.point_set_id,
                        effective_binding_snapshot_id=(
                            binding.effective_binding.effective_binding_snapshot_id
                        ),
                        producer_job_id=job_id,
                        value=assignments,
                    )

    @classmethod
    def _point_set(
        cls,
        store: ProjectStore,
        *,
        point_job_id: str | None = None,
    ):
        source_artifact = store.put_bytes(
            b"fbx",
            media_type="model/vnd.autodesk.fbx",
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        frame = store.create_coordinate_frame(
            name="World",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        inspection = store.put_bytes(
            canonical_json_bytes({"objects": []}),
            media_type="application/json",
        )
        material_facts = store.put_bytes(
            canonical_json_bytes({"materials": []}),
            media_type="application/json",
        )
        manifest = store.put_bytes(
            canonical_json_bytes(
                {
                    "facts": {
                        "scene_inspection_manifest_artifact_id": (
                            inspection.artifact_id
                        ),
                        "material_fact_report_artifact_id": (
                            material_facts.artifact_id
                        ),
                    }
                }
            ),
            media_type="application/json",
        )
        scene = store.create_scene_revision(
            source_id=source.source_id,
            importer_id="test",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest.artifact_id,
        )
        region_job = cls._succeeded_job(store, "test.regions")
        geometry = store.put_bytes(b"{}", media_type="application/json")
        metrics = store.put_bytes(b"{}", media_type="application/json")
        regions = store.create_region_set(
            scene_revision_id=scene.scene_revision_id,
            producer_job_id=region_job,
            extractor_id="test",
            extractor_version="1",
            input_hash="sha256:regions",
            coordinate_frame_id=frame.coordinate_frame_id,
            geometry_artifact_id=geometry.artifact_id,
            metrics_artifact_id=metrics.artifact_id,
            regions=(
                RegionSummary(
                    region_id="region-1",
                    ordinal=0,
                    label="Region",
                    tags="",
                    classification="ordinary",
                    strategy_hint=None,
                    footprint_area_xy=100.0,
                    boundary_perimeter_xy=40.0,
                    min_x=0.0,
                    min_y=0.0,
                    min_z=0.0,
                    max_x=10.0,
                    max_y=10.0,
                    max_z=1.0,
                    metrics_json="{}",
                ),
            ),
        )
        point_job = point_job_id or cls._succeeded_job(store, "test.points")
        point_artifact = store.put_bytes(
            canonical_json_bytes(cls._points_document()),
            media_type="application/vnd.verdflow.points+json",
            schema_id="verdflow.points",
            schema_version="2",
        )
        _, point_set = store.create_generated_point_set(
            scene_revision_id=scene.scene_revision_id,
            region_set_id=regions.region_set_id,
            producer_job_id=point_job,
            strategy_id="street.demo",
            strategy_version="1.0.0",
            strategy_package_hash="sha256:" + "1" * 64,
            generator_id="street-generator",
            generator_version="1",
            random_seed=10,
            input_hash="sha256:points",
            coordinate_frame_id=frame.coordinate_frame_id,
            point_artifact_id=point_artifact.artifact_id,
            point_count=2,
            bounds=(0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            roles=(
                PointRoleSummary("avenue_tree", 1),
                PointRoleSummary("edge_shrub", 1),
            ),
        )
        return point_set

    @staticmethod
    def _succeeded_job(store: ProjectStore, command_type: str) -> str:
        command = store.put_bytes(b"{}", media_type="application/json")
        runtime = JobRuntime(store)
        job = runtime.submit(
            JobCommandRef(command_type, "1", command.artifact_id),
            idempotency_key=f"{command_type}-{store.artifact_count()}",
        )
        claimed = runtime.claim_next("test-worker", command_types=(command_type,))
        assert claimed is not None
        result = store.put_bytes(b"{}", media_type="application/json")
        runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)
        return job.job_id

    @staticmethod
    def _package_artifact(store: ProjectStore) -> str:
        artifact = store.put_bytes(
            b"adapter-validated-package-bundle",
            media_type="application/vnd.verdflow.strategy-package+zip",
            schema_id="verdflow.strategy-package",
            schema_version="1",
        )
        return artifact.artifact_id

    @staticmethod
    def _strategy() -> dict:
        return {
            "schema": "verdflow.strategy",
            "version": "2",
            "strategy_id": "street.demo",
            "strategy_version": "1.0.0",
            "display_name": "Street demo",
            "generator": {"id": "street-generator", "version": "1"},
            "roles": [
                {
                    "role_key": "edge_shrub",
                    "display_name": "Shrub",
                    "description": "Low shrub",
                    "required": True,
                    "assignment_policy": "single",
                },
                {
                    "role_key": "avenue_tree",
                    "display_name": "Tree",
                    "description": "Street tree",
                    "required": True,
                    "assignment_policy": "single",
                },
            ],
        }

    @staticmethod
    def _profile(target_type: str = "dts") -> dict:
        prefix = "SM" if target_type == "dts" else "BL"
        return {
            "schema": "verdflow.role-binding-profile",
            "version": "2",
            "profile_id": f"street.{target_type}",
            "display_name": f"Street {target_type}",
            "strategy": {
                "strategy_id": "street.demo",
                "strategy_version": "1.0.0",
            },
            "target": {
                "target_type": target_type,
                "provider_id": f"{target_type}.provider",
            },
            "catalog_hint": {"catalog_id": f"{target_type}.demo"},
            "assignments": {
                "edge_shrub": {
                    "candidates": [{
                        "asset_id": f"{prefix}_Shrub",
                        "display_name_snapshot": "Shrub",
                        "target_asset_ref": {
                            "target_type": target_type,
                            "provider_id": f"{target_type}.provider",
                            "locator": {"source": "shrub"},
                        },
                        "weight": 1,
                        "scale": {"mode": "fixed", "value": 1},
                    }]
                },
                "avenue_tree": {
                    "candidates": [{
                        "asset_id": f"{prefix}_Oak",
                        "display_name_snapshot": "Oak",
                        "target_asset_ref": {
                            "target_type": target_type,
                            "provider_id": f"{target_type}.provider",
                            "locator": {"source": "oak"},
                        },
                        "weight": 1,
                        "scale": {"mode": "fixed", "value": 1},
                    }]
                },
            },
        }

    @staticmethod
    def _points_document() -> dict:
        points = []
        for index, role in enumerate(("edge_shrub", "avenue_tree")):
            position = [float(index), 0.0, 0.0]
            points.append(
                {
                    "id": f"p{index}",
                    "region_id": "region-1",
                    "role_key": role,
                    "generated_position": position,
                    "terrain_sample": None,
                    "resolved_position": position,
                    "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
                }
            )
        return {
            "schema": "verdflow.points",
            "version": "2",
            "strategy": {
                "strategy_id": "street.demo",
                "strategy_version": "1.0.0",
            },
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "kind": "generated",
            "resolution_policy": "generated_position",
            "point_count": 2,
            "points": points,
        }


if __name__ == "__main__":
    unittest.main()
