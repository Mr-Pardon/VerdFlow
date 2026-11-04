from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(p) for parent in ("packages", "apps", "adapters") for p in (ROOT / parent).glob("*/src")]

from verdflow.local_api import LocalApiConfig, LocalApiServer
from verdflow.system_host import SystemHost, SystemHostConfig
from verdflow.system_host.resource_center import (
    BASE_URL, ResourceCenterService, ResourceInventory, InstalledStrategy, content_url,
    find_strategy_updates, local_state, parse_catalog,
)
from verdflow.system_host.resource_index import IndexedResource, ResourceIndex
from test_starter_resources import resource


def record():
    return {"id": "sample", "type": "strategies", "name": "Sample", "summary": "Description", "author": "Author",
            "platforms": ["DTS"], "tags": ["trees"], "version": "1.2.0", "status": "published", "updatedAt": "2026-11-03",
            "cover": "data/sample/" + "a" * 20 + "/media/cover.png", "detail": "data/sample/" + "a" * 20 + "/resource.json"}


def catalog_bytes(records=None):
    return json.dumps({"schemaVersion": 1, "resources": records if records is not None else [record()]}).encode()


class ResourceCenterTests(unittest.TestCase):
    def setUp(self):
        self.item = IndexedResource(resource(version="1.2.0"), strategy_hash="sha256:" + "b" * 64)
        self.index = ResourceIndex("c" * 64, (self.item,))
        self.inventory = ResourceInventory((), (), ())
        self.service = ResourceCenterService(lambda: self.inventory)
        self.index_patch = patch("verdflow.system_host.resource_center.load_index", return_value=self.index)
        self.load_index = self.index_patch.start()
        self.addCleanup(self.index_patch.stop)

    def document(self, **changes):
        r = self.item.resource
        return {"schemaVersion": 1, "id": "sample", "type": "strategies", "license": "Apache-2.0", "versions": [{
            "version": r.version, "releasedAt": "2026-11-03", "readme": "README.md", "notes": "Updated",
            "status": "published", "package": {"identity": r.identity, "sha256": r.sha256, "bytes": r.bytes},
            "relations": [{"kind": "pak", "name": "Models", "url": "javascript:alert(1)"}], **changes}]}

    def content(self, path):
        if path == "catalog.json":
            return catalog_bytes()
        if path.endswith("resource.json"):
            return json.dumps(self.document()).encode()
        return b"# Sample\n\nContent"

    def test_content_urls_are_confined_to_revisioned_static_documents(self):
        self.assertTrue(content_url(record()["cover"]).startswith(BASE_URL))
        for path in ("https://evil.test/a.md", "../catalog.json", "data/sample/" + "a" * 20 + "/../x.md",
                     record()["cover"] + "?url=x", record()["cover"].replace(".png", ".svg"), "//127.0.0.1/test.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                content_url(path)

    def test_catalog_rejects_identity_cover_and_count_errors(self):
        self.assertEqual(parse_catalog(catalog_bytes())[0].name, "Sample")
        for records in ([record(), record()], [{**record(), "detail": record()["detail"].replace("sample/", "other/")}],
                        [{**record(), "cover": "https://evil.test/cover.png"}], [record()] * 1025, [None]):
            with self.subTest(records=len(records)), self.assertRaises(ValueError):
                parse_catalog(catalog_bytes(records))

    def test_exact_local_status_never_invents_catalog_distribution_versions(self):
        self.inventory = ResourceInventory((InstalledStrategy("sample", "1.2.0", self.item.strategy_hash),), (("dts", "sample"),), ())
        self.assertEqual(local_state(self.item, self.inventory), "installed")
        self.assertEqual(local_state(replace(self.item, strategy_hash="sha256:" + "e" * 64), self.inventory), "conflict")
        asset = replace(self.item, resource=replace(self.item.resource, kind="assets"), target_type="dts")
        self.assertEqual(local_state(asset, self.inventory), "local")

    def test_updates_compare_stable_semver_and_skip_downgrades_or_same_version(self):
        self.inventory = ResourceInventory((InstalledStrategy("sample", "1.9.0", "old"),), (), ())
        candidates = tuple(replace(self.item, resource=replace(self.item.resource, version=v)) for v in ("1.2.0", "1.10.0", "2.0.0-beta.1"))
        updates = find_strategy_updates(ResourceIndex("c" * 64, candidates), self.inventory)
        self.assertEqual(updates[0].version, "1.10.0")
        self.assertFalse(updates[0].major_change)
        stable = replace(self.item, resource=replace(self.item.resource, version="2.0.0"))
        self.assertTrue(find_strategy_updates(ResourceIndex("c" * 64, (stable,)), self.inventory)[0].major_change)
        self.assertFalse(find_strategy_updates(self.index, self.inventory))

    def test_cache_refresh_and_local_state_are_independent(self):
        with patch("verdflow.system_host.resource_center.load_content", side_effect=self.content) as load:
            self.assertFalse(self.service.catalog().stale)
            self.inventory = ResourceInventory((InstalledStrategy("sample", "1.2.0", self.item.strategy_hash),), (), ())
            self.assertEqual(self.service.catalog().resources[0].local_state, "installed")
            self.assertEqual(load.call_count, 1)
            self.load_index.assert_called_once()
            load.side_effect = OSError("offline")
            self.assertTrue(self.service.catalog(True).stale)

    def test_details_intersect_reviewed_index_and_sanitize_external_links(self):
        with patch("verdflow.system_host.resource_center.load_content", side_effect=self.content):
            detail = self.service.detail("sample")
        self.assertTrue(detail.versions[0].installable)
        self.assertEqual(detail.versions[0].relations[0].url, "")
        self.assertEqual(detail.readme, "# Sample\n\nContent")
        self.assertEqual(detail.revision, self.index.revision)
        self.service._documents.clear()
        def mismatch(path):
            if path.endswith("resource.json"):
                return json.dumps(self.document(package={"identity": "different", "sha256": self.item.resource.sha256, "bytes": 7})).encode()
            return self.content(path)
        with patch("verdflow.system_host.resource_center.load_content", side_effect=mismatch):
            self.assertFalse(self.service.detail("sample").versions[0].installable)

    def test_offline_start_reports_semantic_error_and_no_stale_update_claim(self):
        self.load_index.side_effect = OSError("offline")
        with patch("verdflow.system_host.resource_center.load_content", side_effect=OSError("offline")):
            with self.assertRaisesRegex(ValueError, "本地资源仍可正常使用"):
                self.service.catalog()
        self.assertTrue(self.service.updates().warning)

    def test_details_reject_malformed_and_duplicate_versions_semantically(self):
        duplicate = self.document()
        duplicate["versions"] *= 2
        for document in ([], self.document(package=[]), self.document(relations=[None]), duplicate):
            self.service._documents.clear()
            def malformed(path):
                return json.dumps(document).encode() if path.endswith("resource.json") else self.content(path)
            with self.subTest(document=document), patch("verdflow.system_host.resource_center.load_content", side_effect=malformed):
                with self.assertRaises(ValueError):
                    self.service.detail("sample")

    def test_all_discovery_routes_require_auth_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(SystemHostConfig(library_path=root / "library", system_path=root / "system")) as host:
                with LocalApiServer(host, LocalApiConfig(management_token="x" * 32)) as server:
                    with patch("verdflow.system_host.resource_center.load_content", side_effect=self.content) as load:
                        for path in ("", "/detail?id=sample", "/strategy-updates"):
                            with self.assertRaises(HTTPError) as error:
                                urlopen(server.base_url + "/api/v1/resource-center" + path)
                            self.assertEqual(error.exception.code, 403)
                        load.assert_not_called()
                        with urlopen(Request(server.base_url + "/api/v1/resource-center", headers={"Authorization": "Bearer " + "x" * 32})) as response:
                            result = json.load(response)
                        self.assertEqual(result["resources"][0]["name"], "Sample")


if __name__ == "__main__":
    unittest.main()
