"""Produce reviewed discovery facts from real VFA/VFS/VFP bytes, without executing code."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(path) for parent in ("packages", "apps", "adapters") for path in (ROOT / parent).glob("*/src")]

from verdflow.asset_package import inspect_asset_catalog_package
from verdflow.planting import prepare_role_binding_profile_document
from verdflow.profile_package import inspect_role_binding_profile_package
from verdflow.strategy_worker import open_strategy_package_archive
from verdflow.system_host.resource_downloads import download_package, parse_resource
from verdflow.system_host.resource_index import plan_metadata, parse_index


def inspect_resource(record: dict, release: dict, path: Path) -> dict:
    item = {"id": record["id"], "name": record["name"], "kind": record["type"], "version": release["version"],
            **release["package"], "page_url": f"https://mr-pardon.github.io/verdflow-resources/?type={record['type']}&id={record['id']}&version={release['version']}"}
    resource = parse_resource(item, allow_plans=True)
    if path.stat().st_size != resource.bytes or hashlib.sha256(path.read_bytes()).hexdigest() != resource.sha256:
        raise ValueError("Attachment bytes do not match the reviewed release")
    result = {**item, "status": release["status"]}
    if resource.kind == "assets":
        inspected = inspect_asset_catalog_package(path)
        if inspected.catalog.catalog_id != resource.identity:
            raise ValueError("Catalog identity mismatch")
        result["target_type"] = inspected.catalog.target_type
    elif resource.kind == "strategies":
        with open_strategy_package_archive(path) as opened:
            if (opened.package.package_id, opened.package.package_version) != (resource.identity, resource.version):
                raise ValueError("Strategy identity/version mismatch")
            result["strategy_hash"] = opened.package.package_hash
    else:
        inspected = inspect_role_binding_profile_package(path)
        validated = prepare_role_binding_profile_document(json.loads(inspected.profile.canonical_json))
        if validated.profile_id != resource.identity or validated.semantic_hash != inspected.profile.semantic_hash:
            raise ValueError("Plan identity/content mismatch")
        result["plan"] = asdict(plan_metadata(inspected))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources-root", type=Path, required=True)
    parser.add_argument("--packages-root", type=Path, help="Use local release attachments instead of downloading")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = []
    for entry in sorted((args.resources_root / "entries").glob("*/*/resource.json")):
        record = json.loads(entry.read_text(encoding="utf-8"))
        for release in record["versions"]:
            if release["status"] != "published":
                continue
            if record["type"] != "strategies" and release["version"] != record["recommended"]:
                continue
            with TemporaryDirectory(prefix="verdflow-resource-inspection-") as temporary:
                if args.packages_root:
                    path = args.packages_root / release["package"]["filename"]
                else:
                    path = Path(temporary) / "resource"
                    item = {"id": record["id"], "name": record["name"], "kind": record["type"], "version": release["version"],
                            **release["package"], "page_url": "https://mr-pardon.github.io/verdflow-resources/"}
                    download_package(parse_resource(item, allow_plans=True), path, Event(), lambda _: None)
                result.append(inspect_resource(record, release, path))
    revision = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    document = {"schemaVersion": 1, "revision": revision, "resources": result}
    parse_index(json.dumps(document).encode())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Inspected {len(result)} resource versions. No strategy code was executed.")


if __name__ == "__main__":
    main()
