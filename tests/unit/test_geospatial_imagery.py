from __future__ import annotations

from base64 import b64decode, b64encode
import errno
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.unit.test_geospatial_vertical import (
    CoreHost, CoreHostConfig, ElevationSamplingRequest, GeospatialExtensionFactory,
    JobStatus, RegionSourceRequest, SourceKind, _generated_point_set, from_origin,
    numpy as np, rasterio,
)
from verdflow.contracts import SamplingSourceRef
from verdflow.local_api import LocalApiConfig, LocalApiServer
from verdflow.project_store import ProjectStore
from verdflow.system_host import PlantExportRequest, PlantPreviewRequest, SystemHost, SystemHostConfig
from verdflow.system_host.imagery_input import included_from_imagery_bits, read_imagery_index_batch, resolve_plant_workflow
from verdflow.geospatial_worker import (
    GeospatialDataError, IMAGERY_REGION_PROVIDER_ID, IMAGERY_REGION_RECIPE_ID, ImageryRegionOptions,
    IMAGERY_REVIEW_INDEX_ENCODING, IMAGERY_REVIEW_INDEX_ROW, ImageryTileReader,
)
from verdflow.geospatial_worker.imagery import (
    CLASS_COUNT, MODEL_SHA256, PATCH, _mask_windows, _projected_regions, _region_rows, _validate_grid,
    imagery_review_pages, prepare_imagery_regions, write_imagery_review_overview,
)
from verdflow.geospatial_worker.imagery_output import write_imagery_candidate
from verdflow.geospatial_worker.imagery_regions import RegionTally, polygon_tiles
from verdflow.geospatial_worker import imagery_confirmation


def _write_rgb(path: Path, rgb, *, crs="EPSG:3857") -> None:
    with rasterio.open(path, "w", driver="GTiff", width=rgb.shape[2], height=rgb.shape[1], count=3,
                       dtype=rgb.dtype, crs=crs, transform=from_origin(0, rgb.shape[1], 1, 1),
                       nodata=-32768) as dataset:
        dataset.write(rgb)


def _predict_fixture(rgb):
    probabilities = np.zeros((CLASS_COUNT, PATCH, PATCH), dtype=np.float32)
    is_green = rgb[1] > 0
    probabilities[5] = is_green
    probabilities[1] = ~is_green
    return probabilities


