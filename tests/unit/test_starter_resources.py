from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(path) for parent in (ROOT / "packages", ROOT / "apps", ROOT / "adapters") for path in parent.glob("*/src")]

from verdflow.local_api import LocalApiConfig, LocalApiServer
from verdflow.planting import load_catalog_yaml
from verdflow.strategy_worker import inspect_strategy_package, write_strategy_package_archive
from verdflow.system_host import AssetCatalogSaveRequest, SystemHost, SystemHostConfig
from verdflow.system_host.resource_downloads import (
    DownloadCancelled, StarterManifest, StarterResource, download_package, parse_manifest, require_url,
)
from verdflow.system_host.starter_resources import StarterResourceService


def resource(id="sample", payload=b"package", kind="strategies", identity="sample", version="1.0.0"):
    filename = f"{id}.{'vfa' if kind == 'assets' else 'vfs'}"
    return StarterResource(id, id, kind, version, identity, filename, len(payload), hashlib.sha256(payload).hexdigest(),
        f"https://github.com/Mr-Pardon/verdflow-resources/releases/download/sample-v1/{filename}",
        f"https://mr-pardon.github.io/verdflow-resources/?id={id}")


def finish(service, operation):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        operation = service.get(operation.operation_id)
        if operation.status not in {"running", "cancelling"}:
            return operation
        time.sleep(0.01)
    raise AssertionError("Import did not finish")


