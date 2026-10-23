from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
]

from verdflow.asset_package import (  # noqa: E402
    AssetPackageError,
    AssetPackageResource,
    read_asset_catalog_package,
    write_asset_catalog_package,
)
from verdflow.contracts import library_resource_reference  # noqa: E402
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    load_catalog_yaml,
    prepare_asset_catalog_document,
)


ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


def _catalog() -> dict:
    return copy.deepcopy(load_catalog_yaml(
        (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    ))


class AssetPackageTests(unittest.TestCase):
    def test_released_catalog_and_thumbnail_round_trip_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source-library"
            first_path = root / "first.vfa"
            second_path = root / "second.vfa"
            thumbnail = b"RIFF\x08\x00\x00\x00WEBPVP8 "
            with LibraryStore.open(source_root) as source:
                resource = source.import_resource_bytes(
                    thumbnail,
                    media_type="image/webp",
                )
                catalog = _catalog()
                catalog["assets"][0]["thumbnail"] = library_resource_reference(
                    resource.resource_id
                )
                catalog["assets"][0]["reference_radius_m"] = 1.5
                catalog["assets"][0]["reference_height_m"] = 4.2
                document = prepare_asset_catalog_document(catalog)
                first = write_asset_catalog_package(
                    document,
                    (
                        AssetPackageResource(
                            resource_id=resource.resource_id,
                            content_hash=resource.content_hash,
                            media_type=resource.media_type,
                            payload=thumbnail,
                        ),
                    ),
                    first_path,
                )
                second = write_asset_catalog_package(
                    document,
                    (
                        AssetPackageResource(
                            resource_id=resource.resource_id,
                            content_hash=resource.content_hash,
                            media_type=resource.media_type,
                            payload=thumbnail,
                        ),
                    ),
                    second_path,
                )
            self.assertEqual(first.package_hash, second.package_hash)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            loaded_resources: list[AssetPackageResource] = []
            inspected = read_asset_catalog_package(
                first_path,
                on_resource=loaded_resources.append,
            )
            self.assertEqual(inspected.catalog, document)
            self.assertEqual(loaded_resources[0].payload, thumbnail)

    def test_legacy_catalog_revision_is_rejected(self) -> None:
        catalog = _catalog()
        catalog["catalog_revision"] = "r1"
        with self.assertRaisesRegex(Exception, "catalog_revision"):
            prepare_asset_catalog_document(catalog)

    def test_import_rejects_unexpected_archive_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "catalog.vfa"
            catalog = _catalog()
            write_asset_catalog_package(
                prepare_asset_catalog_document(catalog),
                (),
                package,
            )
            with zipfile.ZipFile(package, mode="a") as archive:
                archive.writestr("unexpected.txt", b"not declared")
            with self.assertRaisesRegex(
                AssetPackageError,
                "entries do not match",
            ):
                read_asset_catalog_package(package)

    def test_import_rejects_resource_content_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "catalog.vfa"
            tampered = root / "tampered.vfa"
            with LibraryStore.open(root / "source") as source:
                resource = source.import_resource_bytes(
                    b"thumbnail-content",
                    media_type="image/webp",
                )
                catalog = _catalog()
                catalog["assets"][0]["thumbnail"] = library_resource_reference(
                    resource.resource_id
                )
                write_asset_catalog_package(
                    prepare_asset_catalog_document(catalog),
                    (
                        AssetPackageResource(
                            resource_id=resource.resource_id,
                            content_hash=resource.content_hash,
                            media_type=resource.media_type,
                            payload=b"thumbnail-content",
                        ),
                    ),
                    original,
                )
            with zipfile.ZipFile(original) as source_zip, zipfile.ZipFile(
                tampered,
                mode="w",
            ) as target_zip:
                for info in source_zip.infolist():
                    payload = source_zip.read(info)
                    if info.filename.startswith("resources/"):
                        payload = b"tampered-content"
                    target_zip.writestr(info, payload)
            with self.assertRaisesRegex(
                AssetPackageError,
                "does not match metadata",
            ):
                read_asset_catalog_package(tampered)


if __name__ == "__main__":
    unittest.main()
