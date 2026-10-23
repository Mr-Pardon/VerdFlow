from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters/geospatial-worker/src"))
from tests.unit.test_dts_live_connector_server import _request, _resolve
from verdflow.dts_adapter import normalize_live_regions
from verdflow.dts_live_connector import DtsLiveConnectorServer
from verdflow.dts_live_connector.region_exchange import (
    RegionExchangeBusyError, RegionExportResult, RegionExchangeService,
)
from verdflow.geospatial_worker import ShapefilePolygon, export_polygon_shapefile
from verdflow.system_host import DtsLiveConnectorManager


REGIONS = [
    {"region_id": "a", "name": "\u9009\u533a 1", "coordinates": [[500000, 2000000, 12], [500010, 2000000, 13], [500010, 2000020, 14], [500000, 2000020, 12]]},
    {"region_id": "b", "name": "\u9009\u533a 2", "coordinates": [[0, 0, 1], [0, 2, 3], [2, 0, 2], [0, 0, 1]]},
]


class LiveRegionExportTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Win32 dialog ABI")
    def test_native_picker_structure_and_unicode_result_without_opening_dialog(self) -> None:
        import ctypes
        from types import SimpleNamespace
        from verdflow.dts_live_connector.region_exchange import _choose_zip_destination

        destination = "D:\\exports\\\u9009\u533a.zip"

        def save(pointer):
            options = pointer._obj
            self.assertEqual(options.lStructSize, ctypes.sizeof(options))
            self.assertEqual(options.lpstrInitialDir, "D:\\exports")
            self.assertEqual(options.nMaxFile, 32768)
            # LPWSTR fields auto-convert on read, so write through the stored pointer.
            address = ctypes.c_void_p.from_address(
                ctypes.addressof(options) + type(options).lpstrFile.offset
            ).value
            buffer = ctypes.create_unicode_buffer(destination)
            ctypes.memmove(address, buffer, ctypes.sizeof(buffer))
            return True

        foreground = lambda: 0
        extended_error = lambda: 0
        dialog = SimpleNamespace(GetSaveFileNameW=save, CommDlgExtendedError=extended_error)
        user = SimpleNamespace(GetForegroundWindow=foreground)
        with patch("ctypes.WinDLL", side_effect=lambda name, **_: dialog if name == "comdlg32" else user):
            self.assertEqual(_choose_zip_destination(Path("D:/exports")), Path(destination))
            dialog.GetSaveFileNameW = lambda _: False
            self.assertIsNone(_choose_zip_destination(None))
            dialog.CommDlgExtendedError = lambda: 1
            with self.assertRaises(OSError):
                _choose_zip_destination(None)

    def test_polygon_zip_roundtrip(self) -> None:
        import shapefile

        regions = normalize_live_regions(REGIONS)
        payload = export_polygon_shapefile(tuple(
            ShapefilePolygon(r.region_id, r.name, r.coordinates) for r in regions
        ))
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(set(archive.namelist()), {"regions.shp", "regions.shx", "regions.dbf", "regions.cpg"})
            self.assertEqual(archive.read("regions.cpg"), b"UTF-8")
            reader = shapefile.Reader(**{
                suffix: io.BytesIO(archive.read(f"regions.{suffix}")) for suffix in ("shp", "shx", "dbf")
            }, encoding="utf-8")
            self.assertEqual(reader.shapeType, shapefile.POLYGON)
            self.assertEqual(len(reader), 2)
            for i, region in enumerate(regions):
                shape = reader.shape(i)
                self.assertEqual(shape.points[0], shape.points[-1])
                self.assertEqual(set(map(tuple, shape.points)), {p[:2] for p in region.coordinates})
                self.assertLess(shapefile.signed_area(shape.points), 0)
                record = reader.record(i).as_dict()
                self.assertEqual(record["NAME"], region.name)
                self.assertEqual(record["REGION_ID"], region.region_id)
            self.assertEqual(reader.record(0).as_dict()["AREA_2D"], 200)

    def test_save_cancel_failure_and_directory_memory(self) -> None:
        regions = normalize_live_regions(REGIONS)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "regions.zip"
            initial = []
            service = RegionExchangeService(lambda path: initial.append(path) or destination)
            self.assertTrue(service.export_regions(regions).saved)
            original = destination.read_bytes()
            self.assertTrue(service.export_regions(regions).saved)
            self.assertEqual(initial, [None, destination.parent])
            with patch("verdflow.dts_live_connector.region_exchange.os.replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    service.export_regions(regions)
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [destination])
            self.assertTrue(service.export_regions(regions).saved)
            cancelled = RegionExchangeService(lambda _: None).export_regions(regions)
            self.assertFalse(cancelled.saved)
            self.assertIsNone(cancelled.path)

    def test_export_busy_does_not_open_another_dialog(self) -> None:
        regions = normalize_live_regions(REGIONS)

        def picker(_):
            with self.assertRaises(RegionExchangeBusyError):
                service.export_regions(regions)
            return None

        service = RegionExchangeService(picker)
        self.assertFalse(service.export_regions(regions).saved)

    def test_route_requires_auth_and_does_not_start_planting(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        calls = []

        def export(regions):
            calls.append(regions)
            return RegionExportResult(True, "regions.zip", len(regions))

        with DtsLiveConnectorServer(manager, export_regions=export) as server:
            url = server.base_url + "/api/v1/selections/export-shp"
            status, _, _ = _request(url, document={"regions": REGIONS}, origin=server.base_url)
            self.assertEqual(status, 401)
            _, _, headers = _request(server.base_url + "/api/v1/bootstrap/exchange", document={
                "connector_session_id": bootstrap.connector_session_id,
                "bootstrap_nonce": bootstrap.bootstrap_nonce,
            }, origin=server.base_url)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            status, _, _ = _request(url, document={"regions": REGIONS}, cookie=cookie + "invalid", origin=server.base_url)
            self.assertEqual(status, 401)
            self.assertEqual(len(calls), 0)
            _, before, _ = _request(server.base_url + "/api/v1/session", cookie=cookie)
            status, result, _ = _request(url, document={"regions": REGIONS}, cookie=cookie, origin=server.base_url)
            self.assertEqual(status, 200)
            self.assertEqual(result["region_count"], 2)
            self.assertEqual(len(calls), 1)
            _, after, _ = _request(server.base_url + "/api/v1/session", cookie=cookie)
            self.assertEqual(before, after)
            for invalid in ([], None, [REGIONS[0], REGIONS[0]]):
                status, _, _ = _request(url, document={"regions": invalid}, cookie=cookie, origin=server.base_url)
                self.assertEqual(status, 400)
            status, _, _ = _request(url, document={"regions": REGIONS}, cookie=cookie, origin="https://invalid.example")
            self.assertEqual(status, 403)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
