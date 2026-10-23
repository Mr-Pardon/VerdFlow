"""Process-tree containment shared by external Worker adapters."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
from typing import Any


class ProcessTreeGuard:
    """Own a child process tree and terminate it when the guard closes."""

    def __init__(self) -> None:
        self._job_handle: int | None = None
        self._process_group_id: int | None = None
        self.mode = "posix-process-group"
        if os.name == "nt":
            self._job_handle = _create_kill_on_close_job()
            self.mode = "windows-job-object"

    def popen_options(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"start_new_session": True}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        return {
            "startupinfo": startupinfo,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        if os.name != "nt":
            self._process_group_id = process.pid
            return
        assert self._job_handle is not None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        process_handle = ctypes.c_void_p(int(process._handle))
        if not kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(self._job_handle),
            process_handle,
        ):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "Could not attach Worker to the process tree guard")

    def terminate(
        self,
        process: subprocess.Popen[bytes],
        *,
        grace_seconds: float,
    ) -> int:
        if process.poll() is not None:
            return int(process.returncode)
        if os.name == "nt" and self._job_handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            kernel32.TerminateJobObject.restype = ctypes.c_int
            kernel32.TerminateJobObject(ctypes.c_void_p(self._job_handle), 1)
        elif os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            return int(process.wait(timeout=grace_seconds))
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            return int(process.wait(timeout=grace_seconds))

    def close(self) -> None:
        if os.name != "nt":
            if self._process_group_id is not None:
                try:
                    os.killpg(self._process_group_id, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                self._process_group_id = None
            return
        if self._job_handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(ctypes.c_void_p(self._job_handle))
        self._job_handle = None


def _create_kill_on_close_job() -> int:
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)
    return int(handle)


def terminate_process_by_pid(pid: int) -> None:
    """Terminate a previously identity-verified process."""
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            os.kill(pid, signal.SIGKILL)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_terminate = 0x0001
    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(process_terminate, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)
