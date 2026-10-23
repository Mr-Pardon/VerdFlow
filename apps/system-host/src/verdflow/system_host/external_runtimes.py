"""System-scoped external runtime locator persistence and discovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import RLock
import time
from typing import Callable
from uuid import uuid4

from verdflow.blender_worker import locate_blender


EXTERNAL_RUNTIME_CONFIG_SCHEMA = "verdflow.external-runtime-config"
EXTERNAL_RUNTIME_CONFIG_VERSION = "1"
BLENDER_RUNTIME_ID = "blender"
DTS_EXPLORER_RUNTIME_ID = "dts-explorer"
MAX_EXTERNAL_RUNTIME_CONFIG_BYTES = 64 * 1024
MAX_RUNTIME_PATH_LENGTH = 4096
_DTS_EXPLORER_SDK_RELATIVE_PATH = Path("SDK")
_DTS_EXPLORER_EXECUTABLE_RELATIVE_PATH = (
    Path("AirCityExplorer") / "Binaries" / "Win64" / "DTS Explorer.exe"
)
_DTS_SDK_FILES = ("ac_conf.js", "ac.min.js")


@dataclass(frozen=True, slots=True)
class DtsExplorerInstallation:
    root: Path
    sdk_root: Path
    executable_path: Path


@dataclass(frozen=True, slots=True)
class ExternalRuntimeResolution:
    runtime_id: str
    display_name: str
    path_kind: str
    configured_path: str | None
    resolved_path: str | None
    resolution_source: str
    available: bool
    detail_code: str
    required_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RuntimeDefinition:
    runtime_id: str
    display_name: str
    path_kind: str
    validator: Callable[[Path], str | None]
    discover: Callable[[], Path | None]
    required_names: tuple[str, ...] = ()


class ExternalRuntimeConfigStore:
    """Persist manual machine locators while resolving availability live."""

    def __init__(self, system_root: Path) -> None:
        self.root = system_root.resolve()
        self.path = self.root / "external-runtimes.json"
        self._lock = RLock()
        self._definitions = {
            BLENDER_RUNTIME_ID: _RuntimeDefinition(
                runtime_id=BLENDER_RUNTIME_ID,
                display_name="Blender",
                path_kind="file",
                validator=_validate_blender,
                discover=lambda: locate_blender(),
                required_names=("blender.exe",),
            ),
            DTS_EXPLORER_RUNTIME_ID: _RuntimeDefinition(
                runtime_id=DTS_EXPLORER_RUNTIME_ID,
                display_name="DTS Explorer",
                path_kind="directory",
                validator=_validate_dts_explorer,
                discover=_discover_dts_explorer,
                required_names=(
                    "SDK/ac_conf.js",
                    "SDK/ac.min.js",
                    "AirCityExplorer/Binaries/Win64/DTS Explorer.exe",
                ),
            ),
        }

    def prepare(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._load()
            else:
                self._write({})

    def list(self) -> tuple[ExternalRuntimeResolution, ...]:
        with self._lock:
            manual = self._load()
            return tuple(
                self._resolve(definition, manual.get(runtime_id))
                for runtime_id, definition in self._definitions.items()
            )

    def get(self, runtime_id: str) -> ExternalRuntimeResolution:
        normalized = _required_runtime_id(runtime_id)
        with self._lock:
            try:
                definition = self._definitions[normalized]
            except KeyError as error:
                raise ValueError(
                    f"External runtime is not supported: {normalized}"
                ) from error
            return self._resolve(definition, self._load().get(normalized))

    def configure(
        self,
        runtime_id: str,
        raw_path: str,
    ) -> ExternalRuntimeResolution:
        normalized = _required_runtime_id(runtime_id)
        with self._lock:
            try:
                definition = self._definitions[normalized]
            except KeyError as error:
                raise ValueError(
                    f"External runtime is not supported: {normalized}"
                ) from error
            path = _normalized_path(raw_path)
            detail = definition.validator(path)
            if detail is not None:
                raise ValueError(
                    f"External runtime path is invalid for {normalized}: {detail}"
                )
            manual = self._load()
            manual[normalized] = str(path)
            self._write(manual)
            return self._resolve(definition, str(path))

    def clear(self, runtime_id: str) -> ExternalRuntimeResolution:
        normalized = _required_runtime_id(runtime_id)
        with self._lock:
            try:
                definition = self._definitions[normalized]
            except KeyError as error:
                raise ValueError(
                    f"External runtime is not supported: {normalized}"
                ) from error
            manual = self._load()
            manual.pop(normalized, None)
            self._write(manual)
            return self._resolve(definition, None)

    def _resolve(
        self,
        definition: _RuntimeDefinition,
        configured_path: str | None,
    ) -> ExternalRuntimeResolution:
        if configured_path is not None:
            path = Path(configured_path)
            detail = definition.validator(path)
            return ExternalRuntimeResolution(
                runtime_id=definition.runtime_id,
                display_name=definition.display_name,
                path_kind=definition.path_kind,
                configured_path=str(path),
                resolved_path=str(path.resolve()) if detail is None else None,
                resolution_source="manual",
                available=detail is None,
                detail_code=detail or "configured",
                required_names=definition.required_names,
            )
        discovered = definition.discover()
        if discovered is not None:
            resolved = discovered.expanduser().resolve()
            detail = definition.validator(resolved)
            if detail is None:
                return ExternalRuntimeResolution(
                    runtime_id=definition.runtime_id,
                    display_name=definition.display_name,
                    path_kind=definition.path_kind,
                    configured_path=None,
                    resolved_path=str(resolved),
                    resolution_source="auto",
                    available=True,
                    detail_code="discovered",
                    required_names=definition.required_names,
                )
        return ExternalRuntimeResolution(
            runtime_id=definition.runtime_id,
            display_name=definition.display_name,
            path_kind=definition.path_kind,
            configured_path=None,
            resolved_path=None,
            resolution_source="unavailable",
            available=False,
            detail_code="not_found",
            required_names=definition.required_names,
        )

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        if self.path.stat().st_size > MAX_EXTERNAL_RUNTIME_CONFIG_BYTES:
            raise ValueError("External runtime config exceeds size limit")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "External runtime config is not valid UTF-8 JSON"
            ) from error
        if (
            not isinstance(document, dict)
            or document.get("schema") != EXTERNAL_RUNTIME_CONFIG_SCHEMA
            or str(document.get("version")) != EXTERNAL_RUNTIME_CONFIG_VERSION
        ):
            raise ValueError("External runtime config schema is unsupported")
        manual = document.get("manual_paths")
        if not isinstance(manual, dict):
            raise ValueError("External runtime manual_paths must be an object")
        parsed: dict[str, str] = {}
        for runtime_id, raw_path in manual.items():
            if runtime_id not in self._definitions:
                raise ValueError(
                    f"External runtime config contains unknown id: {runtime_id}"
                )
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError(
                    f"External runtime path is invalid: {runtime_id}"
                )
            parsed[runtime_id] = raw_path
        return parsed

    def _write(self, manual_paths: dict[str, str]) -> None:
        document = {
            "schema": EXTERNAL_RUNTIME_CONFIG_SCHEMA,
            "version": EXTERNAL_RUNTIME_CONFIG_VERSION,
            "manual_paths": dict(sorted(manual_paths.items())),
        }
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_EXTERNAL_RUNTIME_CONFIG_BYTES:
            raise ValueError("External runtime config exceeds size limit")
        temporary = self.root / f".{self.path.name}.{uuid4().hex}.tmp"
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            for attempt in range(20):
                try:
                    os.replace(temporary, self.path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.01)
        finally:
            temporary.unlink(missing_ok=True)


def _required_runtime_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("runtime_id is required")
    return value.strip()


def _normalized_path(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path is required")
    if "\0" in value or len(value) > MAX_RUNTIME_PATH_LENGTH:
        raise ValueError("path is invalid")
    return Path(value.strip()).expanduser().resolve()


def _validate_blender(path: Path) -> str | None:
    return None if path.is_file() else "file_not_found"


def resolve_dts_explorer_installation(
    root: Path,
) -> DtsExplorerInstallation:
    resolved = root.expanduser().resolve()
    detail = _validate_dts_explorer(resolved)
    if detail is not None:
        raise ValueError(f"DTS Explorer installation is invalid: {detail}")
    return DtsExplorerInstallation(
        root=resolved,
        sdk_root=resolved / _DTS_EXPLORER_SDK_RELATIVE_PATH,
        executable_path=resolved / _DTS_EXPLORER_EXECUTABLE_RELATIVE_PATH,
    )


def _validate_dts_explorer(path: Path) -> str | None:
    if not path.is_dir():
        return "directory_not_found"
    sdk_root = path / _DTS_EXPLORER_SDK_RELATIVE_PATH
    if not sdk_root.is_dir():
        return "missing_sdk_directory"
    for filename in _DTS_SDK_FILES:
        if not (sdk_root / filename).is_file():
            return f"missing_sdk_{filename.removesuffix('.js').replace('.', '_')}"
    if not (path / _DTS_EXPLORER_EXECUTABLE_RELATIVE_PATH).is_file():
        return "missing_executable"
    return None


def _discover_dts_explorer() -> Path | None:
    for name in ("VERDFLOW_DTS_EXPLORER_ROOT", "DTS_EXPLORER_ROOT"):
        value = os.environ.get(name)
        if value:
            candidate = Path(value).expanduser()
            if _validate_dts_explorer(candidate) is None:
                return candidate.resolve()
    return None
