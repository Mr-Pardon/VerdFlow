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

    def test_holes_and_invalid_geometry_fail_entire_import(self) -> None:
        cases = [
            ([SQUARE, [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]], "shp_holes_unsupported"),
            ([[[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]], "shp_invalid_geometry"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.zip"
            for rings, code in cases:
                source.write_bytes(dataset(rings))
                with self.assertRaises(ShapefileImportError) as caught:
                    import_polygon_shapefile(source)
                self.assertEqual(caught.exception.code, code)

    def test_missing_companions_unsafe_zip_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.zip"
            source.write_bytes(dataset([SQUARE]))
            with patch("verdflow.geospatial_worker.polygon_import.MAX_DATASET_BYTES", 4):
                with self.assertRaises(ShapefileImportError) as caught:
                    import_polygon_shapefile(source)
                self.assertEqual(caught.exception.code, "shp_too_large")
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
