from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "geospatial-worker" / "src"),
]

try:
    import numpy
    import rasterio
    from rasterio.transform import from_origin
    import shapefile
    import shapely
except ImportError:
    numpy = None
    rasterio = None
    from_origin = None
    shapefile = None
    shapely = None

from verdflow.geospatial_worker import package_shapefile, GeospatialDataError  # noqa: E402
from verdflow.geospatial_worker.processing import (  # noqa: E402
    prepare_regions,
    sample_elevation,
)


IDENTITY = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


@unittest.skipIf(
    any(value is None for value in (numpy, rasterio, shapefile, shapely)),
    "geospatial test dependencies are not installed",
)
class GeospatialProcessingTests(unittest.TestCase):
    def test_geojson_with_different_crs_metadata_is_not_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector = root / "regions.geojson"
            vector.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "crs": {
                            "type": "name",
                            "properties": {"name": "EPSG:4326"},
                        },
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {
                                    "name": "Courtyard",
                                    "classification": "garden",
                                },
                                "geometry": {
                                    "type": "Polygon",
                                    "coordinates": [
                                        [[0.2, 0.2], [3.8, 0.2], [3.8, 3.8], [0.2, 3.8], [0.2, 0.2]],
                                        [[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]],
                                    ],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            raster = _write_raster(root / "elevation.tif", crs="EPSG:3857")

            documents = prepare_regions(
                _command(vector, "geojson", raster),
                root / "work",
            )

            self.assertEqual(documents["regions"]["region_count"], 1)
            self.assertEqual(
                len(documents["regions"]["regions"][0]["footprint_boundary_xyz"]),
                2,
            )
            self.assertEqual(documents["geometry_metrics"]["regions"][0]["hole_count"], 1)
            self.assertEqual(
                documents["source_manifest"]["crs_validation"],
                "not_performed",
            )

    def test_shapefile_package_uses_the_same_region_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shp = root / "areas.shp"
            writer = shapefile.Writer(str(shp))
            writer.field("name", "C")
            writer.poly([[[0.5, 0.5], [0.5, 2.5], [2.5, 2.5], [2.5, 0.5], [0.5, 0.5]]])
            writer.record("SHP area")
            writer.close()
            archive = root / "areas.zip"
            archive.write_bytes(package_shapefile(shp))
            raster = _write_raster(root / "elevation.tif")

            documents = prepare_regions(
                _command(archive, "shapefile", raster),
                root / "work",
            )

            self.assertEqual(documents["regions"]["region_count"], 1)
            self.assertEqual(documents["regions"]["regions"][0]["label"], "SHP area")
            self.assertEqual(documents["source_manifest"]["vector"]["format"], "shapefile")

    def test_samples_known_height_and_rejects_outside_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raster = _write_raster(root / "elevation.tif")
            vector = root / "regions.geojson"
            vector.write_text(
                '{"type":"Polygon","coordinates":[[[0,0],[4,0],[4,4],[0,4],[0,0]]]}',
                encoding="utf-8",
            )
            command = _command(vector, "geojson", raster)
            points = root / "points.json"
            points.write_text(
                json.dumps(_point_set([0.5, 3.5, 0.0])),
                encoding="utf-8",
            )
            command["operation"] = "sample_elevation"
            command["points_path"] = str(points)
            command["options"] = {"band": 1}

            resolved = sample_elevation(command)

            self.assertEqual(resolved["kind"], "elevation_resolved")
            self.assertEqual(resolved["points"][0]["resolved_position"], [0.5, 3.5, 10.0])
            outside = root / "outside.json"
            outside.write_text(json.dumps(_point_set([9.0, 9.0, 0.0])), encoding="utf-8")
            command["points_path"] = str(outside)
            with self.assertRaises(GeospatialDataError) as caught:
                sample_elevation(command)
            self.assertEqual(caught.exception.code, "geo_point_outside_raster")
            self.assertIn("第 1 个", str(caught.exception))
            self.assertIn("覆盖范围", str(caught.exception))

    def test_worker_result_retains_semantic_geometry_error_without_exposing_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector = root / "invalid.geojson"
            vector.write_text('{"type":"Polygon","coordinates":[[[0,0],[4,4],[0,4],[4,0],[0,0]]]}', encoding="utf-8")
            command = root / "command.json"
            command.write_text(json.dumps(_command(vector, "geojson", root / "unused.tif")), encoding="utf-8")
            result = root / "result.json"
            process = subprocess.run([sys.executable, "-I", str(ROOT / "adapters/geospatial-worker/src/verdflow/geospatial_worker/worker_entry.py"),
                                      "--command", str(command), "--output-dir", str(root / "out"), "--result", str(result)],
                                     capture_output=True, timeout=25)
            document = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(process.returncode, 1)
            self.assertEqual(document["error_code"], "geo_invalid_geometry")
            self.assertIn("第 1 条", document["message"])
            self.assertIn("修复", document["message"])
            self.assertNotIn(str(root), document["message"])

    def test_raster_bounds_and_nodata_have_distinct_actionable_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raster = _write_raster(root / "elevation.tif")
            vector = root / "regions.geojson"
            vector.write_text('{"type":"Polygon","coordinates":[[[10,10],[12,10],[12,12],[10,12],[10,10]]]}', encoding="utf-8")
            command = _command(vector, "geojson", raster)
            with self.assertRaises(GeospatialDataError) as caught:
                prepare_regions(command, root / "work")
            self.assertEqual(caught.exception.code, "geo_bounds_mismatch")
            self.assertIn("坐标", str(caught.exception))
            with rasterio.open(raster, "r+") as dataset:
                dataset.write(numpy.full((4,4), -9999, dtype="float32"), 1)
            points = root / "points.json"
            points.write_text(json.dumps(_point_set([0.5,3.5,0])), encoding="utf-8")
            command["points_path"] = str(points)
            with self.assertRaises(GeospatialDataError) as caught:
                sample_elevation(command)
            self.assertEqual(caught.exception.code, "geo_raster_nodata")
            self.assertIn("NoData", str(caught.exception))


def _write_raster(path: Path, *, crs: str | None = None) -> Path:
    values = numpy.arange(16, dtype="float32").reshape((4, 4)) + 10.0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        transform=from_origin(0, 4, 1, 1),
        crs=crs,
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    return path


def _command(vector: Path, vector_kind: str, raster: Path) -> dict[str, object]:
    return {
        "operation": "prepare_regions",
        "source_id": "source_assembly",
        "sources": [
            {
                "source_member_id": "member_vector",
                "kind": vector_kind,
                "source_path": str(vector),
                "source_to_scene_matrix_row_major": IDENTITY,
            },
            {
                "source_member_id": "member_raster",
                "kind": "geotiff",
                "source_path": str(raster),
                "source_to_scene_matrix_row_major": IDENTITY,
            },
        ],
    }


def _point_set(position: list[float]) -> dict[str, object]:
    return {
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
                "region_id": "region_1",
                "role_key": "plant",
                "generated_position": position,
                "terrain_sample": None,
                "resolved_position": position,
                "rotation_quaternion_xyzw": [0, 0, 0, 1],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
