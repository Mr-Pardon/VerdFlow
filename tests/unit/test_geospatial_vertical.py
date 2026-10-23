from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path / "src")
    for group in ("packages", "adapters", "apps")
    for path in (ROOT / group).iterdir()
    if (path / "src").is_dir()
]

try:
    import numpy
    import rasterio
    from rasterio.transform import from_origin
except ImportError:
    numpy = None
    rasterio = None
    from_origin = None

from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobStatus,
    PointRoleSummary,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    ElevationSamplingRequest,
    RegionSourceRequest,
)
from verdflow.core_host import (  # noqa: E402
    CoreHost,
    CoreHostConfig,
    GeospatialExtensionFactory,
)
from verdflow.system_host import (  # noqa: E402
    ProjectBusinessState,
    SystemHost,
    SystemHostConfig,
)


@unittest.skipIf(
    numpy is None or rasterio is None or not os.environ.get("VERDFLOW_GEOSPATIAL_TEST_PYTHON"),
    "geospatial process test environment is not configured",
)
class GeospatialVerticalTests(unittest.TestCase):
    def test_external_worker_finalizes_scene_and_regions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geojson = root / "regions.geojson"
            geojson.write_text(
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [[0.25, 0.25], [2.75, 0.25], [2.75, 2.75], [0.25, 2.75], [0.25, 0.25]]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            tif = root / "elevation.tif"
            with rasterio.open(
                tif,
                "w",
                driver="GTiff",
                width=3,
                height=3,
                count=1,
                dtype="float32",
                transform=from_origin(0, 3, 1, 1),
                nodata=-9999.0,
            ) as dataset:
                dataset.write(numpy.full((3, 3), 12.5, dtype="float32"), 1)
            project = root / "project.verdflow"
            factory = GeospatialExtensionFactory(
                runtime_root=root / "runtime",
                cache_root=root / "cache",
                python_executable=Path(os.environ["VERDFLOW_GEOSPATIAL_TEST_PYTHON"]),
            )
            with CoreHost.create(
                CoreHostConfig(
                    project_path=project,
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    poll_interval_seconds=0.01,
                ),
                project_name="Geospatial vertical",
                extension_factories=(factory,),
            ) as host:
                vector = host.add_source_file(geojson, kind=SourceKind.GEOJSON)
                elevation = host.add_source_file(tif, kind=SourceKind.GEOTIFF)
                assembly = host.create_identity_assembly(
                    original_name="regions + elevation",
                    source_ids=(vector.source_id, elevation.source_id),
                )

                outcome = host.prepare_region_source(
                    RegionSourceRequest(
                        source_id=assembly.source_id,
                        provider_id="verdflow.geospatial",
                    ),
                    timeout_seconds=20.0,
                )

                self.assertEqual(outcome.region_set.region_count, 1)
                self.assertEqual(
                    outcome.region_set.scene_revision_id,
                    outcome.scene_revision.scene_revision_id,
                )
                self.assertEqual(
                    host.store.list_regions(outcome.region_set.region_set_id)[0].footprint_area_xy,
                    6.25,
                )
                generated = _generated_point_set(host, outcome)
                sampled = host.sample_elevation(
                    ElevationSamplingRequest(
                        parent_point_set_id=generated.point_set_id,
                        provider_id="verdflow.geospatial",
                    ),
                    timeout_seconds=20.0,
                )
                with host.store.open_artifact(sampled.point_set.point_artifact_id) as stream:
                    resolved = json.load(stream)
                self.assertEqual(
                    resolved["points"][0]["resolved_position"],
                    [0.5, 2.5, 12.5],
                )

    def test_system_host_exposes_direct_regions_as_ready_to_plant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geojson, tif = _write_inputs(root)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                project = system.create_project(
                    root / "project.verdflow",
                    project_name="Geospatial system route",
                    enable_blender_worker=False,
                    enable_geospatial_worker=True,
                )
                vector = system.add_source_file(
                    project.session_id,
                    geojson,
                    kind=SourceKind.GEOJSON,
                )
                elevation = system.add_source_file(
                    project.session_id,
                    tif,
                    kind=SourceKind.GEOTIFF,
                )
                assembly = system.create_identity_source_assembly(
                    project.session_id,
                    original_name="regions + elevation",
                    source_ids=(vector.source_id, elevation.source_id),
                )
                job = system.submit_region_source(
                    project.session_id,
                    RegionSourceRequest(
                        source_id=assembly.source_id,
                        provider_id="verdflow.geospatial",
                    ),
                    idempotency_key="system-geospatial",
                )
                deadline = time.monotonic() + 20.0
                while job.status not in {
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }:
                    if time.monotonic() >= deadline:
                        self.fail("Geospatial SystemHost Job timed out")
                    time.sleep(0.02)
                    job = system.get_job(project.session_id, job.job_id)
                self.assertEqual(job.status, JobStatus.SUCCEEDED)
                scene, regions = system.finalize_region_source(
                    project.session_id,
                    job.job_id,
                )

                summary = system.get_project_business_summary(
                    project.session_id
                )

                self.assertEqual(summary.state, ProjectBusinessState.READY_TO_PLANT)
                self.assertEqual(
                    summary.scene_revision_id,
                    scene.scene_revision_id,
                )
                self.assertEqual(summary.region_set_id, regions.region_set_id)
                self.assertIsNone(summary.scene_preparation_execution_id)


def _write_inputs(root: Path) -> tuple[Path, Path]:
    geojson = root / "regions.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [0.25, 0.25],
                        [2.75, 0.25],
                        [2.75, 2.75],
                        [0.25, 2.75],
                        [0.25, 0.25],
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )
    tif = root / "elevation.tif"
    with rasterio.open(
        tif,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="float32",
        transform=from_origin(0, 3, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(numpy.full((3, 3), 12.5, dtype="float32"), 1)
    return geojson, tif


def _generated_point_set(host: CoreHost, outcome):
    points = {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {"strategy_id": "fixture", "strategy_version": "1"},
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": 1,
        "points": [
            {
                "id": "point_1",
                "region_id": "region_0001_001",
                "role_key": "plant",
                "generated_position": [0.5, 2.5, 0.0],
                "terrain_sample": None,
                "resolved_position": [0.5, 2.5, 0.0],
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            }
        ],
    }
    point_artifact = host.store.put_bytes(
        json.dumps(points).encode("utf-8"),
        media_type="application/vnd.verdflow.points+json",
        schema_id="verdflow.points",
        schema_version="2",
    )
    command = host.store.put_bytes(b"{}", media_type="application/json")
    job = host.runtime.submit(
        JobCommandRef(
            command_type="fixture.generate",
            command_version="1",
            artifact_id=command.artifact_id,
        ),
        idempotency_key="fixture-generate",
    )
    claimed = host.runtime.claim_next(
        "fixture-generator",
        command_types=("fixture.generate",),
    )
    assert claimed is not None
    result = host.store.put_bytes(b"{}", media_type="application/json")
    host.runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)
    _, point_set = host.store.create_generated_point_set(
        scene_revision_id=outcome.scene_revision.scene_revision_id,
        region_set_id=outcome.region_set.region_set_id,
        producer_job_id=job.job_id,
        strategy_id="fixture",
        strategy_version="1",
        strategy_package_hash="sha256:fixture",
        generator_id="fixture",
        generator_version="1",
        random_seed=1,
        input_hash="sha256:fixture-input",
        coordinate_frame_id=outcome.scene_revision.coordinate_frame_id,
        point_artifact_id=point_artifact.artifact_id,
        point_count=1,
        bounds=(0.5, 2.5, 0.0, 0.5, 2.5, 0.0),
        roles=(PointRoleSummary(role_key="plant", point_count=1),),
    )
    return point_set


if __name__ == "__main__":
    unittest.main()
