from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests.unit.test_live_region_export import REGIONS, _request, _resolve
from verdflow.dts_adapter import normalize_live_regions
from verdflow.dts_live_connector import DtsLiveConnectorServer
from verdflow.dts_live_connector.region_exchange import (
    RegionExchangeBusyError, RegionExchangeService, RegionImportError, RegionImportResult,
)
from verdflow.geospatial_worker import (
    ShapefileImportError, ShapefilePolygon, export_polygon_shapefile, import_polygon_shapefile,
)
from verdflow.system_host import DtsLiveConnectorManager


def dataset(rings, *, encoding="utf-8", polygon_z=False) -> bytes:
    import shapefile
    streams = {suffix: io.BytesIO() for suffix in ("shp", "shx", "dbf")}
    with shapefile.Writer(**streams, shapeType=shapefile.POLYGONZ if polygon_z else shapefile.POLYGON, encoding=encoding) as writer:
        writer.field("NAME", "C", size=80)
        (writer.polyz if polygon_z else writer.poly)(rings)
        writer.record("\u9009\u533a\u6837\u672c")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for suffix, stream in streams.items():
            archive.writestr(f"nested/data.{suffix}", stream.getvalue())
        archive.writestr("nested/data.cpg", encoding)
    return output.getvalue()


SQUARE = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]


