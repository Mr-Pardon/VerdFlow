"""Machine-scoped, cancellable starter imports outside the Library owner thread."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from typing import Callable
from uuid import uuid4

from .resource_downloads import (
    DownloadCancelled, StarterManifest, StarterResource, download_package, load_manifest,
)


@dataclass(frozen=True)
class StarterImportItem:
    id: str
    name: str
    status: str = "pending"
    downloaded_bytes: int = 0
    total_bytes: int = 0
    message: str = ""


@dataclass(frozen=True)
class ImportChoice:
    id: str
    name: str
    kind: str
    version: str = ""
    bytes: int = 0
    optional: bool = False
    conflict: bool = False
    blocked: bool = False
    message: str = ""
    page_url: str = ""


@dataclass(frozen=True)
class StarterImportOperation:
    operation_id: str
    status: str
    items: tuple[StarterImportItem, ...]
    choices: tuple[ImportChoice, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str = ""


class StarterResourceService:
    def __init__(self, importer: Callable[[StarterResource, Path], bool]) -> None:
        self._importer = importer
        self._lock = RLock()
        self._manifest: StarterManifest | None = None
        self._operation: StarterImportOperation | None = None
        self._thread: Thread | None = None
        self._cancel = Event()
        self._closed = False

    def manifest(self) -> StarterManifest:
        manifest = load_manifest()
        with self._lock:
            self._manifest = manifest
        return manifest

    def start(self, revision: str) -> StarterImportOperation:
        with self._lock:
            if self._closed:
                raise ValueError("资源导入服务已关闭。")
            if self._operation and self._operation.status in {"preparing", "awaiting_confirmation", "running", "cancelling"}:
                raise ValueError("已有资源正在导入，请等待完成。")
            if not self._manifest or self._manifest.revision != revision:
                raise ValueError("推荐清单已更新，请重新读取后导入。")
            if not self._manifest.resources:
                raise ValueError("推荐清单暂时没有资源。")
            self._cancel = Event()
            self._operation = StarterImportOperation(str(uuid4()), "running", tuple(
                StarterImportItem(item.id, item.name, total_bytes=item.bytes) for item in self._manifest.resources
            ))
            self._thread = Thread(target=self._run, args=(self._manifest.resources,), name="starter-resources", daemon=True)
            self._thread.start()
            return self._operation

    def get(self, operation_id: str) -> StarterImportOperation:
        with self._lock:
            if not self._operation or self._operation.operation_id != operation_id:
                raise ValueError("资源导入记录不存在。")
            return self._operation

    def cancel(self, operation_id: str) -> StarterImportOperation:
        with self._lock:
            operation = self.get(operation_id)
            if operation.status == "running":
                self._cancel.set()
                self._operation = replace(operation, status="cancelling")
            return self._operation

    def _update(self, index: int, **changes) -> None:
        with self._lock:
            items = list(self._operation.items)
            items[index] = replace(items[index], **changes)
            self._operation = replace(self._operation, items=tuple(items))

    def _run(self, resources: tuple[StarterResource, ...]) -> None:
        try:
            for index, item in enumerate(resources):
                if self._cancel.is_set():
                    break
                try:
                    with TemporaryDirectory(prefix="verdflow-starter-") as temporary:
                        path = Path(temporary) / ("resource.vfa" if item.kind == "assets" else "resource.vfs")
                        self._update(index, status="downloading")
                        download_package(item, path, self._cancel, lambda size: self._update(index, downloaded_bytes=size))
                        if self._cancel.is_set():
                            raise DownloadCancelled()
                        self._update(index, status="importing")
                        existing = self._importer(item, path)
                        self._update(index, status="existing" if existing else "imported")
                except DownloadCancelled:
                    break
                except Exception as error:
                    self._update(index, status="failed", message=str(error))
        finally:
            with self._lock:
                items = tuple(replace(item, status="cancelled") if item.status in {"pending", "downloading", "importing"} else item for item in self._operation.items)
                failed = sum(item.status == "failed" for item in items)
                status = "cancelled" if self._cancel.is_set() else "failed" if failed == len(items) else "partial" if failed else "completed"
                self._operation = replace(self._operation, status=status, items=items)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread:
            thread.join(timeout=45)
            if thread.is_alive():
                raise RuntimeError("Starter resource import did not stop")
