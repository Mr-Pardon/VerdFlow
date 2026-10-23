"""Validate and stage a complete portable application, without loading Core."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import zipfile


MAX_FILES = 20_000
MAX_BYTES = 4 * 1024**3


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid archive path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value:
        raise ValueError("Non-canonical archive path")
    for part in path.parts:
        if (
            part in {".", ".."} or part.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        ):
            raise ValueError("Unsafe archive path")
    return path


def stage_update(plan: dict) -> None:
    token = plan["id"]
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Invalid update identity")
    install = Path(plan["installRoot"]).resolve(strict=True)
    stage = Path(plan["stageRoot"])
    expected = install.parent / f".verdflow-update-{token}"
    if not install.name or not install.is_dir() or stage != expected or stage.exists() or stage.is_symlink():
        raise ValueError("Invalid staging directory")
    source = Path(plan["archive"])
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != plan["sha256"]:
            raise ValueError("Update archive checksum mismatch")
    created = False
    try:
        with zipfile.ZipFile(source) as archive:
            members = [entry for entry in archive.infolist() if not entry.is_dir()]
            if not members or len(members) > MAX_FILES or sum(e.file_size for e in members) > MAX_BYTES:
                raise ValueError("Update archive exceeds its limits")
            paths: dict[str, zipfile.ZipInfo] = {}
            seen: set[str] = set()
            for entry in archive.infolist():
                name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                safe_relative(name)
                if name.casefold() in seen or (entry.external_attr >> 16 & 0o170000) == 0o120000:
                    raise ValueError("Duplicate or linked archive entry")
                seen.add(name.casefold())
                if not entry.is_dir():
                    paths[name] = entry
            prefix = plan["rootName"] + "/"
            safe_relative(plan["rootName"])
            manifest_name = prefix + "update-manifest.json"
            manifest_entry = paths.get(manifest_name)
            if manifest_entry is None or manifest_entry.file_size > 8 * 1024**2:
                raise ValueError("Update manifest is missing or oversized")
            manifest = json.loads(archive.read(manifest_entry))
            if (
                manifest.get("schema") != "verdflow.portable-files"
                or manifest.get("version") != "1"
                or manifest.get("product_version") != plan["targetVersion"]
                or manifest.get("platform") != "windows-x64"
                or not isinstance(manifest.get("files"), list)
            ):
                raise ValueError("Update manifest identity mismatch")
            files: dict[str, dict] = {}
            for item in manifest["files"]:
                name = str(safe_relative(item["path"]))
                if (
                    name in files or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                    or type(item["bytes"]) is not int or item["bytes"] < 0
                ):
                    raise ValueError("Invalid update file entry")
                files[name] = item
            required = {
                "VerdFlow Studio.exe", "resources/python/python.exe",
                "resources/studio/index.html", "resources/app/package.json",
            }
            if not required <= files.keys() or set(paths) != {prefix + name for name in files} | {manifest_name}:
                raise ValueError("Update file inventory mismatch")
            stage.mkdir()
            created = True
            for name, item in files.items():
                entry = paths[prefix + name]
                if entry.file_size != item["bytes"]:
                    raise ValueError("Update file size mismatch")
                destination = stage.joinpath(*safe_relative(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(entry) as incoming, destination.open("xb") as outgoing:
                    while chunk := incoming.read(1024**2):
                        digest.update(chunk)
                        outgoing.write(chunk)
                if digest.hexdigest() != item["sha256"]:
                    raise ValueError("Update file checksum mismatch")
            (stage / ".verdflow-update-ready.json").write_text(
                json.dumps({"id": token, "version": plan["targetVersion"]}), encoding="utf-8",
            )
    except BaseException:
        if created and stage.resolve() == expected and not stage.is_symlink():
            shutil.rmtree(stage)
        raise


if __name__ == "__main__":
    try:
        stage_update(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
        print("portable-update-ready", flush=True)
    except Exception as error:
        print(f"Update preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