class LiveRegionImportTests(unittest.TestCase):
    def test_export_import_roundtrip_zip_and_shp(self) -> None:
        regions = normalize_live_regions(REGIONS)
        payload = export_polygon_shapefile(tuple(ShapefilePolygon(r.region_id, r.name, r.coordinates) for r in regions))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "regions.zip"
            source.write_bytes(payload)
            zipped = import_polygon_shapefile(source)
            with zipfile.ZipFile(source) as archive:
                archive.extractall(directory)
            loose = import_polygon_shapefile(Path(directory) / "regions.shp")
            self.assertEqual(zipped, loose)
            self.assertEqual(len(zipped), 2)
            self.assertEqual(zipped[0].name, regions[0].name)
            self.assertEqual({p[:2] for p in zipped[0].coordinates}, {p[:2] for p in regions[0].coordinates})
            self.assertEqual({p[2] for p in zipped[0].coordinates}, {13.0})

    def test_multipart_split_and_cpg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "multipart.zip"
            source.write_bytes(dataset([SQUARE, [[x + 30, y] for x, y in SQUARE]], encoding="gbk"))
            result = import_polygon_shapefile(source)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0].name, "\u9009\u533a\u6837\u672c (1)")
            self.assertEqual(result[1].name, "\u9009\u533a\u6837\u672c (2)")
            self.assertEqual({p[2] for p in result[0].coordinates}, {0.0})

    def test_polygon_z_keeps_reference_heights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "z.zip"
            source.write_bytes(dataset([[[x, y, 20 + x] for x, y in SQUARE]], polygon_z=True))
            result = import_polygon_shapefile(source)
            self.assertEqual({tuple(p) for p in result[0].coordinates}, {(x, y, 20 + x) for x, y in SQUARE})

    def test_valid_holes_survive_import_export_and_live_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "holes.zip"
            source.write_bytes(dataset([SQUARE, [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]]))
            polygon, = import_polygon_shapefile(source)
            self.assertEqual(len(polygon.holes), 1)
            regions = normalize_live_regions([{"region_id": "a", "name": "Garden", "coordinates": polygon.coordinates, "holes": polygon.holes}])
            self.assertEqual(regions[0].holes, polygon.holes)
            service = RegionExchangeService(import_picker=lambda _: source)
            self.assertEqual(service.import_regions().regions[0].holes, polygon.holes)
            source.write_bytes(export_polygon_shapefile((polygon,)))
            restored, = import_polygon_shapefile(source)
            self.assertEqual({p[:2] for p in restored.holes[0]}, {p[:2] for p in polygon.holes[0]})

    def test_invalid_geometry_fails_entire_import_with_feature_number(self) -> None:
        cases = [
            ([[[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]], "shp_invalid_geometry"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.zip"
            for rings, code in cases:
                source.write_bytes(dataset(rings))
                with self.assertRaises(ShapefileImportError) as caught:
                    import_polygon_shapefile(source)
                self.assertEqual(caught.exception.code, code)
                self.assertIn("第 1 条", str(caught.exception))
                self.assertIn("修复", str(caught.exception))

    def test_missing_companions_unsafe_zip_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.zip"
            source.write_bytes(dataset([SQUARE]))
            with patch("verdflow.geospatial_worker.polygon_import.MAX_DATASET_BYTES", 4):
                with self.assertRaises(ShapefileImportError) as caught:
                    import_polygon_shapefile(source)
                self.assertEqual(caught.exception.code, "shp_file_size_limit")
                self.assertIn("MiB", str(caught.exception))
                self.assertIn("工作台", str(caught.exception))
            with zipfile.ZipFile(source, "a") as archive:
                archive.writestr("../data.shp", b"unsafe")
            with self.assertRaises(ShapefileImportError) as caught:
                import_polygon_shapefile(source)
            self.assertEqual(caught.exception.code, "shp_invalid_dataset")
            shp = Path(directory) / "data.shp"
            shp.write_bytes(b"missing")
            with self.assertRaises(ShapefileImportError) as caught:
                import_polygon_shapefile(shp)
            self.assertEqual(caught.exception.code, "shp_companions_missing")

    def test_vertex_limit_includes_holes_and_live_budget_cannot_be_bypassed_by_appending(self) -> None:
        from verdflow.dts_adapter import DtsLiveSelectionLimitError
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "holes.zip"
            source.write_bytes(dataset([SQUARE, [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]]))
            with patch("verdflow.geospatial_worker.polygon_import.MAX_VERTICES", 9):
                with self.assertRaises(ShapefileImportError) as caught:
                    import_polygon_shapefile(source)
                self.assertEqual(caught.exception.code, "shp_vertex_limit")
        with patch("verdflow.dts_adapter.live.DTS_LIVE_MAX_VERTICES", 6):
            with self.assertRaises(DtsLiveSelectionLimitError):
                normalize_live_regions(REGIONS)

    def test_import_http_preserves_semantic_error_code_and_public_message(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        def reject():
            raise RegionImportError("shp_vertex_limit", "顶点超过实时上限，请改用工作台 SHP＋TIF")
        with DtsLiveConnectorServer(manager, import_regions=reject) as server:
            _, _, headers = _request(server.base_url + "/api/v1/bootstrap/exchange", document={
                "connector_session_id": bootstrap.connector_session_id, "bootstrap_nonce": bootstrap.bootstrap_nonce,
            }, origin=server.base_url)
            status, result, _ = _request(server.base_url + "/api/v1/selections/import-shp", document={},
                                        cookie=headers["Set-Cookie"].split(";", 1)[0], origin=server.base_url)
            self.assertEqual(status, 400)
            self.assertEqual(result["error"]["code"], "shp_vertex_limit")
            self.assertIn("工作台", result["error"]["message"])

    def test_service_cancel_new_ids_and_shared_dialog_lock(self) -> None:
        self.assertFalse(RegionExchangeService(import_picker=lambda _: None).import_regions().imported)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.zip"
            source.write_bytes(dataset([SQUARE]))
            initial = []

            def pick(path):
                initial.append(path)
                with self.assertRaises(RegionExchangeBusyError):
                    service.export_regions(normalize_live_regions(REGIONS))
                return source

            service = RegionExchangeService(import_picker=pick)
            first = service.import_regions()
            second = service.import_regions()
            self.assertNotEqual(first.regions[0].region_id, second.regions[0].region_id)
            self.assertEqual(first.regions[0].coordinates, second.regions[0].coordinates)
            self.assertEqual(initial, [None, source.parent])
            source.write_bytes(b"bad zip")
            with self.assertRaises(RegionImportError):
                service.import_regions()

    def test_http_import_is_authenticated_and_never_accepts_client_path(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        calls = []

        def importer():
            calls.append(True)
            return RegionImportResult(True, "regions.zip", normalize_live_regions(REGIONS))

        with DtsLiveConnectorServer(manager, import_regions=importer) as server:
            url = server.base_url + "/api/v1/selections/import-shp"
            status, _, _ = _request(url, document={}, origin=server.base_url)
            self.assertEqual(status, 401)
            _, _, headers = _request(server.base_url + "/api/v1/bootstrap/exchange", document={
                "connector_session_id": bootstrap.connector_session_id, "bootstrap_nonce": bootstrap.bootstrap_nonce,
            }, origin=server.base_url)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            status, _, _ = _request(url, document={"path": "C:/unapproved.shp"}, cookie=cookie, origin=server.base_url)
            self.assertEqual(status, 400)
            self.assertEqual(calls, [])
            _, before, _ = _request(server.base_url + "/api/v1/session", cookie=cookie)
            status, result, _ = _request(url, document={}, cookie=cookie, origin=server.base_url)
            self.assertEqual(status, 200)
            self.assertEqual(len(result["regions"]), 2)
            self.assertTrue(result["imported"])
            _, after, _ = _request(server.base_url + "/api/v1/session", cookie=cookie)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
