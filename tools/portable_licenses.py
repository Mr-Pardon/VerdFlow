"""Preserve upstream runtime notices and inventory a staged portable bundle."""

from __future__ import annotations

import csv
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import re
import shutil


PROJECT_FILES = ("LICENSE", "NOTICE", "AUTHORS.md", "THIRD_PARTY_NOTICES.md")
NOTICE_NAME = re.compile(r"^(licen[sc]e|copying|copyright|notice|authors)([._-]|$)", re.I)


def _required(path: Path, boundary: Path) -> Path:
    if (path.is_symlink() or not path.resolve().is_relative_to(boundary.resolve())
            or not path.is_file() or not path.stat().st_size):
        raise ValueError(f"Missing or unsafe license material: {path.name}")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wheel_notices(site: Path, distribution: Path) -> list[Path]:
    # RECORD also identifies native-library notices stored outside dist-info.
    record = _required(distribution / "RECORD", site)
    notices = set()
    with record.open(encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream):
            if not row:
                continue
            relative = Path(row[0])
            if "licenses" in relative.parts or NOTICE_NAME.match(relative.name):
                notices.add(_required(site / relative, site))
    if not notices:
        raise ValueError(f"No license material in {distribution.name}")
    return sorted(notices)


def stage_licenses(workspace: Path, bundle: Path, *, python_version: str) -> dict:
    """Use root notices in a source export, or the explicit local publication overlay."""
    legal = workspace if (workspace / "LICENSE").is_file() else workspace / "publishing" / "public"
    for name in PROJECT_FILES:
        _required(legal / name, legal)
    overrides_path = legal / "third-party/license-overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.is_file() else {}

    third_party = bundle / "third-party"
    third_party.mkdir(parents=True, exist_ok=True)
    # Electron's root LICENSE must survive before VerdFlow's root LICENSE is installed.
    electron_license = third_party / "electron-LICENSE.txt"
    if electron_license.exists():
        raise ValueError("License staging requires a fresh Electron bundle")
    _required(bundle / "LICENSE", bundle)
    shutil.copyfile(bundle / "LICENSE", electron_license)
    _required(bundle / "LICENSES.chromium.html", bundle)
    for name in PROJECT_FILES:
        shutil.copyfile(legal / name, bundle / name)
    shutil.copyfile(_required(legal / "third-party/runtime-ui.txt", legal), third_party / "runtime-ui.txt")

    components = []
    electron_version = _required(bundle / "version", bundle).read_text(encoding="utf-8").strip()
    components.append({"name": "Electron / Chromium", "version": electron_version,
                       "license_files": ["third-party/electron-LICENSE.txt", "LICENSES.chromium.html"]})
    python_notice = _required(bundle / "resources/python/LICENSE.txt", bundle)
    components.append({"name": "CPython", "version": python_version,
                       "license_files": [python_notice.relative_to(bundle).as_posix()]})

    # Read the installed runtime graph, not every development tool in node_modules.
    visited = set()
    for app in ("studio", "dts-live-webui"):
        app_root = workspace / "apps" / app
        manifest = json.loads((app_root / "package.json").read_text(encoding="utf-8"))
        pending = [(app_root, name) for name in manifest.get("dependencies", {})]
        while pending:
            parent, name = pending.pop()
            current = parent
            while not (current / "node_modules" / name / "package.json").is_file():
                if current == workspace or not current.is_relative_to(workspace):
                    raise ValueError(f"Runtime dependency is not installed: {name}")
                current = current.parent
            package = (current / "node_modules" / name).resolve()
            if not package.is_relative_to(workspace.resolve()):
                raise ValueError(f"Runtime dependency escapes the workspace: {name}")
            if package in visited:
                continue
            visited.add(package)
            document = json.loads((package / "package.json").read_text(encoding="utf-8"))
            pending.extend((package, child) for child in document.get("dependencies", {}))
            # Workspace UI code has the project license, not a separate upstream license.
            if name.startswith("@verdflow/"):
                continue
            version = document["version"]
            identity = re.sub(r"[^A-Za-z0-9._-]", "_", name + "-" + version)
            originals = sorted(file for file in package.iterdir() if NOTICE_NAME.match(file.name) and file.is_file())
            original_root = package
            override = None
            if not originals and (override := overrides.get(name + "@" + version)):
                original = _required(legal / override["path"], legal)
                if _digest(original) != override["sha256"]:
                    raise ValueError(f"Reviewed license changed: {name}")
                originals = [original]
                original_root = legal
            if not originals:
                raise ValueError(f"No license material for JavaScript runtime dependency: {name}")
            paths = []
            for original in originals:
                _required(original, original_root)
                target = third_party / "javascript" / identity / original.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                paths.append(target.relative_to(bundle).as_posix())
            component = {"name": name, "version": version, "license": document.get("license"),
                         "license_files": paths}
            if override:
                component["license_source"] = override["source"]
            components.append(component)

    site = bundle / "resources/python/Lib/site-packages"
    distributions = sorted(site.glob("*.dist-info"))
    if not distributions:
        raise ValueError("No Python runtime dependency metadata")
    for distribution in distributions:
        metadata = Parser().parsestr(_required(distribution / "METADATA", site).read_text(encoding="utf-8"))
        notices = _wheel_notices(site, distribution)
        components.append({
            "name": metadata["Name"], "version": metadata["Version"],
            "license": metadata.get("License-Expression") or metadata.get("License"),
            "license_files": [path.relative_to(bundle).as_posix() for path in notices],
        })

    components.sort(key=lambda item: (item["name"].lower(), item["version"]))
    files = sorted({name for component in components for name in component["license_files"]}
                   | set(PROJECT_FILES) | {"third-party/runtime-ui.txt"})
    manifest = {
        "schema": "verdflow.portable-licenses", "version": "1",
        "components": components,
        "files": {name: _digest(bundle / name) for name in files},
    }
    (third_party / "licenses.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Bundled Runtime Notices", "",
             "VerdFlow original code: Apache-2.0; see ../LICENSE and ../NOTICE.",
             "Upstream notices are retained without relicensing. Versions below describe this bundle.",
             "The JavaScript inventory is conservative: it includes the runtime dependency graph,",
             "including helpers that may be removed by the frontend bundler.",
             "Native libraries can have additional terms in the linked upstream notices.",
             "This index is not a legal clearance or a grant for external asset packages.", ""]
    for item in components:
        lines.append(f"## {item['name']} {item['version']}")
        lines.extend(f"- [{path}](../{path})" for path in item["license_files"])
        lines.append("")
    (third_party / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def verify_licenses(bundle: Path) -> None:
    manifest = json.loads((bundle / "third-party/licenses.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "verdflow.portable-licenses" or not manifest.get("components"):
        raise ValueError("Invalid portable license inventory")
    for name in PROJECT_FILES:
        if name not in manifest["files"]:
            raise ValueError(f"Missing project notice: {name}")
    for component in manifest["components"]:
        if not component["license_files"] or any(name not in manifest["files"] for name in component["license_files"]):
            raise ValueError("Uninventoried component license")
    for name, digest in manifest["files"].items():
        if _digest(_required(bundle / name, bundle)) != digest:
            raise ValueError(f"License material changed: {name}")
