"""Atomic system-scoped registry for recently opened VerdFlow projects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4


PROJECT_REGISTRY_SCHEMA = "verdflow.system-project-registry"
PROJECT_REGISTRY_VERSION = "1"
MAX_PROJECT_REGISTRY_BYTES = 4 * 1024 * 1024


class ProjectAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class ProjectWorkspace(StrEnum):
    DTS = "dts"
    BLENDER = "blender"


@dataclass(frozen=True, slots=True)
class RegisteredProject:
    registry_id: str
    project_id: str | None
    name: str
    project_path: str
    last_opened_at: str
    last_workspace: ProjectWorkspace | None
    last_error: str | None = None


class ProjectRegistry:
    """Persist discovery metadata without opening or scanning project stores."""

    def __init__(self, system_root: Path) -> None:
        self.root = system_root.resolve()
        self.path = self.root / "projects.json"
        self._lock = RLock()

    def prepare(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._load()
            else:
                self._write(())

    def record_opened(
        self,
        *,
        project_id: str,
        name: str,
        project_path: Path,
        opened_at: str,
        workspace: ProjectWorkspace | None,
    ) -> RegisteredProject:
        with self._lock:
            projects = self._load()
            existing = self._find_path(projects, project_path)
            record = RegisteredProject(
                registry_id=(
                    existing.registry_id
                    if existing is not None
                    else f"recent_{uuid4().hex}"
                ),
                project_id=_required_text(project_id, "project_id"),
                name=_required_text(name, "name"),
                project_path=str(project_path.resolve()),
                last_opened_at=_required_text(opened_at, "opened_at"),
                last_workspace=workspace,
                last_error=None,
            )
            self._write(self._replace(projects, record))
            return record

    def record_failed_open(
        self,
        *,
        project_path: Path,
        attempted_at: str,
        error: str,
        workspace: ProjectWorkspace | None,
    ) -> RegisteredProject:
        with self._lock:
            projects = self._load()
            existing = self._find_path(projects, project_path)
            record = RegisteredProject(
                registry_id=(
                    existing.registry_id
                    if existing is not None
                    else f"recent_{uuid4().hex}"
                ),
                project_id=existing.project_id if existing else None,
                name=(
                    existing.name
                    if existing is not None
                    else project_path.resolve().stem
                ),
                project_path=str(project_path.resolve()),
                last_opened_at=_required_text(attempted_at, "attempted_at"),
                last_workspace=(
                    workspace
                    if workspace is not None
                    else existing.last_workspace if existing else None
                ),
                last_error=_required_text(error, "error"),
            )
            self._write(self._replace(projects, record))
            return record

    def list(self) -> tuple[RegisteredProject, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._load(),
                    key=lambda item: (item.last_opened_at, item.registry_id),
                    reverse=True,
                )
            )

    def get(self, registry_id: str) -> RegisteredProject | None:
        with self._lock:
            normalized = _required_text(registry_id, "registry_id")
            return next(
                (
                    item
                    for item in self._load()
                    if item.registry_id == normalized
                ),
                None,
            )

    def remove(self, registry_id: str) -> bool:
        with self._lock:
            normalized = _required_text(registry_id, "registry_id")
            projects = self._load()
            retained = tuple(
                item for item in projects if item.registry_id != normalized
            )
            if len(retained) == len(projects):
                return False
            self._write(retained)
            return True

    def _load(self) -> tuple[RegisteredProject, ...]:
        if not self.path.is_file():
            return ()
        if self.path.stat().st_size > MAX_PROJECT_REGISTRY_BYTES:
            raise ValueError("Project registry exceeds size limit")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Project registry is not valid UTF-8 JSON") from error
        if not isinstance(document, dict):
            raise ValueError("Project registry must be an object")
        if (
            document.get("schema") != PROJECT_REGISTRY_SCHEMA
            or str(document.get("version")) != PROJECT_REGISTRY_VERSION
        ):
            raise ValueError("Project registry schema is unsupported")
        rows = document.get("projects")
        if not isinstance(rows, list):
            raise ValueError("Project registry projects must be an array")
        records = tuple(_record_from_document(row) for row in rows)
        paths = [_path_key(Path(item.project_path)) for item in records]
        if len(paths) != len(set(paths)):
            raise ValueError("Project registry contains duplicate paths")
        return records

    def _write(self, projects: tuple[RegisteredProject, ...]) -> None:
        document = {
            "schema": PROJECT_REGISTRY_SCHEMA,
            "version": PROJECT_REGISTRY_VERSION,
            "projects": [_record_document(item) for item in projects],
        }
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_PROJECT_REGISTRY_BYTES:
            raise ValueError("Project registry exceeds size limit")
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

    @staticmethod
    def _find_path(
        projects: tuple[RegisteredProject, ...],
        path: Path,
    ) -> RegisteredProject | None:
        key = _path_key(path)
        return next(
            (item for item in projects if _path_key(Path(item.project_path)) == key),
            None,
        )

    @staticmethod
    def _replace(
        projects: tuple[RegisteredProject, ...],
        record: RegisteredProject,
    ) -> tuple[RegisteredProject, ...]:
        key = _path_key(Path(record.project_path))
        return tuple(
            item
            for item in projects
            if _path_key(Path(item.project_path)) != key
        ) + (record,)


def availability(record: RegisteredProject) -> ProjectAvailability:
    path = Path(record.project_path)
    if not path.is_dir() or not (path / "project.db").is_file():
        return ProjectAvailability.MISSING
    if record.last_error is not None:
        return ProjectAvailability.UNAVAILABLE
    return ProjectAvailability.AVAILABLE


def _record_document(record: RegisteredProject) -> dict[str, object]:
    return {
        "registry_id": record.registry_id,
        "project_id": record.project_id,
        "name": record.name,
        "project_path": record.project_path,
        "last_opened_at": record.last_opened_at,
        "last_workspace": (
            record.last_workspace.value if record.last_workspace else None
        ),
        "last_error": record.last_error,
    }


def _record_from_document(value: object) -> RegisteredProject:
    if not isinstance(value, dict):
        raise ValueError("Project registry item must be an object")
    project_id = value.get("project_id")
    if project_id is not None:
        project_id = _required_text(project_id, "project_id")
    workspace = value.get("last_workspace")
    return RegisteredProject(
        registry_id=_required_text(value.get("registry_id"), "registry_id"),
        project_id=project_id,
        name=_required_text(value.get("name"), "name"),
        project_path=_required_text(value.get("project_path"), "project_path"),
        last_opened_at=_required_text(
            value.get("last_opened_at"),
            "last_opened_at",
        ),
        last_workspace=(ProjectWorkspace(workspace) if workspace else None),
        last_error=_optional_text(value.get("last_error")),
    )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional project registry text is invalid")
    return value.strip() or None


def _path_key(path: Path) -> str:
    return str(path.resolve()).casefold()
