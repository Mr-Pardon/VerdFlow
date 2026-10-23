"""Machine-local Blender asset library locators."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import BlenderWorkerError


REGISTRY_SCHEMA = "verdflow.blender-asset-library-registry"
REGISTRY_VERSION = "2"
LIBRARY_ID = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class BlenderAssetLibraryRegistration:
    library_id: str
    library_path: Path


class BlenderAssetLibraryRegistry:
    """Persist provider locators outside ProjectStore."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "registry.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"schema": REGISTRY_SCHEMA, "version": REGISTRY_VERSION, "libraries": []})
        else:
            self._load()

    def register(
        self,
        library_id: str,
        library_path: Path,
        *,
        replace: bool = False,
    ) -> BlenderAssetLibraryRegistration:
        library_id = _validate_library_id(library_id)
        library_path = _validate_library_path(library_path)
        payload = self._load()
        libraries = list(payload["libraries"])
        existing = next(
            (item for item in libraries if item["library_id"] == library_id),
            None,
        )
        record = {
            "library_id": library_id,
            "library_path": str(library_path),
        }
        if existing is not None:
            if existing == record:
                return _registration(record)
            if not replace:
                raise BlenderWorkerError(
                    f"Blender asset library is already registered: {library_id}"
                )
            libraries[libraries.index(existing)] = record
        else:
            libraries.append(record)
        payload["libraries"] = sorted(libraries, key=lambda item: item["library_id"])
        self._write(payload)
        return _registration(record)

    def get(self, library_id: str) -> BlenderAssetLibraryRegistration | None:
        library_id = _validate_library_id(library_id)
        for item in self._load()["libraries"]:
            if item["library_id"] == library_id:
                return _registration(item)
        return None

    def list(self) -> tuple[BlenderAssetLibraryRegistration, ...]:
        return tuple(_registration(item) for item in self._load()["libraries"])

    def resolve(
        self,
        library_id: str,
    ) -> BlenderAssetLibraryRegistration:
        """Resolve the current local locator without pinning registered bytes."""

        registration = self.get(library_id)
        if registration is None:
            raise BlenderWorkerError(
                f"Blender asset library is not registered: {library_id}"
            )
        _validate_library_path(registration.library_path)
        return registration

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BlenderWorkerError(
                "Blender asset library registry is not valid UTF-8 JSON"
            ) from error
        if not isinstance(payload, dict):
            raise BlenderWorkerError("Blender asset library registry must be an object")
        if payload.get("schema") != REGISTRY_SCHEMA or str(payload.get("version")) != REGISTRY_VERSION:
            raise BlenderWorkerError("Blender asset library registry version is unsupported")
        libraries = payload.get("libraries")
        if not isinstance(libraries, list):
            raise BlenderWorkerError("Blender asset library registry libraries must be a list")
        seen: set[str] = set()
        for item in libraries:
            if not isinstance(item, dict):
                raise BlenderWorkerError("Blender asset library registry entry must be an object")
            library_id = _validate_library_id(item.get("library_id"))
            if library_id in seen:
                raise BlenderWorkerError(
                    f"Blender asset library registry contains duplicate id: {library_id}"
                )
            seen.add(library_id)
            if not isinstance(item.get("library_path"), str):
                raise BlenderWorkerError("Blender asset library path must be a string")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def _registration(value: dict[str, Any]) -> BlenderAssetLibraryRegistration:
    return BlenderAssetLibraryRegistration(
        library_id=str(value["library_id"]),
        library_path=Path(str(value["library_path"])),
    )


def _validate_library_id(value: object) -> str:
    if not isinstance(value, str) or not LIBRARY_ID.fullmatch(value):
        raise BlenderWorkerError(
            "library_id must start with a lowercase letter and use lowercase letters, digits, '.', '_' or '-'"
        )
    return value


def _validate_library_path(value: Path) -> Path:
    path = Path(value).resolve()
    if path.suffix.lower() != ".blend":
        raise BlenderWorkerError("Blender asset library must be a .blend file")
    if not path.is_file():
        raise BlenderWorkerError(f"Blender asset library was not found: {path}")
    return path