class StarterDownloadTests(unittest.TestCase):
    def test_manifest_rejects_unsafe_or_inconsistent_entries(self):
        item = asdict(resource())
        def parse(items):
            return parse_manifest(json.dumps({"schemaVersion": 1, "revision": "a" * 64, "resources": items}).encode())
        self.assertEqual(parse([item]).resources[0].id, "sample")
        for changes in ({"kind": "plans"}, {"url": "http://127.0.0.1/a.vfs"}, {"filename": "../sample.vfs"},
                        {"bytes": True}, {"bytes": 1 << 30}, {"sha256": "bad"}, {"page_url": "javascript:alert(1)"},
                        {"url": "https://github.com/other/repo/releases/download/v1/sample.vfs"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse([{**item, **changes}])
        with self.assertRaises(ValueError):
            parse([item, item])

    def test_redirects_are_https_and_only_fixed_repository_or_github_cdn(self):
        for url in ("http://release-assets.githubusercontent.com/a", "https://127.0.0.1/a", "https://evil.test/a",
                    "https://user@release-assets.githubusercontent.com/a", "https://github.com/Mr-Pardon/verdflow-resources/releases/latest/download/sample.vfs"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                require_url(url, redirect=True)
        require_url("https://release-assets.githubusercontent.com/a?signature=value", redirect=True)

    def test_download_checks_size_hash_and_cancel_before_import(self):
        for payload, expected, cancel in [(b"package", None, False), (b"truncated", ValueError, False),
                                          (b"PACKAGE", ValueError, False), (b"package", DownloadCancelled, True)]:
            with self.subTest(payload=payload, cancel=cancel), tempfile.TemporaryDirectory() as temporary:
                event = Event()
                if cancel:
                    event.set()
                opener = Mock()
                opener.open.return_value = io.BytesIO(payload)
                path = Path(temporary) / "download.vfs"
                with patch("verdflow.system_host.resource_downloads.build_opener", return_value=opener):
                    if expected:
                        with self.assertRaises(expected):
                            download_package(resource(), path, event, lambda _: None)
                    else:
                        download_package(resource(), path, event, lambda _: None)
                        self.assertEqual(path.read_bytes(), payload)


class StarterServiceTests(unittest.TestCase):
    def test_partial_failure_retains_success_and_cleans_temporary_files(self):
        manifest = StarterManifest("a" * 64, (resource("one"), resource("two")))
        paths = []
        def download(item, path, cancel, progress):
            paths.append(path)
            path.write_bytes(b"package")
        def importer(item, path):
            if item.id == "one":
                raise ValueError("Conflict; keep local data")
            return True
        service = StarterResourceService(importer)
        try:
            with patch("verdflow.system_host.starter_resources.load_manifest", return_value=manifest), patch("verdflow.system_host.starter_resources.download_package", download):
                service.manifest()
                with self.assertRaises(ValueError):
                    service.start("stale")
                result = finish(service, service.start(manifest.revision))
            self.assertEqual([item.status for item in result.items], ["failed", "existing"])
            self.assertTrue(all(not path.parent.exists() for path in paths))
        finally:
            service.close()

    def test_cancel_during_download_never_imports_and_close_joins_worker(self):
        manifest = StarterManifest("a" * 64, (resource("one"), resource("two")))
        entered = Event()
        paths = []
        def download(item, path, cancel, progress):
            paths.append(path)
            entered.set()
            cancel.wait(5)
            raise DownloadCancelled()
        importer = Mock()
        service = StarterResourceService(importer)
        with patch("verdflow.system_host.starter_resources.load_manifest", return_value=manifest), patch("verdflow.system_host.starter_resources.download_package", download):
            service.manifest()
            operation = service.start(manifest.revision)
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ValueError):
                service.start(manifest.revision)
            with self.assertRaises(ValueError):
                service.cancel("unrelated-operation")
            service.close()
        self.assertEqual(service.get(operation.operation_id).status, "cancelled")
        self.assertTrue(all(not path.parent.exists() for path in paths))
        importer.assert_not_called()

    def test_real_import_is_idempotent_keeps_trust_and_never_overwrites_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            strategy = inspect_strategy_package(ROOT / "strategy-packs/universal-natural-grove")
            strategy_path = root / "strategy.vfs"
            write_strategy_package_archive(strategy, strategy_path)
            catalog = load_catalog_yaml((ROOT / "asset-packs/dts-default/catalog.yaml").read_text(encoding="utf-8"))
            asset_path = root / "asset.vfa"
            with SystemHost(SystemHostConfig(library_path=root / "library", system_path=root / "system")) as host:
                host.save_asset_catalog(AssetCatalogSaveRequest(document=catalog, expected_generation=0))
                host.export_asset_catalog_package("dts", catalog["catalog_id"], asset_path)
                host.delete_asset_catalog("dts", catalog["catalog_id"])
                strategy_item = resource("strategy", strategy_path.read_bytes(), identity=strategy.package_id, version=strategy.package_version)
                asset_item = resource("asset", asset_path.read_bytes(), kind="assets", identity=catalog["catalog_id"])
                for item, path in [(strategy_item, strategy_path), (asset_item, asset_path)]:
                    self.assertFalse(host._import_starter_resource(item, path))
                    self.assertTrue(host._import_starter_resource(item, path))
                    with self.assertRaises(ValueError):
                        host._import_starter_resource(replace(item, identity="wrong"), path)
                self.assertNotEqual(host.list_strategies()[0].trust_state, "trusted")
                host.trust_strategy(strategy.package_hash)
                host._import_starter_resource(strategy_item, strategy_path)
                self.assertEqual(host.list_strategies()[0].trust_state, "trusted")
                catalog["display_name"] = "Local edit"
                host.save_asset_catalog(AssetCatalogSaveRequest(document=catalog, expected_generation=1))
                with self.assertRaisesRegex(ValueError, "未覆盖"):
                    host._import_starter_resource(asset_item, asset_path)
                self.assertEqual(host.list_asset_catalogs("dts")[0].display_name, "Local edit")

    def test_http_management_auth_and_async_start(self):
        manifest = StarterManifest("a" * 64, (resource(),))
        entered = Event()
        def download(item, path, cancel, progress):
            entered.set()
            cancel.wait(5)
            raise DownloadCancelled()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(SystemHostConfig(library_path=root / "library", system_path=root / "system")) as host:
                with LocalApiServer(host, LocalApiConfig(management_token="x" * 32)) as server:
                    def request(path, method="GET", body=None, auth=True):
                        headers = {"Content-Type": "application/json"}
                        if auth:
                            headers["Authorization"] = "Bearer " + "x" * 32
                        data = json.dumps(body).encode() if body is not None else None
                        with urlopen(Request(server.base_url + path, data=data, headers=headers, method=method), timeout=3) as response:
                            return json.load(response)
                    with patch("verdflow.system_host.starter_resources.load_manifest", return_value=manifest) as load, patch("verdflow.system_host.starter_resources.download_package", download):
                        with self.assertRaises(HTTPError) as caught:
                            request("/api/v1/starter-resources", auth=False)
                        self.assertEqual(caught.exception.code, 403)
                        load.assert_not_called()
                        request("/api/v1/starter-resources")
                        result = request("/api/v1/starter-resources/import", "POST", {"revision": manifest.revision})
                        self.assertTrue(entered.wait(2))
                        self.assertEqual(request("/api/v1/health")["state"], "running")
                        operation_id = result["operation_id"]
                        self.assertEqual(request(f"/api/v1/starter-resources/import?operation_id={operation_id}")["status"], "running")
                        request("/api/v1/starter-resources/import", "DELETE", {"operation_id": operation_id})
                        finish(host._starter_resources, host.get_starter_import(operation_id))


if __name__ == "__main__":
    unittest.main()
