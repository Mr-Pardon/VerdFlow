from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
]

from verdflow.blender_worker import (  # noqa: E402
    BlenderAssetLibraryRegistry,
    BlenderWorkerError,
    build_blender_asset_catalog,
)


class BlenderAssetLibraryRegistryTests(unittest.TestCase):
    def test_registry_persists_and_resolves_machine_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "vegetation.blend"
            library.write_bytes(b"synthetic blend bytes")
            registry = BlenderAssetLibraryRegistry(root / "registry")

            registered = registry.register("studio-vegetation", library)
            reopened = BlenderAssetLibraryRegistry(root / "registry")
            resolved = reopened.get("studio-vegetation")
            assert resolved is not None
            self.assertEqual(resolved, registered)

            self.assertEqual(
                reopened.resolve(registered.library_id),
                registered,
            )

    def test_library_updates_do_not_require_registry_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "vegetation.blend"
            library.write_bytes(b"first")
            registry = BlenderAssetLibraryRegistry(root / "registry")
            registered = registry.register("studio-vegetation", library)
            library.write_bytes(b"changed")

            resolved = registry.resolve(registered.library_id)
            self.assertEqual(resolved, registered)

    def test_duplicate_id_requires_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.blend"
            second = root / "second.blend"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            registry = BlenderAssetLibraryRegistry(root / "registry")
            registry.register("studio-vegetation", first)

            with self.assertRaisesRegex(
                BlenderWorkerError,
                "already registered",
            ):
                registry.register("studio-vegetation", second)
            replaced = registry.register(
                "studio-vegetation",
                second,
                replace=True,
            )
            self.assertEqual(replaced.library_path, second.resolve())


class BlenderAssetCatalogTests(unittest.TestCase):
    def test_inspection_maps_to_path_free_blender_catalog(self) -> None:
        inspection = {
            "schema": "verdflow.blender-asset-library-inspection",
            "version": "1",
            "library_id": "studio-vegetation",
            "library_content_hash": "sha256:" + "a" * 64,
            "assets": [
                {
                    "datablock_type": "COLLECTION",
                    "datablock_name": "VF_Tree",
                    "display_name": "Tree",
                    "object_count": 2,
                    "mesh_object_count": 2,
                }
            ],
        }

        catalog = build_blender_asset_catalog(
            inspection,
            catalog_id="blender_demo",
            display_name="Blender Demo",
        )

        asset = catalog["assets"][0]
        metadata = asset["provider_metadata"]
        self.assertEqual(catalog["target_type"], "blender")
        self.assertEqual(asset["asset_id"], "VF_Tree")
        self.assertEqual(metadata["library_id"], "studio-vegetation")
        self.assertEqual(metadata["datablock_type"], "COLLECTION")
        self.assertNotIn("path", json.dumps(catalog).lower())

    def test_empty_and_duplicate_collection_assets_are_rejected(self) -> None:
        inspection = {
            "schema": "verdflow.blender-asset-library-inspection",
            "version": "1",
            "library_id": "studio-vegetation",
            "library_content_hash": "sha256:" + "a" * 64,
            "assets": [],
        }
        with self.assertRaisesRegex(BlenderWorkerError, "no Collection assets"):
            build_blender_asset_catalog(
                inspection,
                catalog_id="blender_demo",
                display_name="Blender Demo",
            )

        duplicate = copy.deepcopy(inspection)
        duplicate["assets"] = [
            {
                "datablock_type": "COLLECTION",
                "datablock_name": "Same",
                "display_name": "Same",
            },
            {
                "datablock_type": "COLLECTION",
                "datablock_name": "Same",
                "display_name": "Same",
            },
        ]
        with self.assertRaisesRegex(BlenderWorkerError, "duplicated"):
            build_blender_asset_catalog(
                duplicate,
                catalog_id="blender_demo",
                display_name="Blender Demo",
            )


if __name__ == "__main__":
    unittest.main()
