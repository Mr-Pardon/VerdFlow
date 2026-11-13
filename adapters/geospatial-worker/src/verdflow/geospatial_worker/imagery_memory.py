"""Windows memory admission shared by imagery jobs in a single Host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from verdflow.worker_runtime import WindowsMemoryHeadroom, read_windows_memory_headroom

from .errors import GeospatialDataError


_MIB = 1024**2
_MIN_WORKER_BYTES = 512 * _MIB
_TELEMETRY_FALLBACK_BYTES = 3072 * _MIB


def _usable_commit(snapshot: WindowsMemoryHeadroom) -> int:
    commit_reserve = max(768 * _MIB, snapshot.total_physical_bytes // 16)
    physical_reserve = max(256 * _MIB, snapshot.total_physical_bytes // 32)
    # A committed page need not be resident. Allow some paging, but do not
    # let a large pagefile alone make an already busy desktop look idle.
    return max(0, min(
        snapshot.available_commit_bytes - commit_reserve,
        2 * (snapshot.available_physical_bytes - physical_reserve),
    ))


@dataclass(eq=False, frozen=True, slots=True)
class ImageryMemoryReservation:
    limit_bytes: int
    _pool: ImageryMemoryPool

    def close(self) -> None:
        self._pool.release(self)


class ImageryMemoryPool:
    def __init__(
        self, read_headroom: Callable[[], WindowsMemoryHeadroom | None] = read_windows_memory_headroom,
    ) -> None:
        self._read_headroom = read_headroom
        self._lock = Lock()
        self._active: set[ImageryMemoryReservation] = set()

    def try_reserve(self) -> ImageryMemoryReservation | None:
        """Return None to wait for sibling work; reject only actual low headroom."""
        with self._lock:
            snapshot = self._read_headroom()
            capacity = _TELEMETRY_FALLBACK_BYTES if snapshot is None else _usable_commit(snapshot)
            # Charge the whole live allowance, not just current usage: a
            # sibling can still allocate up to its limit in its next phase.
            capacity -= sum(active.limit_bytes for active in self._active)
            limit = max(0, capacity) // _MIB * _MIB
            if limit < _MIN_WORKER_BYTES:
                if self._active:
                    return None
                raise GeospatialDataError(
                    "imagery_memory_pressure",
                    f"当前可用于影像处理的内存约 {limit // _MIB} MiB，不足以安全启动。请释放内存后重试，项目已保留。",
                )
            reservation = ImageryMemoryReservation(limit, self)
            self._active.add(reservation)
            return reservation

    def release(self, reservation: ImageryMemoryReservation) -> None:
        with self._lock:
            self._active.discard(reservation)