class ImageryOptionsTests(unittest.TestCase):
    def test_refinement_watchdog_requires_strictly_advancing_work(self):
        checks = []
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            imagery_confirmation, "monotonic", side_effect=[0, 4, 8, 10, 12, 13],
        ):
            path = Path(temporary) / "progress.json"
            process = SimpleNamespace(poll=lambda: None)
            waiting = imagery_confirmation._wait_for_progress(process, path, lambda: checks.append(1), 5)
            for progress in (1, 2, 2, 1):
                path.write_text(json.dumps(["refining", progress, 100]), encoding="ascii")
                next(waiting)
            path.write_text(json.dumps(["refining", 2, 200]), encoding="ascii")
            with self.assertRaisesRegex(ValueError, "原有种植范围未更改"):
                next(waiting)
        self.assertEqual(len(checks), 5)

    def test_configuration_freezes_validated_bands_and_backend(self):
        options = ImageryRegionOptions("member", (3, 2, 1), "directml", 1, min_region_pixels=80)
        self.assertEqual(ImageryRegionOptions.from_document(options.to_document()), options)
        for changes in ({"rgb_bands": [1, 2, 2]}, {"rgb_bands": [True, 2, 3]},
                        {"profile": "latest"}, {"backend": "auto"}, {"model_path": "foreign.onnx"},
                        {"min_region_pixels": True}, {"min_region_pixels": -1}, {"min_region_pixels": 1.5}):
            with self.subTest(changes=changes), self.assertRaises(GeospatialDataError):
                ImageryRegionOptions.from_document({**options.to_document(), **changes})

    def test_packed_review_index_batches_and_exact_selection(self):
        count = 32_771
        packed = IMAGERY_REVIEW_INDEX_ROW.pack(2**32 + 1, 2) * count
        review = {"index_encoding": IMAGERY_REVIEW_INDEX_ENCODING, "region_count": count}
        with tempfile.TemporaryDirectory() as temporary, ProjectStore.create(
            Path(temporary) / "review.verdflow", "Packed index test",
        ) as store:
            artifact = store.put_bytes(packed, media_type="application/octet-stream",
                schema_id="verdflow.imagery-ordinal-index", schema_version="1")
            manifest = store.put_bytes(json.dumps({"review": review}).encode(), media_type="application/json")
            with patch("verdflow.system_host.imagery_input._imagery_outputs", return_value={
                "imagery_packed_index": artifact.artifact_id, "source_manifest": manifest.artifact_id,
            }):
                head = read_imagery_index_batch(store, "test-job", 0)
                tail = read_imagery_index_batch(store, "test-job", 32_768)
                self.assertEqual((head.start, head.count, tail.start, tail.count), (0, 32_768, 32_768, 3))
                self.assertEqual(b64decode(head.rows_base64) + b64decode(tail.rows_base64), packed)
                with self.assertRaises(ValueError):
                    read_imagery_index_batch(store, "test-job", count)
                bits = bytearray((count + 7) // 8)
                bits[0], bits[-1] = 1, 4
                self.assertEqual(included_from_imagery_bits(store, "test-job", b64encode(bits).decode()),
                                 ("region_00001", "region_32771"))
                bits[-1] |= 8
                for invalid in (b64encode(bits).decode(), "!" * len(b64encode(bits)), "AA=="):
                    with self.subTest(invalid=invalid[:16]), self.assertRaises(ValueError):
                        included_from_imagery_bits(store, "test-job", invalid)


@unittest.skipIf(np is None or rasterio is None, "GIS dependencies unavailable")
class ImageryProcessingTests(unittest.TestCase):
    def test_batched_large_component_preserves_pixel_topology_and_reports_progress(self):
        from rasterio.features import rasterize

        pixels = np.zeros((36, 40), dtype=np.uint8)
        pixels[2:-2, 2:-2] = 1
        pixels[4:29, 9:12] = 0
        pixels[16:18, 21:33] = 0
        pixels[0, 0] = pixels[1, 1] = 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "mask.tif"
            with rasterio.open(path, "w", driver="GTiff", count=1, dtype="uint8", height=36, width=40) as output:
                output.write(pixels, 1)
            module = "verdflow.geospatial_worker.imagery_regions"
            with rasterio.open(path) as dataset, patch(f"{module}.VECTOR_TILE", 8):
                original = list(polygon_tiles(dataset, root / "old.sqlite", 0, RegionTally(), lambda *_: None))
                reports = []
                with patch(f"{module}.INLINE_MERGE_BYTES", 128), patch(f"{module}.MERGE_BATCH_BYTES", 256):
                    batched = list(polygon_tiles(dataset, root / "new.sqlite", 0, RegionTally(),
                                                lambda phase, done, total: reports.append((phase, done, total))))
        self.assertEqual(len(batched), len(original))
        for (result, count), (reference, expected) in zip(batched, original, strict=True):
            self.assertEqual(count, expected)
            self.assertTrue(result.equals(reference))
            self.assertEqual(len(result.interiors), len(reference.interiors))
        np.testing.assert_array_equal(rasterize(((polygon, 1) for polygon, _ in batched), out_shape=pixels.shape), pixels)
        merge_steps = [done for phase, done, _ in reports if phase == "merging"]
        self.assertEqual(merge_steps, sorted(merge_steps))
        self.assertGreater(len(set(merge_steps)), 3)

    def test_streamed_exact_boundary_keeps_bytes_and_holes(self):
        from affine import Affine
        from shapely.geometry import Polygon, box

        shell = box(0, 0, 24, 24)
        polygon = Polygon(shell.exterior, [box(3, 5, 7, 8).exterior, box(12, 9, 19, 20).exterior])
        preview = {"width": 24, "height": 24, "transform": [1, 0, 0, 0, -1, 24]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ordinary, streamed = root / "ordinary", root / "streamed"
            ordinary.mkdir()
            streamed.mkdir()

            def write(path):
                return write_imagery_candidate(path, _projected_regions(iter([(polygon, int(polygon.area))]),
                    Affine.identity(), RegionTally()), "member", preview, lambda *_: None)

            write(ordinary)
            for chunk_size in (2, 10):
                with (patch("verdflow.geospatial_worker.imagery_output._STREAM_REGION_VERTICES", 1),
                      patch("verdflow.geospatial_worker.imagery_output._RING_WRITE_CHUNK", chunk_size)):
                    write(streamed)
                for name in ("regions.json", "region-metrics.json", "imagery-review-index.json", "imagery-overview.png"):
                    self.assertEqual((streamed / name).read_bytes(), (ordinary / name).read_bytes(), name)

    def test_review_overview_indexes_full_candidates_and_preserves_holes(self):
        def ring(x0, y0, x1, y1):
            return [[x, y, 0] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "overview.png"
            rows = [
                {"footprint_boundary_xyz": [ring(0, 0, 5, 5), ring(1, 1, 4, 4)]},
                {"footprint_boundary_xyz": [ring(6, 1, 8, 3)]},
            ]
            write_imagery_review_overview(path, {"width": 8, "height": 6, "transform": [1, 0, 0, 0, -1, 6]}, rows)
            with rasterio.open(path) as overview:
                r, g, b = overview.read().astype(np.uint32)
            ids = r | (g << 8) | (b << 16)
            self.assertEqual(int((ids == 1).sum()), 16)
            self.assertEqual(int((ids == 2).sum()), 4)
            self.assertEqual(int(ids[3, 2]), 0)

    def test_overlapping_stripes_preserve_mask_grid_and_holes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.tif"
            rgb = np.zeros((3, 905, 520), dtype=np.int16)
            rgb[1, 10:892, 10:510] = 255
            rgb[1, 300:600, 200:250] = 0
            rgb[:, 365:405, 315:345] = -32768
            _write_rgb(source, rgb)
            command = {"source_id": "assembly", "options": ImageryRegionOptions("member").to_document(),
                       "model_path": "unused", "sources": [{"source_path": str(source),
                           "source_content_hash": "fixture"}]}
            with (patch("verdflow.geospatial_worker.imagery._load_predictor", return_value=(_predict_fixture, "fixture")),
                  patch("verdflow.geospatial_worker.imagery.BLEND_WIDTH", 256)):
                documents = prepare_imagery_regions(command, root)
            with rasterio.open(root / "vegetation-mask.tif") as mask, rasterio.open(source) as original:
                self.assertEqual(mask.crs, original.crs)
                self.assertEqual(mask.transform, original.transform)
                self.assertEqual(mask.nodata, 255)
                np.testing.assert_array_equal(mask.read(1), np.where(rgb[1] == -32768, 255, rgb[1] == 255))
            self.assertEqual(documents["regions"]["region_count"], 1)
            self.assertEqual(documents["geometry_metrics"]["regions"][0]["hole_count"], 2)
            self.assertEqual(documents["source_manifest"]["recognition"]["onnx_sha256"], MODEL_SHA256)

    def test_input_and_output_limits_fail_without_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.tif"
            rgb = np.zeros((3, 17, 19), dtype=np.int16)
            rgb[1, 0, 0] = 1024
            _write_rgb(source, rgb)
            with rasterio.open(source) as dataset, self.assertRaises(GeospatialDataError) as invalid:
                list(_mask_windows(dataset, (1, 2, 3), _predict_fixture, lambda *_: None))
            self.assertEqual(invalid.exception.code, "imagery_rgb_range")
            with (rasterio.open(source) as dataset,
                  patch("verdflow.geospatial_worker.imagery.MAX_DECODE_BLOCK_BYTES", 100),
                  self.assertRaises(GeospatialDataError) as oversized):
                _validate_grid(dataset, (1, 2, 3))
            self.assertEqual(oversized.exception.code, "imagery_decode_block_limit")
            _write_rgb(source, rgb, crs="EPSG:4326")
            with rasterio.open(source) as dataset, self.assertRaises(GeospatialDataError) as unprojected:
                _validate_grid(dataset, (1, 2, 3))
            self.assertEqual(unprojected.exception.code, "imagery_grid_invalid")

    def test_large_grid_filters_complete_components_and_preserves_exact_mask(self):
        from rasterio.features import rasterize

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "large.tif"
            rgb = np.zeros((3, 1940, 8500), dtype=np.int16)
            # Each side of the tile seam is under the threshold; the complete region is not.
            rgb[1, 510:514, 511:513] = 255
            rgb[1, 510, 508] = 255
            rgb[1, 511, 509] = 255  # diagonal touching does not merge
            rgb[1, 700:703, 700:702] = 255  # exactly the threshold
            _write_rgb(source, rgb)
            command = {"source_id": "assembly", "options": ImageryRegionOptions("member", min_region_pixels=6).to_document(),
                       "model_path": "unused", "sources": [{"source_path": str(source), "source_content_hash": "fixture"}]}
            with patch("verdflow.geospatial_worker.imagery._load_predictor", return_value=(_predict_fixture, "fixture")):
                documents = prepare_imagery_regions(command, root)
            rows = documents["regions"]["regions"]
            self.assertEqual(sorted(row["pixel_count"] for row in rows), [6, 8])
            self.assertEqual(documents["source_manifest"]["recognition"]["filtered_regions"], 2)
            with rasterio.open(root / "vegetation-mask.tif") as result:
                np.testing.assert_array_equal(result.read(1), rgb[1] == 255)
                clean = rasterize(({"type": "Polygon", "coordinates": [
                    [(x, y) for x, y, _ in ring] for ring in row["footprint_boundary_xyz"]
                ]} for row in rows), out_shape=result.shape, transform=result.transform)
                self.assertEqual(int(clean.sum()), 14)
            self.assertFalse((root / "seams.sqlite").exists())

    def test_no_partial_candidate_when_disk_budget_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "image.tif"
            _write_rgb(source, np.zeros((3, 20, 20), dtype=np.int16))
            command = {"source_id": "assembly", "options": ImageryRegionOptions("member").to_document(),
                       "model_path": "unused", "sources": [{"source_path": str(source), "source_content_hash": "fixture"}]}
            with (patch("verdflow.geospatial_worker.imagery.shutil.disk_usage", return_value=SimpleNamespace(free=0)),
                  self.assertRaises(GeospatialDataError) as limit):
                prepare_imagery_regions(command, root)
            self.assertEqual(limit.exception.code, "imagery_disk_budget")
            self.assertFalse((root / "vegetation-mask.tif").exists())

    def test_preview_vertex_budget_does_not_limit_exact_region(self):
        from affine import Affine
        from shapely.geometry import Point

        polygon = Point(0, 0).buffer(100, quad_segs=64)
        with patch("verdflow.geospatial_worker.imagery.MAX_PREVIEW_VERTICES", 32):
            rows, metrics = _region_rows(iter([(polygon, 1)]), Affine.identity(), "image", RegionTally())
            [[preview]] = imagery_review_pages(rows)
        self.assertEqual(len(rows[0]["footprint_boundary_xyz"][0]), len(polygon.exterior.coords))
        self.assertEqual(metrics[0]["footprint_area_xy"], polygon.area)
        self.assertEqual(preview["id"], rows[0]["id"])
        self.assertEqual(preview["preview_mode"], "simplified")
        self.assertLessEqual(sum(map(len, preview["footprint_boundary_xyz"])), 32)

    def test_detail_worker_accepts_large_minimum_and_keeps_cross_tile_components(self):
        from rasterio.io import MemoryFile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, geometry, mask = (root / name for name in ("image.tif", "regions.json", "mask.tif"))
            width, height = 1030, 128
            transform = from_origin(100, 900, 1, 1)
            profile = dict(driver="GTiff", width=width, height=height, dtype="uint8", crs="EPSG:3857", transform=transform)
            with rasterio.open(image, "w", count=3, **profile) as output:
                output.write(np.full((3, height, width), 100, dtype="uint8"))
            candidate = np.zeros((height, width), dtype="uint8")
            candidate[20:30, 500:530] = 1  # 300 pixels, split 120/180 across tiles.
            candidate[50:60, 480:499] = 1  # 190 pixels, below the tested threshold.
            with rasterio.open(mask, "w", count=1, **profile) as output:
                output.write(candidate, 1)
            geometry.write_text('{"regions":[]}', encoding="utf-8")
            grid = {"width": width, "height": height, "transform": list(transform[:6]), "bands": [1, 2, 3],
                    "refinement_mask": str(mask)}
            cache = root / "cache"
            reader = ImageryTileReader()

            def read(column, minimum):
                for _ in range(60):
                    result = reader.read(("session", "job"), Path(sys.executable), image, geometry, grid, (0, column, 0),
                                         cache_root=cache, refinement=("greenness", 55, minimum))
                    self.assertIsNotNone(result)
                    if "retry_after_ms" not in result:
                        with MemoryFile(b64decode(result["refinement_png"])) as memory, memory.open() as tile:
                            return int(tile.read(1).sum())
                    self.assertTrue(result["refinement_pending"])
                    time.sleep(.05)
                self.fail("Background refinement did not finish")

            try:
                self.assertEqual((read(0, 190), read(1, 190)), (310, 180))
                self.assertEqual((read(0, 300), read(1, 300)), (120, 180))
                self.assertEqual(read(0, 301), 0)
                self.assertEqual(read(1, 10**25), 0)
            finally:
                reader.close()
            self.assertFalse(list(cache.iterdir()))

    def test_detail_worker_preserves_native_pixels_affine_and_holes_across_tiles(self):
        from affine import Affine
        from rasterio.io import MemoryFile
        from verdflow.geospatial_worker.imagery_tile_index import MemoryTileRegions
        from verdflow.geospatial_worker.imagery_tiles import ImageryTiles

        with tempfile.TemporaryDirectory() as temporary:
            source, geometry, mask = (Path(temporary) / name for name in ("image.blob", "regions.blob", "mask.blob"))
            transform = Affine(2, .3, 100, .1, -2, 800)
            rgb = np.broadcast_to(np.arange(770, dtype="uint8"), (3, 680, 770))
            with rasterio.open(source, "w", driver="GTiff", width=770, height=680, count=3,
                               dtype="uint8", crs="EPSG:3857", transform=transform) as output:
                output.write(rgb)
            shell = [(498, 30), (740, 30), (740, 90), (498, 90), (498, 30)]
            hole = [(504, 50), (520, 50), (520, 60), (504, 60), (504, 50)]
            geometry.write_text(json.dumps({"regions": [{"id": "region_a", "footprint_boundary_xyz":
                [[transform * point for point in ring] for ring in (shell, hole)]}]}), encoding="utf-8")
            selected = np.zeros((680, 770), dtype=np.uint8)
            selected[35:45, 506:520] = 1
            selected[100, 512] = 1
            with rasterio.open(mask, "w", driver="GTiff", width=770, height=680, count=1,
                               dtype="uint8", crs="EPSG:3857", transform=transform) as output:
                output.write(selected, 1)
            grid = {"width": 770, "height": 680, "transform": list(transform[:6]), "bands": [1, 2, 3],
                    "refinement_mask": str(mask)}
            reader = ImageryTileReader()
            try:
                def read(level, column, refinement=None):
                    return reader.read(("session", "job"), Path(sys.executable), source, geometry, grid, (level, column, 0),
                                       refinement=refinement)

                first, edge = read(0, 0), read(0, 1)
                self.assertIsNotNone(first)
                self.assertIsNotNone(edge)
                self.assertEqual(edge["width"], 258)
                self.assertEqual(edge["transform"], list((transform * Affine.translation(512, 0))[:6]))
                with MemoryFile(b64decode(edge["base_png"])) as memory, memory.open() as tile:
                    np.testing.assert_array_equal(tile.read(), rgb[:, :512, 512:])
                with MemoryFile(b64decode(first["ids_png"])) as memory, memory.open() as tile:
                    ids = tile.read(1)
                    self.assertEqual((ids[41, 512], ids[41, 513], ids[56, 512]), (1, 1, 0))
                with MemoryFile(b64decode(edge["ids_png"])) as memory, memory.open() as tile:
                    ids = tile.read(1)
                    self.assertEqual((ids[41, 0], ids[41, 1], ids[56, 1]), (1, 1, 0))
                self.assertEqual(edge["region_ids"], ["region_a"])
                for mode, strength, expected in (("greenness", 50, 140), ("greenness", 0, 0), ("patches", 50, 0)):
                    proposals = []
                    for column in (0, 1):
                        refined = read(0, column, (mode, strength, 96))
                        self.assertEqual(refined["ids_png"], (first, edge)[column]["ids_png"])
                        with MemoryFile(b64decode(refined["refinement_png"])) as memory, memory.open() as tile:
                            proposals.append(tile.read(1))
                            self.assertFalse(tile.read((2, 3)).any())
                    proposal = np.concatenate(proposals, axis=1)
                    self.assertEqual(int(proposal.sum()), expected)
                    self.assertFalse((proposal & ~selected[:512]).any())
                with (
                    rasterio.open(source) as dataset,
                    patch("verdflow.geospatial_worker.imagery_tiles.COMPLEX_DISPLAY_VERTICES", 4),
                ):
                    tiles = ImageryTiles(dataset, MemoryTileRegions(json.loads(geometry.read_bytes())["regions"]), grid)
                    self.assertEqual(tiles.read(0, 0, 0), first)
                    self.assertEqual(tiles.read(0, 1, 0), edge)
                self.assertEqual(read(1, 0)["width"], 385)
                self.assertIsNone(read(0, 2))
                reader.close_project("session")
                self.assertEqual(read(0, 1)["region_ids"], ["region_a"])
                disk_python = os.environ.get("VERDFLOW_GEOSPATIAL_TEST_PYTHON")
                if disk_python:
                    reader.close_project("session")
                    cache = source.parent / "cache"
                    with (
                        patch("verdflow.geospatial_worker.tile_reader.IMAGERY_TILE_INLINE_GEOMETRY_BYTES", 0),
                        patch("verdflow.geospatial_worker.tile_reader._WARMUP_PROBE_SECONDS", 0.0),
                    ):
                        def disk_read(column):
                            return reader.read(
                                ("session", "disk-job"), Path(disk_python), source, geometry, grid, (0, column, 0),
                                cache_root=cache, expected_count=1,
                            )

                        self.assertIn("retry_after_ms", disk_read(0))
                        process = reader._process
                        deadline = time.monotonic() + 12
                        while time.monotonic() < deadline:
                            disk_first = disk_read(0)
                            if disk_first is None or "retry_after_ms" not in disk_first:
                                break
                            time.sleep(0.05)
                        self.assertIs(reader._process, process)
                        self.assertEqual(disk_first, first)
                        self.assertEqual(disk_read(1), edge)
                        self.assertEqual(len(list(cache.rglob("geometry.sqlite"))), 1)
                        reader.close_project("session")
                        self.assertEqual(list(cache.iterdir()), [])
                        self.assertIn("retry_after_ms", disk_read(0))
                        pending_process = reader._process
                        reader.close_project("session")
                        self.assertIsNotNone(pending_process.poll())
                        self.assertEqual(list(cache.iterdir()), [])
            finally:
                reader.close()

    def test_detail_index_rejects_invalid_suffix_and_checks_count(self):
        from ijson.common import JSONError
        from verdflow.geospatial_worker.imagery_tile_index import DiskTileRegions

        row = {"id": "region_a", "footprint_boundary_xyz": [[[0, 0], [1, 0], [0, 1], [0, 0]]]}
        with tempfile.TemporaryDirectory() as temporary:
            geometry = Path(temporary) / "regions.json"
            payload = json.dumps({"regions": [row]})
            geometry.write_text(payload + "{}", encoding="utf-8")
            with self.assertRaises(JSONError):
                DiskTileRegions.build(Path(temporary) / "bad.sqlite", geometry, 1, lambda: None)
            geometry.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "count"):
                DiskTileRegions.build(Path(temporary) / "count.sqlite", geometry, 2, lambda: None)

    def test_detail_index_compact_boundary_matches_xy_and_keeps_holes(self):
        from contextlib import closing
        from shapely.geometry import Polygon, box
        from verdflow.geospatial_worker.imagery_tile_index import DiskTileRegions

        shell = [[x, y, 12.0] for x, y in box(0, 0, 12, 12).exterior.coords]
        hole = [[x, y, -3.5] for x, y in box(2, 3, 6, 8).exterior.coords]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geometry = root / "regions.json"
            geometry.write_text(json.dumps({
                "regions": [{"footprint_boundary_xyz": [shell, hole], "id": "region_a", "label": {"x": [1]}}],
                "metadata": [1, [2], {"x": "ignored"}],
            }), encoding="utf-8")
            with closing(DiskTileRegions.build(root / "detail.sqlite", geometry, 1, lambda: None)) as index:
                self.assertEqual(list(index.matches(box(3, 4, 5, 7))), [])
                [(ordinal, result)] = list(index.matches(box(0, 0, 1, 1)))
                self.assertEqual(ordinal, 1)
                self.assertTrue(result.equals(Polygon(shell, [hole])))
                self.assertEqual(index.ids([1]), ["region_a"])

    def test_polygonization_failure_closes_scratch_without_masking_domain_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.tif"
            rgb = np.zeros((3, 20, 20), dtype=np.int16)
            rgb[1, 4:16, 4:16] = 255
            _write_rgb(source, rgb)
            command = {"source_id": "assembly", "options": ImageryRegionOptions("member").to_document(),
                       "model_path": "unused", "sources": [{"source_path": str(source), "source_content_hash": "fixture"}]}
            with (patch("verdflow.geospatial_worker.imagery._load_predictor", return_value=(_predict_fixture, "fixture")),
                  patch("shapely.affinity.affine_transform", side_effect=GeospatialDataError("fixture", "invalid boundary")),
                  self.assertRaises(GeospatialDataError) as failure):
                prepare_imagery_regions(command, root)
            self.assertEqual(failure.exception.code, "fixture")
            self.assertFalse((root / "seams.sqlite").exists())

    def test_polygonization_retry_reuses_only_the_matching_complete_mask(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.tif"
            checkpoint = root / "checkpoint.tif"
            rgb = np.zeros((3, 20, 20), dtype=np.int16)
            rgb[1, 4:16, 4:16] = 255
            _write_rgb(source, rgb)
            command = {
                "source_id": "assembly", "options": ImageryRegionOptions("member").to_document(),
                "model_path": "unused", "mask_checkpoint": str(checkpoint),
                "sources": [{"source_path": str(source), "source_content_hash": "fixture"}],
            }
            first, second, third = (root / name for name in ("first", "second", "third"))
            for attempt in (first, second, third):
                attempt.mkdir()
            with (
                patch.dict(sys.modules, {"onnxruntime": SimpleNamespace(__version__="fixture")}),
                patch("verdflow.geospatial_worker.imagery._load_predictor",
                      return_value=(_predict_fixture, "fixture")) as predictor,
            ):
                with (
                    patch("shapely.affinity.affine_transform", side_effect=GeospatialDataError("fixture", "invalid boundary")),
                    self.assertRaises(GeospatialDataError),
                ):
                    prepare_imagery_regions(command, first)
                self.assertTrue(checkpoint.is_file())
                completed = prepare_imagery_regions(command, second)
                self.assertEqual(completed["regions"]["region_count"], 1)
                self.assertEqual(predictor.call_count, 1)
                command["sources"][0]["source_content_hash"] = "different"
                prepare_imagery_regions(command, third)
                self.assertEqual(predictor.call_count, 2)

    def test_candidate_spooling_preserves_exact_bytes_and_cleans_failed_attempt(self):
        from affine import Affine
        from shapely.geometry import Polygon, box

        transform = Affine(1, 0, 0, 0, -1, 12)
        polygons = [
            (box(7, 7, 10, 10), 9),
            (Polygon(box(1, 1, 6, 6).exterior, [box(2, 2, 3, 3).exterior]), 24),
            (box(1, 8, 2, 9), 1),
        ]
        preview = {"width": 12, "height": 12, "transform": list(transform[:6])}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            memory, disk, failed = (root / part for part in ("memory", "disk", "failed"))
            for part in (memory, disk, failed):
                part.mkdir()

            def write(path, report=lambda *_: None):
                return write_imagery_candidate(
                    path, _projected_regions(iter(polygons), transform, RegionTally()), "member", preview, report,
                )

            expected = write(memory)
            with (
                patch("verdflow.geospatial_worker.imagery_output._INLINE_VERTICES", 1),
                patch("verdflow.geospatial_worker.imagery_output._OVERVIEW_BATCH_ROWS", 1),
            ):
                self.assertEqual(write(disk), expected)

                def fail(_, completed, total):
                    if completed:
                        raise OSError(errno.ENOSPC, "fixture")

                with self.assertRaises(OSError):
                    write(failed, fail)
                self.assertEqual(list(failed.iterdir()), [])

            self.assertEqual(expected["region_count"], 3)
            for file in memory.iterdir():
                self.assertEqual(file.read_bytes(), (disk / file.name).read_bytes(), file.name)
            with patch("verdflow.geospatial_worker.imagery_output.IMAGERY_REVIEW_INDEX_INLINE_ROWS", 2):
                packed_review = write(failed)
            self.assertEqual(packed_review, {**expected, "index_encoding": IMAGERY_REVIEW_INDEX_ENCODING})
            rows = list(IMAGERY_REVIEW_INDEX_ROW.iter_unpack((failed / "imagery-review-index.bin").read_bytes()))
            items = json.loads((failed / "imagery-review-index.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual(rows, [(item["pixel_count"], item["page"]) for item in items])
            for file in memory.iterdir():
                self.assertEqual(file.read_bytes(), (failed / file.name).read_bytes(), file.name)
            exact, metrics = _region_rows(iter(polygons), transform, "member", RegionTally())
            for row in exact:
                row.pop("pixel_count")
            self.assertEqual(json.loads((disk / "regions.json").read_text(encoding="utf-8"))["regions"], exact)
            self.assertEqual(json.loads((disk / "region-metrics.json").read_text(encoding="utf-8"))["regions"], metrics)

    def test_worker_rejects_unverified_weights_without_creating_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tif, model = root / "image.tif", root / "unverified.onnx"
            _write_rgb(tif, np.zeros((3, 10, 10), dtype=np.int16))
            model.write_bytes(b"not a model")
            factory = GeospatialExtensionFactory(root / "runtime", root / "cache",
                                                python_executable=Path(sys.executable), imagery_model_path=model)
            config = CoreHostConfig(root / "project.verdflow", enable_blender_worker=False,
                                    enable_project_maintenance=False)
            with CoreHost.create(config, project_name="Invalid weights", extension_factories=(factory,)) as host:
                source = host.add_source_file(tif, kind=SourceKind.GEOTIFF)
                assembly = host.create_identity_assembly(original_name="Image", source_ids=(source.source_id,))
                member, = host.store.list_source_assembly_members(assembly.source_id)
                job = host.submit_region_source(RegionSourceRequest(
                    assembly.source_id, IMAGERY_REGION_PROVIDER_ID, ImageryRegionOptions(member.source_member_id).to_document(),
                ))
                completed = host.service_loop.run_until_terminal(job.job_id, timeout_seconds=20)
                self.assertEqual(completed.status, JobStatus.FAILED)
                self.assertEqual(completed.error.code, "imagery_model_mismatch")
                self.assertIsNone(host.store.project_workflow_state().region_set_id)
                self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_refined_confirmation_publishes_exact_holes_and_feeds_core_export(self):
        from shapely.geometry import Point, Polygon
        from tests.unit.test_dts_live_vertical_runtime import _prepare_library

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path, terrain_path = root / "image.tif", root / "terrain.tif"
            rgb = np.empty((3, 40, 1030), dtype=np.int16)
            rgb[:] = np.array([50, 150, 50])[:, None, None]
            rgb[:, 15:25, 500:530] = 100
            rgb[:, :, 1000:1010] = 100
            _write_rgb(image_path, rgb)
            with rasterio.open(image_path) as image, rasterio.open(
                terrain_path, "w", driver="GTiff", height=image.height, width=image.width, count=1,
                dtype="float32", transform=image.transform, crs=image.crs,
            ) as terrain:
                terrain.write(np.full(image.shape, 27.0, dtype="float32"), 1)
            strategy_hash, profile_id = _prepare_library(root / "library")
            token = "imagery-refinement-test-management-token"
            config = SystemHostConfig(root / "library", imagery_python_executable=Path(sys.executable))
            with SystemHost(config) as system, LocalApiServer(system, LocalApiConfig(port=0, management_token=token)) as api:
                session = system.create_project(root / "project", project_name="Refinement", enable_blender_worker=False,
                                                enable_geospatial_worker=True).session_id
                runtime = system._project_runtime(session)
                frozen = root / "frozen"
                frozen.mkdir()

                def prepare_fixture(host):
                    image = host.add_source_file(image_path, kind=SourceKind.GEOTIFF)
                    elevation = host.add_source_file(terrain_path, kind=SourceKind.GEOTIFF)
                    assembly = host.create_identity_assembly(original_name="Imagery", source_ids=(image.source_id, elevation.source_id))
                    members = host.store.list_source_assembly_members(assembly.source_id)
                    by_source = {member.source_id: member.source_member_id for member in members}
                    options = ImageryRegionOptions(by_source[image.source_id], elevation_member_id=by_source[elevation.source_id])
                    def recognize(_):
                        scores = np.zeros((CLASS_COUNT, PATCH, PATCH), dtype=np.float32)
                        scores[2] = 1
                        return scores
                    with patch("verdflow.geospatial_worker.imagery._load_predictor", return_value=(recognize, "fixture")):
                        prepared = prepare_imagery_regions({"source_id": assembly.source_id,
                            "options": options.to_document(), "model_path": "unused",
                            "sources": [{"source_path": str(image_path), "source_content_hash": image.content_hash,
                                         "source_member_id": by_source[image.source_id]}],
                            "elevation_path": str(terrain_path)}, frozen, write_candidate=True)
                    manifest = {**prepared["source_manifest"], "review": prepared["review"]}
                    (frozen / "source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                    submitted = host.submit_region_source(RegionSourceRequest(assembly.source_id, IMAGERY_REGION_PROVIDER_ID,
                                                                              options.to_document()))
                    claimed = host.runtime.claim_next("fixture", only_job_id=submitted.job_id)
                    outputs = []
                    for role, filename in (
                        ("source_manifest", "source-manifest.json"), ("vegetation_mask", "vegetation-mask.tif"),
                        ("imagery_preview", "imagery-preview.png"), ("imagery_overview", "imagery-overview.png"),
                        ("imagery_review_index", "imagery-review-index.json"), ("imagery_page_1", "imagery_page_1.json"),
                        ("regions", "regions.json"), ("geometry_metrics", "region-metrics.json"),
                    ):
                        artifact = host.store.put_file(frozen / filename, media_type="application/octet-stream")
                        outputs.append({"role": role, **asdict(artifact)})
                    result = {"source_id": assembly.source_id, "result_kind": "region_source",
                              "producer": {"id": IMAGERY_REGION_PROVIDER_ID, "version": "1"}, "output_artifacts": outputs}
                    ref = host.store.put_bytes(json.dumps(result).encode(), media_type="application/json")
                    return host.runtime.succeed(claimed.lease, result_artifact_id=ref.artifact_id).job_id

                job_id = runtime.call(prepare_fixture, timeout_seconds=20)
                endpoint = f"/projects/{session}/region-sources/{job_id}"
                def request(route, body=None):
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    with urlopen(Request(api.base_url + "/api/v1" + route,
                                         data=json.dumps(body).encode() if body is not None else None, headers=headers), timeout=15) as response:
                        return json.load(response)
                def wait(job):
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        value = request(f"/projects/{session}/jobs/{job['job_id']}")
                        if value["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                            return value
                        time.sleep(.05)
                    self.fail("Confirmation did not finish")

                selection = {"included_region_ids": ["region_00001"]}
                options = {"mode": "greenness", "strength": 55, "minimum": "300", "min_region_pixels": "1000"}
                endpoint_v2 = endpoint + "/finalize?background=1"
                completed = wait(request(endpoint_v2, {**selection, "refinement": options}))
                self.assertEqual(completed["status"], "succeeded", completed.get("error"))
                review = request(endpoint + "/imagery-preview")
                current = review["confirmed_region_set_id"]
                self.assertEqual(review["confirmed_refinement"], options)
                self.assertEqual(review["confirmed_region_ids"], selection["included_region_ids"])

                def polygon(store):
                    region_set = store.get_region_set(current)
                    self.assertEqual(region_set.region_count, 1)
                    with store.open_artifact(region_set.geometry_artifact_id) as stream:
                        shell, *holes = json.load(stream)["regions"][0]["footprint_boundary_xyz"]
                    return region_set, Polygon(shell, holes)

                region_set, retained = runtime.query(polygon, timeout_seconds=10)
                self.assertEqual(region_set.producer_job_id, completed["job_id"])
                self.assertEqual((retained.area, len(retained.interiors)), (39700, 1))
                self.assertFalse(retained.covers(Point(515, 20)))
                self.assertFalse(retained.covers(Point(1020, 20)))
                revise = {**selection, "expected_region_set_id": current}
                cancellation = request(endpoint_v2, {**revise, "refinement": options})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and system.get_job(session, cancellation["job_id"]).status is JobStatus.QUEUED:
                    time.sleep(.02)
                self.assertEqual(system.get_job(session, cancellation["job_id"]).status, JobStatus.RUNNING)
                system.request_job_cancel(session, cancellation["job_id"])
                self.assertEqual(wait(cancellation)["status"], "cancelled")
                self.assertEqual(request(endpoint + "/imagery-preview")["confirmed_region_set_id"], current)
                failed = wait(request(endpoint_v2, {**revise, "refinement": {**options, "min_region_pixels": "100000"}}))
                self.assertEqual(failed["status"], "failed")
                self.assertIn("细化后没有可确认", failed["error"]["message"])
                self.assertEqual(request(endpoint + "/imagery-preview")["confirmed_region_set_id"], current)

                def no_progress(process, path, check, timeout_seconds):
                    return real_wait(process, path, check, 0)

                real_wait = imagery_confirmation._wait_for_progress
                with patch.object(imagery_confirmation, "_wait_for_progress", no_progress):
                    stalled = wait(request(endpoint_v2, {**revise, "refinement": options}))
                self.assertEqual(stalled["status"], "failed")
                self.assertIn("没有处理进展", stalled["error"]["message"])
                self.assertEqual(request(endpoint + "/imagery-preview")["confirmed_region_set_id"], current)
                reset = wait(request(endpoint_v2, revise))
                self.assertEqual(reset["status"], "succeeded", reset.get("error"))
                reset_review = request(endpoint + "/imagery-preview")
                self.assertIsNone(reset_review["confirmed_refinement"])
                self.assertNotEqual(reset_review["confirmed_region_set_id"], current)

                # Reapply, then let the real generation, elevation and DTS export chain consume the refined revision.
                reapplied = wait(request(endpoint_v2, {**revise, "expected_region_set_id": reset_review["confirmed_region_set_id"],
                                                      "refinement": options}))
                self.assertEqual(reapplied["status"], "succeeded", reapplied.get("error"))
                current = request(endpoint + "/imagery-preview")["confirmed_region_set_id"]
                execution = system.submit_plant_export(session, PlantExportRequest(PlantPreviewRequest(
                    region_set.scene_revision_id, current, ("region_00001",), strategy_hash, "dts", "dts_default",
                    random_seed=17, profile_id=profile_id,
                ))).execution
                deadline = time.monotonic() + 45
                while execution.status.value not in {"succeeded", "failed", "cancelled"} and time.monotonic() < deadline:
                    time.sleep(.05)
                    execution = system.get_plant_export(session, execution.execution_id)
                self.assertEqual(execution.status.value, "succeeded", execution.error_message)
                def verify_export(store):
                    point_set = store.get_point_set(execution.elevation_point_set_id)
                    self.assertEqual(point_set.min_z, 27.0)
                    with store.open_artifact(point_set.point_artifact_id) as stream:
                        planted = json.load(stream)["points"]
                    self.assertTrue(planted)
                    self.assertTrue(all(retained.covers(Point(row["resolved_position"][:2])) for row in planted))
                    record = store.get_export_record(execution.export_id)
                    with store.open_artifact(record.output_artifact_id) as stream:
                        self.assertGreater(len(ET.parse(stream).getroot()), 0)
                runtime.query(verify_export, timeout_seconds=10)


@unittest.skipUnless(np is not None and rasterio is not None and all(os.environ.get(name) for name in (
    "VERDFLOW_IMAGERY_TEST_SOURCE", "VERDFLOW_IMAGERY_TEST_MODEL", "VERDFLOW_IMAGERY_TEST_MASK",
    "VERDFLOW_IMAGERY_TEST_PYTHON",
)), "licensed local imagery/model fixture not configured")
class ImageryWorkerVerticalTests(unittest.TestCase):
    def test_studio_http_review_reopens_and_keeps_explicit_terrain(self):
        from rasterio.transform import from_bounds
        from shapely.geometry import Polygon
        from tests.unit.test_dts_live_vertical_runtime import _prepare_library

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = Path(os.environ["VERDFLOW_IMAGERY_TEST_SOURCE"])
            terrain_path = root / "terrain.tif"
            with rasterio.open(source_path) as source:
                with rasterio.open(terrain_path, "w", driver="GTiff", width=5, height=5, count=1,
                        dtype="float32", crs=source.crs, transform=from_bounds(*source.bounds, 5, 5)) as terrain:
                    terrain.write(np.full((5, 5), 27.0, dtype=np.float32), 1)
            config = SystemHostConfig(root / "library", imagery_model_path=Path(os.environ["VERDFLOW_IMAGERY_TEST_MODEL"]),
                imagery_python_executable=Path(os.environ["VERDFLOW_IMAGERY_TEST_PYTHON"]),
                imagery_backend=os.environ.get("VERDFLOW_IMAGERY_TEST_BACKEND", "cpu"))
            path = root / "imagery.verdflow"
            strategy_hash, profile_id = _prepare_library(config.library_path)
            token = "imagery-test-management-token-for-local-api"
            with SystemHost(config) as system, LocalApiServer(system, LocalApiConfig(port=0, management_token=token)) as api:
                def request(path, body=None, *, authorized=True):
                    headers = {"Authorization": f"Bearer {token}"} if authorized else {}
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    with urlopen(Request(api.base_url + "/api/v1" + path,
                            data=json.dumps(body).encode() if body is not None else None, headers=headers), timeout=30) as response:
                        return json.load(response)

                self.assertTrue(request("/health")["imagery_available"])
                project = system.create_project(path, project_name="Imagery Studio", enable_blender_worker=False,
                                                enable_geospatial_worker=True)
                session = project.session_id
                endpoint = f"/projects/{session}/region-sources"
                intake = request(f"/projects/{session}/source-intake", {"workflow": "imagery", "source_path": str(source_path),
                    "elevation_path": str(terrain_path), "idempotency_key": "imagery-intake-fixture"})
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    intake_job = system.get_job(session, intake["job_id"])
                    if intake_job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                        break
                    time.sleep(0.05)
                self.assertEqual(intake_job.status, JobStatus.SUCCEEDED, intake_job.error)

                def saved_input(store):
                    with store.open_artifact(intake_job.result_artifact_id) as stream:
                        saved = json.load(stream)
                    return saved["preparation_id"], tuple(store.get_source(item) for item in saved["source_ids"])

                job_id, (image, terrain) = system._project_runtime(session).query(saved_input, timeout_seconds=5)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    state = system.get_job(session, job_id)
                    if state.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                        break
                    time.sleep(0.1)
                self.assertEqual(state.status, JobStatus.SUCCEEDED, state.error)
                self.assertEqual(request(f"/projects/{session}/source-intake", {"workflow": "imagery", "source_path": str(source_path),
                    "elevation_path": str(terrain_path), "idempotency_key": "imagery-intake-fixture"})["job_id"], intake["job_id"])
                self.assertIsNone(system.get_project_business_summary(session).region_set_id)
                system.close_project(session)
                session = system.open_project(path, enable_blender_worker=False, enable_geospatial_worker=True).session_id
                summary = request(f"/projects/{session}/summary")
                self.assertEqual(summary["state"], "awaiting_region_confirmation")
                self.assertEqual(summary["execution_id"], job_id)
                endpoint = f"/projects/{session}/region-sources/{job_id}"
                with self.assertRaises(HTTPError) as unauthorized:
                    request(endpoint + "/imagery-preview", authorized=False)
                self.assertEqual(unauthorized.exception.code, 403)
                candidate = request(endpoint + "/imagery-preview")
                tile_path = endpoint + "/imagery-tile?level=0&column=0&row=0"
                with self.assertRaises(HTTPError) as unauthorized_tile:
                    request(tile_path, authorized=False)
                self.assertEqual(unauthorized_tile.exception.code, 403)
                tile = request(tile_path)
                self.assertTrue(tile["base_png"].startswith("iVBOR"))
                self.assertTrue(tile["ids_png"].startswith("iVBOR"))
                self.assertEqual(tile["transform"], candidate["source_grid"]["transform"])
                with self.assertRaises(HTTPError) as outside:
                    request(endpoint + "/imagery-tile?level=0&column=999999&row=0")
                self.assertEqual(outside.exception.code, 400)
                self.assertTrue(candidate["preview_png_base64"].startswith("iVBOR"))
                self.assertTrue(candidate["overview_png_base64"].startswith("iVBOR"))
                self.assertEqual(len(candidate["review_index"]), candidate["region_count"])
                self.assertTrue(all(row["pixel_count"] > 0 for row in candidate["review_index"]))
                self.assertGreater(len(candidate["regions"]), 1)
                rows = list(candidate["regions"])
                for page in range(2, candidate["page_count"] + 1):
                    other = request(endpoint + f"/imagery-preview?page={page}")
                    self.assertIsNone(other["preview_png_base64"])
                    self.assertIsNone(other["overview_png_base64"])
                    self.assertIsNone(other["review_index"])
                    self.assertEqual(other["page"], page)
                    rows.extend(other["regions"])
                self.assertEqual(len({row["region_id"] for row in rows}), candidate["region_count"])
                def area(row):
                    shell, *holes = row["footprint_boundary_xyz"]
                    return Polygon(shell, holes).area

                selected = min((row for row in rows if area(row) >= 5000), key=area)
                selection = [selected["region_id"]]
                with self.assertRaises(HTTPError) as invalid:
                    request(endpoint + "/finalize", {"included_region_ids": ["foreign"]})
                self.assertEqual(invalid.exception.code, 409)
                self.assertIsNone(system.get_project_business_summary(session).region_set_id)
                excluded = [row["region_id"] for row in rows if row["region_id"] not in selection]
                with self.assertRaises(HTTPError) as invalid_exclusion:
                    request(endpoint + "/finalize", {"excluded_region_ids": ["foreign"]})
                self.assertEqual(invalid_exclusion.exception.code, 400)
                self.assertIsNone(system.get_project_business_summary(session).region_set_id)
                finalized = request(endpoint + "/finalize", {"excluded_region_ids": excluded})
                self.assertEqual(finalized["region_set"]["region_count"], 1)
                self.assertEqual(request(endpoint + "/finalize", {"included_region_ids": selection}), finalized)
                scene_id = finalized["scene_revision"]["scene_revision_id"]
                initial_id = finalized["region_set"]["region_set_id"]
                self.assertEqual(request(f"/projects/{session}/summary")["imagery_job_id"], job_id)
                confirmed = request(endpoint + "/imagery-preview")
                self.assertEqual(confirmed["confirmed_region_set_id"], initial_id)
                self.assertEqual(confirmed["confirmed_region_ids"], selection)
                self.assertIsNone(confirmed["selection_locked_reason"])
                additional = next(row["region_id"] for row in rows if row["region_id"] != selection[0])
                revised_selection = [*selection, additional]
                revision_request = {"expected_region_set_id": initial_id, "included_region_ids": revised_selection}
                with self.assertRaises(HTTPError) as unauthorized_revision:
                    request(endpoint + "/finalize", revision_request, authorized=False)
                self.assertEqual(unauthorized_revision.exception.code, 403)
                with patch.object(ProjectStore, "current_planting_composition", return_value=object()):
                    with self.assertRaises(HTTPError) as locked_revision:
                        request(endpoint + "/finalize", revision_request)
                self.assertEqual(locked_revision.exception.code, 409)
                self.assertEqual(system.get_project_business_summary(session).region_set_id, initial_id)

                def wait_for_confirmation(job):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        value = request(f"/projects/{session}/jobs/{job['job_id']}")
                        if value["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                            return value
                        time.sleep(0.05)
                    self.fail("Background confirmation did not finish")

                background = endpoint + "/finalize?background=1"
                with patch.object(ProjectStore, "create_region_set", side_effect=OSError(errno.ENOSPC, "fixture")):
                    failed = wait_for_confirmation(request(background, revision_request))
                self.assertEqual(failed["status"], "failed")
                self.assertIn("磁盘空间不足", failed["error"]["message"])
                self.assertEqual(request(endpoint + "/imagery-preview")["confirmed_region_ids"], selection)
                submitted = request(background, revision_request)
                self.assertEqual(submitted["status"], "queued")
                self.assertEqual(wait_for_confirmation(submitted)["status"], "succeeded")
                revised = request(f"/projects/{session}/summary")
                revised_id = revised["region_set_id"]
                self.assertNotEqual(revised_id, initial_id)
                self.assertEqual(revised["scene_revision_id"], scene_id)
                self.assertCountEqual(request(endpoint + "/imagery-preview")["confirmed_region_ids"], revised_selection)
                self.assertEqual(system.get_job(session, job_id).result_artifact_id, state.result_artifact_id)
                previous = system._project_runtime(session).query(lambda store: store.list_regions(initial_id), timeout_seconds=5)
                self.assertEqual([row.region_id for row in previous], selection)
                with self.assertRaises(HTTPError) as stale:
                    request(endpoint + "/finalize", revision_request)
                self.assertEqual(stale.exception.code, 409)
                self.assertEqual(request(endpoint + "/finalize", {
                    "expected_region_set_id": revised_id, "included_region_ids": list(reversed(revised_selection)),
                })["region_set"]["region_set_id"], revised_id)

            with SystemHost(SystemHostConfig(root / "library")) as reopened:
                session = reopened.open_project(path, enable_blender_worker=False, enable_geospatial_worker=True).session_id
                self.assertFalse(reopened.imagery_available())
                self.assertEqual(reopened.get_project_business_summary(session).state, "ready_to_plant")
                self.assertEqual(reopened.get_project_business_summary(session).imagery_job_id, job_id)
                restored = reopened.imagery_candidate(session, job_id)
                self.assertEqual(restored.confirmed_region_set_id, revised_id)
                self.assertCountEqual(restored.confirmed_region_ids, revised_selection)
                workflow = reopened._project_runtime(session).call(
                    lambda host: resolve_plant_workflow(host, scene_revision_id=scene_id, target_type="dts"),
                    timeout_seconds=10,
                )
                self.assertEqual(workflow.sampling.source.source_id, terrain.source_id)
                self.assertEqual(workflow.recipe_id, IMAGERY_REGION_RECIPE_ID)
                submission = reopened.submit_plant_export(session, PlantExportRequest(PlantPreviewRequest(
                    scene_id, revised_id, tuple(selection),
                    strategy_hash, "dts", "dts_default", random_seed=17, profile_id=profile_id,
                )))
                deadline = time.monotonic() + 90
                execution = submission.execution
                while execution.status.value not in {"succeeded", "failed", "cancelled"} and time.monotonic() < deadline:
                    time.sleep(0.1)
                    execution = reopened.get_plant_export(session, execution.execution_id)
                self.assertEqual(execution.status.value, "succeeded", execution.error_message)
                self.assertIsNotNone(execution.export_id)
                def check_output(store):
                    resolved = store.get_point_set(execution.elevation_point_set_id)
                    self.assertEqual(resolved.min_z, 27.0)
                    record = store.get_export_record(execution.export_id)
                    with store.open_artifact(record.output_artifact_id) as stream:
                        self.assertGreater(len(ET.parse(stream).getroot()), 0)
                reopened._project_runtime(session).query(check_output, timeout_seconds=10)

    def test_validated_model_preserves_mask_and_reuses_explicit_elevation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = Path(os.environ["VERDFLOW_IMAGERY_TEST_SOURCE"])
            with rasterio.open(source_path) as image:
                transform, crs = image.transform, image.crs
                width, height = image.width, image.height
            terrain_path = root / "fixture-elevation.tif"
            with rasterio.open(terrain_path, "w", driver="GTiff", width=width, height=height, count=1,
                               dtype="float32", crs=crs, transform=transform, compress="deflate") as terrain:
                terrain.write(np.full((height, width), 12.5, dtype=np.float32), 1)
            factory = GeospatialExtensionFactory(root / "runtime", root / "cache",
                python_executable=Path(os.environ["VERDFLOW_IMAGERY_TEST_PYTHON"]),
                imagery_model_path=Path(os.environ["VERDFLOW_IMAGERY_TEST_MODEL"]))
            config = CoreHostConfig(root / "project.verdflow", enable_blender_worker=False,
                                    enable_project_maintenance=False)
            with CoreHost.create(config, project_name="Imagery validation", extension_factories=(factory,)) as host:
                image = host.add_source_file(source_path, kind=SourceKind.GEOTIFF)
                terrain = host.add_source_file(terrain_path, kind=SourceKind.GEOTIFF)
                assembly = host.create_identity_assembly(original_name="Terrain then image",
                    source_ids=(terrain.source_id, image.source_id))
                members = {member.source_id: member for member in host.store.list_source_assembly_members(assembly.source_id)}
                options = ImageryRegionOptions(members[image.source_id].source_member_id,
                    backend=os.environ.get("VERDFLOW_IMAGERY_TEST_BACKEND", "cpu"))
                job = host.submit_region_source(RegionSourceRequest(assembly.source_id, IMAGERY_REGION_PROVIDER_ID,
                                                                    options.to_document()))
                completed = host.service_loop.run_until_terminal(job.job_id, timeout_seconds=120)
                self.assertEqual(completed.status, JobStatus.SUCCEEDED, completed.error)
                self.assertIsNone(host.store.project_workflow_state().region_set_id)
                with host.store.open_artifact(completed.result_artifact_id) as stream:
                    result = json.load(stream)
                outputs = {item["role"]: item["artifact_id"] for item in result["output_artifacts"]}
                path = host.store.materialize_artifact(outputs["vegetation_mask"], root / "actual.tif")
                with rasterio.open(path) as actual, rasterio.open(os.environ["VERDFLOW_IMAGERY_TEST_MASK"]) as expected:
                    self.assertEqual(actual.crs, expected.crs)
                    self.assertEqual(actual.transform, expected.transform)
                    np.testing.assert_array_equal(actual.read(1), expected.read(1))
                with host.store.open_artifact(outputs["regions"]) as stream:
                    regions_document = json.load(stream)
                scene, regions = host.finalize_region_source(job.job_id)
                self.assertEqual(scene.importer_id, IMAGERY_REGION_PROVIDER_ID)
                self.assertEqual(regions.region_count, regions_document["region_count"])
                self.assertTrue(all(row["source_member_id"] == options.imagery_member_id for row in regions_document["regions"]))
                binding = SamplingSourceRef("source", terrain.source_id, terrain.artifact_id, terrain.content_hash,
                    scene.coordinate_frame_id, assembly_source_id=assembly.source_id,
                    source_member_id=members[terrain.source_id].source_member_id)
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    host.workflows.resolve(host.store, scene_revision_id=scene.scene_revision_id,
                        recipe_id=IMAGERY_REGION_RECIPE_ID, target_type="dts")
                workflow = host.workflows.resolve(host.store, scene_revision_id=scene.scene_revision_id,
                    recipe_id=IMAGERY_REGION_RECIPE_ID, target_type="dts", terrain_source=binding)
                from shapely.geometry import Polygon
                row = regions_document["regions"][0]
                shell, *holes = row["footprint_boundary_xyz"]
                point = Polygon(shell, holes).representative_point()
                generated = _generated_point_set(host, SimpleNamespace(scene_revision=scene, region_set=regions),
                    region_id=row["id"], xy=(point.x, point.y))
                sampled = host.sample_elevation(ElevationSamplingRequest(generated.point_set_id,
                    workflow.elevation_provider_id, binding=workflow.sampling), timeout_seconds=30)
                self.assertEqual(sampled.point_set.min_z, 12.5)
                self.assertEqual(list((root / "runtime").iterdir()), [])
                print(f"\nImagery backend={options.backend}, regions={regions.region_count}, mask identical, Z=12.5", flush=True)
