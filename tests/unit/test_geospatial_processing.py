from __future__ import annotations

import errno
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch


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
    write_prepared_regions,
    write_sampled_elevation,
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
    def test_streamed_geojson_preserves_order_multipart_holes_and_metrics(self) -> None:
        polygon = [
            [[0.2, 0.2], [3.8, 0.2], [3.8, 3.8], [0.2, 3.8], [0.2, 0.2]],
            [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]],
        ]
        separate = [[[4.2, 0], [5, 0], [5, 1], [4.2, 1], [4.2, 0]]]
        collection = {
            "features": [
                {"type": "Feature", "properties": {"name": "Courtyard", "tags": ["a", "b"]},
                 "geometry": {"type": "MultiPolygon", "coordinates": [polygon, separate]}},
                {"type": "Feature", "properties": None,
                 "geometry": {"type": "Polygon", "coordinates": separate}},
            ],
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "local"}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector = root / "regions.geojson"
            vector.write_text(json.dumps(collection), encoding="utf-8")
            command = _command(vector, "geojson", _write_raster(root / "elevation.tif"))
            expected = prepare_regions(command, root)
            geometry, metrics = StringIO(), StringIO()
            with patch("verdflow.geospatial_worker.vector_json._SMALL_GEOJSON_BYTES", 0):
                manifest = write_prepared_regions(command, root, geometry, metrics, lambda *_: None)
            self.assertEqual(manifest, expected["source_manifest"])
            self.assertEqual(json.loads(geometry.getvalue()), expected["regions"])
            self.assertEqual(json.loads(metrics.getvalue()), expected["geometry_metrics"])
            self.assertEqual(
                [row["id"] for row in expected["regions"]["regions"]],
                ["region_0001_001", "region_0001_002", "region_0002_001"],
            )
            self.assertEqual(expected["geometry_metrics"]["regions"][0]["hole_count"], 1)

    def test_streamed_geojson_invalid_suffix_publishes_neither_output(self) -> None:
        from verdflow.geospatial_worker.worker_entry import main

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector = root / "regions.geojson"
            vector.write_text(
                '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":'
                '{"type":"Polygon","coordinates":[[[0,0],[2,0],[2,2],[0,2],[0,0]]]}}]}{}',
                encoding="utf-8",
            )
            command = root / "command.json"
            command.write_text(json.dumps(_command(vector, "geojson", _write_raster(root / "height.tif"))), encoding="utf-8")
            output, result = root / "outputs", root / "result.json"
            with (
                patch("sys.argv", ["worker_entry", "--command", str(command), "--output-dir", str(output), "--result", str(result)]),
                patch("verdflow.geospatial_worker.vector_json._SMALL_GEOJSON_BYTES", 0),
                patch("verdflow.geospatial_worker.worker_entry.traceback.print_exc"),
            ):
                self.assertEqual(main(), 1)
            self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["error_code"], "geo_invalid_json")
            self.assertEqual(list(output.iterdir()), [])

    def test_vector_disk_full_during_streaming_leaves_no_output(self) -> None:
        from verdflow.geospatial_worker.worker_entry import main

        def fail(_command, _workdir, geometry, metrics, _report):
            geometry.write("partial geometry")
            metrics.write("partial metrics")
            raise OSError(errno.ENOSPC, "injected disk full")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / "command.json"
            command.write_text(json.dumps({"operation": "prepare_regions"}), encoding="utf-8")
            output, result = root / "outputs", root / "result.json"
            with (
                patch("sys.argv", ["worker_entry", "--command", str(command), "--output-dir", str(output), "--result", str(result)]),
                patch("verdflow.geospatial_worker.worker_entry.write_prepared_regions", side_effect=fail),
                patch("verdflow.geospatial_worker.worker_entry.traceback.print_exc"),
            ):
                self.assertEqual(main(), 1)
            self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["error_code"], "geo_disk_budget")
            self.assertEqual(list(output.iterdir()), [])

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

    def test_chunked_sampling_matches_rasterio_order_with_rotated_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transform = from_origin(100, 200, 2, 3) * rasterio.Affine.rotation(17)
            values = numpy.arange(20, dtype="float32").reshape(4, 5)
            raster = root / "elevation.tif"
            with rasterio.open(
                raster, "w", driver="GTiff", width=5, height=4, count=1,
                dtype="float32", transform=transform,
            ) as dataset:
                dataset.write(values, 1)
            xy = [transform * pixel for pixel in ((4.5, 3.5), (0.5, 0.5), (2.5, 1.5), (4.5, 3.5))]
            doc = _point_set([*xy[0], 0.0])
            doc["points"] = [
                {**doc["points"][0], "id": f"point-{i}", "generated_position": [x, y, 0.0]}
                for i, (x, y) in enumerate(xy)
            ]
            doc["point_count"] = len(xy)
            path = root / "points.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            command = _command(root / "unused.geojson", "geojson", raster)
            command["points_path"] = str(path)
            command["point_count"] = len(xy)
            progress = []
            streamed = BytesIO()
            with (
                patch("verdflow.geospatial_worker.processing.ELEVATION_POINT_CHUNK", 2),
                patch("verdflow.geospatial_worker.processing.ELEVATION_WINDOW_SIZE", 2),
            ):
                result = sample_elevation(command, lambda _, done, total: progress.append((done, total)))
                write_sampled_elevation(command, streamed, lambda *_: None)
            with rasterio.open(raster) as dataset:
                expected = [float(z[0]) for z in dataset.sample(xy, indexes=1, masked=True)]
            self.assertEqual([point["resolved_position"][2] for point in result["points"]], expected)
            self.assertEqual([point["id"] for point in result["points"]], [p["id"] for p in doc["points"]])
            self.assertEqual(progress, [(2, 4), (4, 4)])
            self.assertEqual(
                streamed.getvalue(),
                json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            )

    def test_streamed_worker_does_not_publish_when_later_input_fails(self) -> None:
        from verdflow.geospatial_worker.worker_entry import main

        for error in ("outside", "trailing_json"):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                points = root / "points.json"
                first = _point_set([0.5, 3.5, 0.0])
                second = {**first["points"][0], "id": "later"}
                if error == "outside":
                    second["generated_position"] = [9.0, 9.0, 0.0]
                first["points"].append(second)
                first["point_count"] = 2
                points.write_text(json.dumps(first) + (" {}" if error == "trailing_json" else ""), encoding="utf-8")
                command = _command(root / "unused.geojson", "geojson", _write_raster(root / "height.tif"))
                command.update(operation="sample_elevation", points_path=str(points), point_count=2)
                command_file = root / "command.json"
                command_file.write_text(json.dumps(command), encoding="utf-8")
                output = root / "out"
                result = root / "result.json"
                with (
                    patch("sys.argv", ["worker_entry", "--command", str(command_file), "--output-dir", str(output), "--result", str(result)]),
                    patch("verdflow.geospatial_worker.processing.ELEVATION_POINT_CHUNK", 1),
                    patch("verdflow.geospatial_worker.worker_entry.traceback.print_exc"),
                ):
                    self.assertEqual(main(), 1)
                failure = json.loads(result.read_text(encoding="utf-8"))
                self.assertEqual(failure["status"], "failed")
                self.assertEqual(list(output.iterdir()), [])
                if error == "outside":
                    self.assertEqual(failure["error_code"], "geo_point_outside_raster")
                    self.assertIn("第 2 个", failure["message"])

    def test_elevation_uses_first_invalid_point_not_raster_window_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raster = _write_raster(root / "elevation.tif")
            with rasterio.open(raster, "r+") as dataset:
                heights = dataset.read(1)
                heights[3, 3] = float("nan")
                dataset.write(heights, 1)
                mask = numpy.full(heights.shape, 255, dtype="uint8")
                mask[0, 0] = 0
                dataset.write_mask(mask)
            points = root / "points.json"
            command = _command(root / "unused.geojson", "geojson", raster)
            command.update(points_path=str(points), point_count=3)
            valid, nonfinite, masked, outside = (
                [2.5, 2.5, 0], [3.5, 0.5, 0], [0.5, 3.5, 0], [9, 9, 0],
            )
            for first, second, code, message in (
                (nonfinite, masked, "geo_raster_nodata", "高程为空或无效"),
                (masked, outside, "geo_raster_nodata", "NoData 空洞"),
                (outside, masked, "geo_point_outside_raster", "超出 TIF 覆盖范围"),
            ):
                with self.subTest(first=first, second=second):
                    document = _point_set(valid)
                    document["points"] = [
                        {**document["points"][0], "id": f"p{i}", "generated_position": xyz}
                        for i, xyz in enumerate((valid, first, second))
                    ]
                    document["point_count"] = 3
                    points.write_text(json.dumps(document), encoding="utf-8")
                    with (
                        patch("verdflow.geospatial_worker.processing.ELEVATION_WINDOW_SIZE", 2),
                        self.assertRaises(GeospatialDataError) as caught,
                    ):
                        write_sampled_elevation(command, BytesIO(), lambda *_: None)
                    self.assertEqual(caught.exception.code, code)
                    self.assertIn("第 2 个", str(caught.exception))
                    self.assertIn(message, str(caught.exception))

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
