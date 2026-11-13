"""Run at most one optional imagery detail read outside the host process."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import monotonic
from typing import Any

from verdflow.worker_runtime import ProcessTreeGuard

from .protocol import IMAGERY_TILE_INLINE_GEOMETRY_BYTES, IMAGERY_TILE_RESPONSE_BYTES


_STARTUP_IDLE_SECONDS = 60.0
_WARMUP_RETRY_MS = 350
_WARMUP_PROBE_SECONDS = 0.12


@dataclass(slots=True)
class _Startup:
    last_progress: float
    disk_index: bool
    ready: bool = False


class ImageryTileReader:
    def __init__(self) -> None:
        self._lock = Lock()
        self._closed = False
        self._session: tuple[str, str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._guard: ProcessTreeGuard | None = None
        self._responses: Queue[bytes] = Queue(1)
        self._startup: _Startup | None = None
        self._cache: TemporaryDirectory | None = None
        self._failed_start: tuple[tuple[str, str], float] | None = None

    def read(self, session: tuple[str, str], python: Path, image: Path, regions: Path,
             grid: dict[str, Any], tile: tuple[int, int, int], *,
             cache_root: Path | None = None, expected_count: int | None = None,
             refinement: tuple[str, int, int] | None = None) -> dict[str, Any] | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self._closed:
                return None
            if self._failed_start is not None:
                failed_session, failed_at = self._failed_start
                if failed_session == session and monotonic() - failed_at < 3.0:
                    return None
                self._failed_start = None
            if (self._session == session and self._process is not None and self._process.poll() is not None
                    and self._startup is not None and not self._startup.ready):
                self._failed_start = (session, monotonic())
                self._stop()
                return None
            if self._session != session or self._process is None or self._process.poll() is not None:
                self._stop()
                self._start(session, python, image, regions, grid, cache_root, expected_count)
            assert self._process is not None and self._process.stdin is not None and self._startup is not None
            if not self._startup.ready:
                try:
                    ready = self._responses.get(timeout=_WARMUP_PROBE_SECONDS if self._startup.disk_index else 8.0)
                except Empty:
                    if (self._startup.disk_index and self._process.poll() is None
                            and monotonic() - self._startup.last_progress < _STARTUP_IDLE_SECONDS):
                        return {"retry_after_ms": _WARMUP_RETRY_MS}
                    raise
                if ready != b"ready":
                    self._failed_start = (session, monotonic())
                    self._stop()
                    return None
                self._startup.ready = True
            self._process.stdin.write(json.dumps((*tile, refinement) if refinement else tile).encode("ascii") + b"\n")
            self._process.stdin.flush()
            return json.loads(self._responses.get(timeout=8.0))
        except (OSError, Empty, ValueError):
            if self._startup is None or not self._startup.ready:
                self._failed_start = (session, monotonic())
            self._stop()
            return None
        finally:
            self._lock.release()

    def close_project(self, session_id: str) -> None:
        with self._lock:
            if self._session and self._session[0] == session_id:
                self._stop()
            if self._failed_start and self._failed_start[0][0] == session_id:
                self._failed_start = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop()

    def _start(
        self, session: tuple[str, str], python: Path, image: Path, regions: Path, grid: dict[str, Any],
        cache_root: Path | None, expected_count: int | None,
    ) -> None:
        cache_path = ""
        disk_index = regions.stat().st_size > IMAGERY_TILE_INLINE_GEOMETRY_BYTES
        if disk_index and cache_root is None:
            raise ValueError("Large imagery detail requires a project cache directory")
        if disk_index or grid.get("refinement_mask"):
            if cache_root is not None:
                cache_root.mkdir(parents=True, exist_ok=True)
            self._cache = TemporaryDirectory(prefix="imagery-detail-", dir=cache_root, ignore_cleanup_errors=True)
            grid = {**grid, "refinement_cache": self._cache.name}
            if disk_index:
                cache_path = str(Path(self._cache.name) / "geometry.sqlite")
        guard = ProcessTreeGuard(memory_limit_bytes=2 * 1024**3)
        self._guard = guard
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
        env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        if self._cache is not None:
            env.update(TEMP=self._cache.name, TMP=self._cache.name)
        process = subprocess.Popen(
            [str(python), "-I", "-B", str(Path(__file__).with_name("imagery_tiles.py")),
             str(image), str(regions), json.dumps({**grid, "expected_region_count": expected_count}), cache_path],
            env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **guard.popen_options(),
        )
        self._process = process
        guard.attach(process)
        self._session = session
        responses: Queue[bytes] = Queue(1)
        self._responses = responses
        startup = _Startup(monotonic(), disk_index)
        self._startup = startup

        def receive() -> None:
            assert process.stdout is not None
            try:
                while line := process.stdout.readline(IMAGERY_TILE_RESPONSE_BYTES + 1):
                    if len(line) > IMAGERY_TILE_RESPONSE_BYTES or not line.endswith(b"\n"):
                        break
                    message = line.rstrip(b"\r\n")
                    if message == b"progress":
                        startup.last_progress = monotonic()
                    else:
                        responses.put_nowait(message)
            except (OSError, Full):
                pass
            finally:
                if responses.empty():
                    responses.put_nowait(b"")

        Thread(target=receive, daemon=True, name="verdflow-imagery-tile-reader").start()

    def _stop(self) -> None:
        process, guard = self._process, self._guard
        self._process = self._guard = self._session = None
        self._startup = None
        try:
            if guard is not None:
                try:
                    if process is not None:
                        guard.terminate(process, grace_seconds=2.0)
                finally:
                    guard.close()
                    if process is not None:
                        for stream in (process.stdin, process.stdout):
                            if stream is not None:
                                stream.close()
        finally:
            if self._cache is not None:
                self._cache.cleanup()
                self._cache = None
