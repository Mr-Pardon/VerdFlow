from pathlib import Path
import copy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(path) for parent in (ROOT / "packages", ROOT / "apps", ROOT / "adapters") for path in parent.glob("*/src")]

from verdflow.library_store import LibraryStore
from verdflow.planting import prepare_asset_catalog_document
from verdflow.system_host import AssetSearchRequest, SystemHost, SystemHostConfig, parse_asset_search_request
from verdflow.system_host.asset_search import AssetSearchService


def catalog(key, count=6, provider="dts.static-mesh"):
    return {"schema": "verdflow.target-asset-catalog", "version": "1", "catalog_id": key,
            "target_type": "dts", "provider_id": provider, "display_name": "Library " + key,
            "assets": [{"asset_id": "tree_" + str(i), "display_name": "Tree " + str(i), "tags": ["green"],
                        "provider_metadata": {"model_name": key + str(i)}} for i in range(count)],
            "collections": [{"collection_id": "trees", "display_name": "Trees", "asset_ids": ["tree_0"]}]}


class AssetSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LibraryStore.open(Path(self.temporary.name) / "library")
        self.addCleanup(self.store.close)
        for key in ("a", "b"):
            self.store.save_asset_catalog(prepare_asset_catalog_document(catalog(key)), expected_generation=0)
        self.service = AssetSearchService()

    def request(self, **values):
        return AssetSearchRequest(**{"target_type": "dts", "provider_id": "dts.static-mesh", "catalog_ids": ("b", "a"), **values})

    def test_mixed_scope_identity_order_and_source_categories(self):
        page = self.service.search(self.store, self.request(limit=7))
        self.assertEqual((page.total_count, len(page.items), page.next_offset), (12, 7, 7))
        self.assertEqual(page.items[0].source_catalog_name, "Library a")
        self.assertNotEqual(page.items[0].asset_id, page.items[6].asset_id)
        self.assertEqual(page.items[0].binding_candidate["catalog_link"]["asset_id"], "tree_0")
        self.assertEqual(len(page.collections), 2)
        next_page = self.service.search(self.store, self.request(offset=7, limit=7, revision=page.revision))
        self.assertEqual(len(next_page.items), 5)
        self.assertIsNone(next_page.next_offset)
        self.assertFalse(set(item.asset_id for item in page.items) & set(item.asset_id for item in next_page.items))

    def test_search_and_category_are_across_scope_and_source_local(self):
        self.assertEqual(self.service.search(self.store, self.request(query="TREE_0")).total_count, 2)
        self.assertEqual(self.service.search(self.store, self.request(query="GREEN")).total_count, 12)
        page = self.service.search(self.store, self.request(collection_catalog_id="b", collection_id="trees"))
        self.assertEqual(page.total_count, 1)
        self.assertEqual(page.items[0].source_catalog_name, "Library b")
        self.assertEqual(self.service.search(self.store, self.request(catalog_ids=())).total_count, 0)

    def test_updated_catalog_invalidates_cache_and_page_revision(self):
        old = self.service.search(self.store, self.request(limit=5))
        doc = catalog("a")
        doc["assets"][0]["display_name"] = "Renamed"
        self.store.save_asset_catalog(prepare_asset_catalog_document(doc), expected_generation=1)
        current = self.service.search(self.store, self.request(offset=5, limit=5, revision=old.revision))
        self.assertEqual(current.offset, 0)
        self.assertNotEqual(old.revision, current.revision)
        self.assertEqual(current.items[0].display_name, "Renamed")
        self.assertEqual(len(self.service._cache), 2)
        self.service.clear()
        self.assertFalse(self.service._cache)

    def test_missing_and_unreadable_catalogs_are_reported_without_losing_healthy_results(self):
        page = self.service.search(self.store, self.request(catalog_ids=("a", "missing")))
        self.assertEqual(page.total_count, 6)
        self.assertEqual(page.unavailable_catalogs[0].catalog_id, "missing")
        original = self.service._index
        def broken(store, info):
            if info.catalog_id == "b":
                raise OSError("private local path")
            return original(store, info)
        with patch.object(self.service, "_index", side_effect=broken):
            page = self.service.search(self.store, self.request())
        self.assertEqual(page.total_count, 6)
        self.assertEqual(page.unavailable_catalogs[0].catalog_id, "b")
        self.assertNotIn("private", page.unavailable_catalogs[0].message)

    def test_invalid_scope_and_request_are_rejected(self):
        self.store.save_asset_catalog(prepare_asset_catalog_document(catalog("other", provider="another.provider")), expected_generation=0)
        with self.assertRaises(ValueError):
            self.service.search(self.store, self.request(catalog_ids=("a", "other")))
        document = {"target_type": "dts", "provider_id": "dts.static-mesh", "catalog_ids": ["a"]}
        for patch_value in ({"limit": 201}, {"offset": -1}, {"offset": True}, {"catalog_ids": "a"}, {"collection_id": "trees"}, {"query": 10}):
            with self.subTest(patch_value=patch_value), self.assertRaises(ValueError):
                parse_asset_search_request({**document, **patch_value})

    def test_search_includes_assets_past_old_thousand_item_limit(self):
        self.store.save_asset_catalog(prepare_asset_catalog_document(catalog("large", 1205)), expected_generation=0)
        page = self.service.search(self.store, self.request(catalog_ids=("large",), query="tree_1204"))
        self.assertEqual(page.total_count, 1)
        self.assertEqual(page.items[0].display_name, "Tree 1204")


if __name__ == "__main__":
    unittest.main()
