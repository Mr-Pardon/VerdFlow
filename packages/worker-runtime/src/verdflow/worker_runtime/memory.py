"""System memory telemetry for native Worker admission."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os


@dataclass(frozen=True, slots=True)
class WindowsMemoryHeadroom:
    total_physical_bytes: int
    available_physical_bytes: int
    available_commit_bytes: int


def read_windows_memory_headroom() -> WindowsMemoryHeadroom | None:
    """Read system-wide memory without relying on potential pagefile growth."""
    if os.name != "nt":
        return None
    binding = _performance_query()
    if binding is None:
        return None
    query, structure, size = binding
    information = structure(cb=size)
    if not query(information, size):
        return None
    page = information.page_size
    if not page or not information.physical_total or information.physical_available > information.physical_total:
        return None
    return WindowsMemoryHeadroom(
        information.physical_total * page,
        information.physical_available * page,
        max(0, information.commit_limit - information.commit_total) * page,
    )


@lru_cache(maxsize=1)
def _performance_query():
    import ctypes
    from ctypes import wintypes

    class PerformanceInformation(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            *((name, ctypes.c_size_t) for name in (
                "commit_total", "commit_limit", "commit_peak", "physical_total",
                "physical_available", "system_cache", "kernel_total", "kernel_paged",
                "kernel_nonpaged", "page_size",
            )),
            ("handle_count", wintypes.DWORD),
            ("process_count", wintypes.DWORD),
            ("thread_count", wintypes.DWORD),
        ]

    try:
        query = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
    except (OSError, AttributeError):
        return None
    query.argtypes = [ctypes.POINTER(PerformanceInformation), wintypes.DWORD]
    query.restype = wintypes.BOOL
    return query, PerformanceInformation, ctypes.sizeof(PerformanceInformation)
