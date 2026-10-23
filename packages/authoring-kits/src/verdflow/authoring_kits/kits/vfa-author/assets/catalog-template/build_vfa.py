from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import unicodedata
import zipfile


PACKAGE_SCHEMA = "verdflow.asset-catalog-package"
PACKAGE_VERSION = "1"
CATALOG_PATH = "catalog.json"
MANIFEST_PATH = "manifest.json"
MAX_RESOURCE_BYTES = 16 * 1024 * 1024
MEDIA_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build one DTS Catalog authoring document as a VerdFlow .vfa package."
    )
    parser.add_argument("source", type=Path, help="Catalog authoring JSON")
    parser.add_argument("destination", type=Path, help="Output .vfa path")
    args = parser.parse_args()
    build(args.source, args.destination)


def build(source: Path, destination: Path) -> None:
    source_path = source.resolve(strict=True)
    root = source_path.parent
    catalog = _json_object(source_path.read_bytes(), "Catalog source")
    _validate_dts_catalog(catalog)
    final_catalog = copy.deepcopy(catalog)
    resources: dict[str, tuple[str, bytes]] = {}
    for asset in final_catalog["assets"]:
        thumbnail = asset.get("thumbnail")
        if thumbnail is None:
            continue
        if not isinstance(thumbnail, str) or not thumbnail.strip():
            raise ValueError("Asset thumbnail must be a relative image path")
        relative = Path(thumbnail)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Thumbnail escapes the authoring directory: {thumbnail}")
        thumbnail_path = (root / relative).resolve(strict=True)
        try:
            thumbnail_path.relative_to(root)
        except ValueError as error:
            raise ValueError(
                f"Thumbnail escapes the authoring directory: {thumbnail}"
            ) from error
        media_type = MEDIA_TYPES.get(thumbnail_path.suffix.lower())
        if media_type is None:
            raise ValueError(f"Unsupported thumbnail type: {thumbnail_path.suffix}")
        payload = thumbnail_path.read_bytes()
        if not payload or len(payload) > MAX_RESOURCE_BYTES:
            raise ValueError(f"Thumbnail has an invalid size: {thumbnail}")
        digest = hashlib.sha256(payload).hexdigest()
        resource_id = f"res_{digest}"
        resources[resource_id] = (media_type, payload)
        asset["thumbnail"] = f"verdflow-resource:{resource_id}"

    catalog_bytes = _canonical_json_bytes(final_catalog)
    catalog_hash = f"sha256:{hashlib.sha256(catalog_bytes).hexdigest()}"
    resource_rows = []
    for resource_id in sorted(resources):
        media_type, payload = resources[resource_id]
        digest = resource_id.removeprefix("res_")
        resource_rows.append(
            {
                "resource_id": resource_id,
                "content_hash": f"sha256:{digest}",
                "media_type": media_type,
                "byte_size": len(payload),
                "path": f"resources/{resource_id}",
            }
        )
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "version": PACKAGE_VERSION,
        "catalog": {
            "target_type": final_catalog["target_type"],
            "provider_id": final_catalog["provider_id"],
            "catalog_id": final_catalog["catalog_id"],
            "semantic_hash": catalog_hash,
            "path": CATALOG_PATH,
        },
        "resources": resource_rows,
    }

    destination_path = destination.resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise ValueError(f"Destination already exists: {destination_path}")
    with zipfile.ZipFile(
        destination_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        _write_entry(archive, CATALOG_PATH, catalog_bytes)
        for row in resource_rows:
            _write_entry(
                archive,
                row["path"],
                resources[row["resource_id"]][1],
            )
        _write_entry(archive, MANIFEST_PATH, _canonical_json_bytes(manifest))
    print(destination_path)


def _validate_dts_catalog(catalog: dict[str, object]) -> None:
    if catalog.get("schema") != "verdflow.target-asset-catalog":
        raise ValueError("Catalog schema must be verdflow.target-asset-catalog")
    if catalog.get("version") != "1":
        raise ValueError("Catalog version must be 1")
    if catalog.get("target_type") != "dts":
        raise ValueError("VFA Author v1 only supports target_type dts")
    if catalog.get("provider_id") != "dts.static-mesh":
        raise ValueError("VFA Author v1 only supports provider_id dts.static-mesh")
    assets = catalog.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Catalog assets must be an array")
    seen: set[str] = set()
    for raw in assets:
        if not isinstance(raw, dict):
            raise ValueError("Catalog asset must be an object")
        asset_id = raw.get("asset_id")
        metadata = raw.get("provider_metadata")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("Catalog asset_id is required")
        if asset_id in seen:
            raise ValueError(f"Duplicate asset_id: {asset_id}")
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("model_name"), str
        ) or not metadata["model_name"].strip():
            raise ValueError(f"DTS model_name is required: {asset_id}")
        seen.add(asset_id)
        for field in ("reference_radius_m", "reference_height_m"):
            if field not in raw:
                continue
            dimension = raw[field]
            if (
                isinstance(dimension, bool)
                or not isinstance(dimension, (int, float))
                or not math.isfinite(dimension)
                or dimension <= 0
            ):
                raise ValueError(f"{field} must be finite and positive: {asset_id}")
    for collection in catalog.get("collections", []):
        if not isinstance(collection, dict) or not isinstance(
            collection.get("asset_ids"), list
        ):
            raise ValueError("Catalog collection is invalid")
        missing = sorted(set(collection["asset_ids"]) - seen)
        if missing:
            raise ValueError(f"Collection references unknown assets: {missing}")


def _write_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize(value: object) -> object:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Catalog contains a non-finite number")
        return 0.0 if value == 0.0 else value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", key): _normalize(item) for key, item in value.items()}
    raise ValueError(f"Catalog contains unsupported value: {type(value).__name__}")


def _json_object(payload: bytes, label: str) -> dict[str, object]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


if __name__ == "__main__":
    main()

