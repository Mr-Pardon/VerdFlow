"""Read validated strategy documents from immutable project snapshots."""

from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any
import zipfile

from verdflow.project_store import ProjectStore

from .package import inspect_strategy_package


MAX_ARCHIVE_ENTRIES = 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class ProjectStrategyDocumentProvider:
    """Inspect a PlantingRun's frozen strategy archive without Library access."""

    def __init__(self, store: ProjectStore) -> None:
        self._store = store
        self._runtime_root = store.root / "runtime" / "strategy-documents"

    def load_strategy_document(
        self,
        package_hash: str,
        package_artifact_id: str,
    ) -> dict[str, Any]:
        artifact = self._store.get_artifact(package_artifact_id)
        if (
            artifact.schema_id != "verdflow.strategy-package"
            or artifact.schema_version != "1"
        ):
            raise ValueError("Strategy snapshot Artifact schema is unsupported")
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._runtime_root) as temporary:
            root = Path(temporary)
            archive_path = root / "strategy.zip"
            package_root = root / "package"
            package_root.mkdir()
            self._store.materialize_artifact(
                package_artifact_id,
                archive_path,
            )
            self._extract_archive(archive_path, package_root)
            package = inspect_strategy_package(package_root)
            if package.package_hash != package_hash:
                raise ValueError(
                    "Strategy snapshot differs from the PlantingRun package hash"
                )
            return copy.deepcopy(package.strategy)

    @staticmethod
    def _extract_archive(archive_path: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("Strategy snapshot archive entry count is invalid")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Strategy snapshot archive contains duplicate paths")
            total = sum(info.file_size for info in infos)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Strategy snapshot archive is too large")
            for info in infos:
                relative = ProjectStrategyDocumentProvider._archive_path(
                    info.filename
                )
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError("Strategy snapshot archive contains a symlink")
                target = destination.joinpath(*relative.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    @staticmethod
    def _archive_path(value: str) -> PurePosixPath:
        if not value or "\\" in value or ":" in value:
            raise ValueError("Strategy snapshot archive path is invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise ValueError("Strategy snapshot archive path is invalid")
        return path

