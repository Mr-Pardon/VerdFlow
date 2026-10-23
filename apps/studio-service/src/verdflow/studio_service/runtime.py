"""Lazy read-only runtimes for Studio-specific project projections."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from queue import Queue
from threading import RLock, Thread
from typing import Callable, TypeVar, cast
from uuid import uuid4

from verdflow.project_store import (
    PointSetNotFoundError,
    ProjectQueryStore,
    RegionSetNotFoundError,
    SnapshotNotFoundError,
)

from .models import (
    PointReviewManifest,
    PointViewportChunk,
    RegionViewerManifest,
    RegionViewportChunk,
)
from .viewer_queries import ProjectViewerQueries


ResultT = TypeVar("ResultT")
_STOP = object()


class StudioResourceNotFoundError(RuntimeError):
    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(f"{resource_type} was not found: {resource_id}")
        self.resource_type = resource_type
        self.resource_id = resource_id


class StudioProjectQueryRuntime:
    """Own one read-only project connection and its transient UI caches."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.resolve()
        self._queue: Queue[object] = Queue()
        self._ready: Future[None] = Future()
        self._thread = Thread(
            target=self._run,
            name=f"verdflow-studio-query-{uuid4().hex[:8]}",
            daemon=True,
        )

    def start(self, timeout_seconds: float) -> None:
        self._thread.start()
        self._ready.result(timeout=timeout_seconds)

    def call(
        self,
        callback: Callable[[ProjectViewerQueries], ResultT],
        *,
        timeout_seconds: float,
    ) -> ResultT:
        future: Future[object] = Future()
        self._queue.put((callback, future))
        try:
            return cast(ResultT, future.result(timeout=timeout_seconds))
        except TimeoutError:
            future.cancel()
            raise

    def close(self, timeout_seconds: float) -> None:
        if not self._thread.is_alive():
            return
        self._queue.put(_STOP)
        self._thread.join(timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Studio project query thread did not stop in time")

    def _run(self) -> None:
        try:
            with ProjectQueryStore.open(self.project_path) as store:
                queries = ProjectViewerQueries(store)
                self._ready.set_result(None)
                while True:
                    item = self._queue.get()
                    if item is _STOP:
                        return
                    callback, future = cast(
                        tuple[
                            Callable[[ProjectViewerQueries], object],
                            Future[object],
                        ],
                        item,
                    )
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(callback(queries))
                        except BaseException as error:
                            future.set_exception(error)
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)


class StudioQueryService:
    """Lazily own Studio projections without entering Core/SystemHost."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._lock = RLock()
        self._runtimes: dict[str, StudioProjectQueryRuntime] = {}

    def close(self) -> None:
        with self._lock:
            runtimes = tuple(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.close(self._timeout_seconds)

    def close_project(self, project_key: str) -> None:
        with self._lock:
            runtime = self._runtimes.pop(project_key, None)
        if runtime is not None:
            runtime.close(self._timeout_seconds)

    def get_region_manifest(
        self,
        project_key: str,
        project_path: Path,
        region_set_id: str,
    ) -> RegionViewerManifest:
        try:
            return self._runtime(project_key, project_path).call(
                lambda queries: queries.region_manifest(region_set_id),
                timeout_seconds=self._timeout_seconds,
            )
        except RegionSetNotFoundError as error:
            raise StudioResourceNotFoundError(
                "RegionSet",
                region_set_id,
            ) from error

    def get_region_chunk(
        self,
        project_key: str,
        project_path: Path,
        region_set_id: str,
        *,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        classifications: tuple[str, ...] = (),
        after_ordinal: int = -1,
        limit: int = 1000,
        simplification_tolerance: float = 0.0,
    ) -> RegionViewportChunk:
        try:
            return self._runtime(project_key, project_path).call(
                lambda queries: queries.region_chunk(
                    region_set_id,
                    min_x=min_x,
                    min_y=min_y,
                    max_x=max_x,
                    max_y=max_y,
                    classifications=classifications,
                    after_ordinal=after_ordinal,
                    limit=limit,
                    simplification_tolerance=simplification_tolerance,
                ),
                timeout_seconds=self._timeout_seconds,
            )
        except RegionSetNotFoundError as error:
            raise StudioResourceNotFoundError(
                "RegionSet",
                region_set_id,
            ) from error

    def get_point_manifest(
        self,
        project_key: str,
        project_path: Path,
        point_set_id: str,
        assignment_set_id: str,
    ) -> PointReviewManifest:
        try:
            return self._runtime(project_key, project_path).call(
                lambda queries: queries.point_manifest(
                    point_set_id,
                    assignment_set_id,
                ),
                timeout_seconds=self._timeout_seconds,
            )
        except (PointSetNotFoundError, SnapshotNotFoundError) as error:
            raise StudioResourceNotFoundError(
                "Point review",
                f"{point_set_id}/{assignment_set_id}",
            ) from error

    def get_point_chunk(
        self,
        project_key: str,
        project_path: Path,
        point_set_id: str,
        assignment_set_id: str,
        *,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        roles: tuple[str, ...] = (),
        assets: tuple[str, ...] = (),
        after_ordinal: int = -1,
        limit: int = 5000,
    ) -> PointViewportChunk:
        try:
            return self._runtime(project_key, project_path).call(
                lambda queries: queries.point_chunk(
                    point_set_id,
                    assignment_set_id,
                    min_x=min_x,
                    min_y=min_y,
                    max_x=max_x,
                    max_y=max_y,
                    roles=roles,
                    assets=assets,
                    after_ordinal=after_ordinal,
                    limit=limit,
                ),
                timeout_seconds=self._timeout_seconds,
            )
        except (PointSetNotFoundError, SnapshotNotFoundError) as error:
            raise StudioResourceNotFoundError(
                "Point review",
                f"{point_set_id}/{assignment_set_id}",
            ) from error

    def _runtime(
        self,
        project_key: str,
        project_path: Path,
    ) -> StudioProjectQueryRuntime:
        key = project_key.strip()
        if not key:
            raise ValueError("project_key is required")
        resolved = project_path.resolve()
        with self._lock:
            existing = self._runtimes.get(key)
            if existing is not None:
                if existing.project_path != resolved:
                    raise ValueError("project_key is already bound to another path")
                return existing
            runtime = StudioProjectQueryRuntime(resolved)
            try:
                runtime.start(self._timeout_seconds)
            except BaseException:
                runtime.close(self._timeout_seconds)
                raise
            self._runtimes[key] = runtime
            return runtime
