from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
]

from verdflow.contracts import RoleBindingProfileDocument
from verdflow.profile_package import (
    ProfilePackageError,
    inspect_role_binding_profile_package,
    write_role_binding_profile_package,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _document() -> RoleBindingProfileDocument:
    value = {
        "schema": "verdflow.role-binding-profile",
        "version": "2",
        "profile_id": "profile_grove_dts",
        "display_name": "通用自然聚落 DTS",
        "strategy": {
            "strategy_id": "universal_natural_grove",
            "strategy_version": "2.0.0",
        },
        "target": {
            "target_type": "dts",
            "provider_id": "dts.static-mesh",
        },
        "catalog_hint": {"catalog_id": "dts_default"},
        "assignments": {
            "low_canopy_filler": {
                "candidates": [
                    {
                        "asset_id": "SM_tree01_01",
                        "display_name_snapshot": "Tree 01 01",
                        "target_asset_ref": {
                            "target_type": "dts",
                            "provider_id": "dts.static-mesh",
                            "locator": {"model_name": "SM_tree01_01"},
                        },
                        "weight": 1,
                        "scale": {"mode": "fixed", "value": 1},
                        "reference_radius_m": 1.5,
                        "reference_height_m": 4.2,
                    }
                ]
            }
        },
    }
    payload = _canonical(value)
    return RoleBindingProfileDocument(
        profile_id="profile_grove_dts",
        display_name="通用自然聚落 DTS",
        strategy_id="universal_natural_grove",
        strategy_version="2.0.0",
        target_type="dts",
        provider_id="dts.static-mesh",
        catalog_id="dts_default",
        semantic_hash=_hash(payload),
        canonical_json=payload,
    )


class ProfilePackageTests(unittest.TestCase):
    def test_package_is_deterministic_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.vfp"
            second = root / "second.vfp"
            package_hash = "sha256:" + "1" * 64

            one = write_role_binding_profile_package(
                _document(),
                strategy_package_hash=package_hash,
                destination=first,
            )
            two = write_role_binding_profile_package(
                _document(),
                strategy_package_hash=package_hash,
                destination=second,
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(one.package_hash, two.package_hash)
            inspection = inspect_role_binding_profile_package(first)
            self.assertEqual(inspection.profile, _document())
            self.assertEqual(inspection.strategy.package_hash, package_hash)

    def test_rejects_unexpected_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "profile.vfp"
            write_role_binding_profile_package(
                _document(),
                strategy_package_hash="sha256:" + "1" * 64,
                destination=source,
            )
            rewritten = root / "unexpected.vfp"
            with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
                rewritten,
                "w",
            ) as changed:
                for item in original.infolist():
                    changed.writestr(item.filename, original.read(item.filename))
                changed.writestr("unexpected.txt", b"nope")

            with self.assertRaisesRegex(ProfilePackageError, "entries"):
                inspect_role_binding_profile_package(rewritten)

    def test_rejects_tampered_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "profile.vfp"
            write_role_binding_profile_package(
                _document(),
                strategy_package_hash="sha256:" + "1" * 64,
                destination=source,
            )
            rewritten = root / "tampered.vfp"
            with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
                rewritten,
                "w",
            ) as changed:
                for item in original.infolist():
                    payload = original.read(item.filename)
                    if item.filename == "profile.json":
                        value = json.loads(payload)
                        value["display_name"] = "Changed"
                        payload = _canonical(value)
                    changed.writestr(item.filename, payload)

            with self.assertRaisesRegex(ProfilePackageError, "metadata"):
                inspect_role_binding_profile_package(rewritten)


if __name__ == "__main__":
    unittest.main()
