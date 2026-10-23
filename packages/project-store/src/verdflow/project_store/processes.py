"""Operating-system process identity inspection for lock ownership."""

from __future__ import annotations

import ctypes
import os
import socket
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class ProcessStatus(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    host: str
    pid: int
    started_at_token: str


@dataclass(frozen=True, slots=True)
class ProcessProbe:
    status: ProcessStatus
    identity: ProcessIdentity | None = None


class ProcessInspector(Protocol):
    def current_identity(self) -> ProcessIdentity:
        """Return the identity of the current process."""

    def probe(self, host: str, pid: int) -> ProcessProbe:
        """Inspect a process without assuming PID uniqueness."""


class SystemProcessInspector:
    """Reads a stable process-start token using only the standard library."""

    def __init__(self, host: str | None = None) -> None:
        self.host = host or socket.gethostname()

    def current_identity(self) -> ProcessIdentity:
        pid = os.getpid()
        token = self._started_at_token(pid)
        if token is None:
            raise RuntimeError("Current process identity is unavailable")
        return ProcessIdentity(self.host, pid, token)

    def probe(self, host: str, pid: int) -> ProcessProbe:
        if host.casefold() != self.host.casefold():
            return ProcessProbe(ProcessStatus.UNKNOWN)
        try:
            token = self._started_at_token(pid)
        except PermissionError:
            return ProcessProbe(ProcessStatus.UNKNOWN)
        if token is None:
            return ProcessProbe(ProcessStatus.DEAD)
        return ProcessProbe(
            ProcessStatus.ALIVE,
            ProcessIdentity(self.host, pid, token),
        )

    @staticmethod
    def _started_at_token(pid: int) -> str | None:
        if os.name == "nt":
            return _windows_process_started_at(pid)
        return _posix_process_started_at(pid)


def _windows_process_started_at(pid: int) -> str | None:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    error_invalid_parameter = 87
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    filetime_pointer = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return None
        if error == error_access_denied:
            raise PermissionError(pid)
        return None

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(
            handle,
            ctypes.byref(exit_code),
        ):
            error = ctypes.get_last_error()
            if error == error_access_denied:
                raise PermissionError(pid)
            return None
        if exit_code.value != still_active:
            return None
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            error = ctypes.get_last_error()
            if error == error_access_denied:
                raise PermissionError(pid)
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"win-filetime:{ticks}"
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_started_at(pid: int) -> str | None:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return None
    except PermissionError:
        raise
    except OSError:
        fields = []
    if len(fields) > 21:
        return f"proc-start-ticks:{fields[21]}"

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        raise
    raise PermissionError(
        "The platform cannot provide a stable process-start identity"
    )
