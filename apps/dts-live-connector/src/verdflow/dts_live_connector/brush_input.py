"""Ephemeral, foreground-only brush commands. No hooks, key injection or event history."""

import ctypes
from ctypes import wintypes as w
from pathlib import Path
import sys
from threading import Lock
from time import monotonic, sleep
from typing import Callable


VK_TAB, VK_SHIFT, VK_CONTROL, VK_ALT = 0x09, 0x10, 0x11, 0x12


class Tap:
    """Require neutral -> one held brush key -> neutral; discard intervening input."""

    def __init__(self):
        self.ready = False
        self.held = 0
        self.stamp = 0

    def feed(self, state: tuple[int, int] | None) -> int:
        if state is None:
            self.ready, self.held = False, 0
            return 0
        key, stamp = state
        if key == 0:
            result = self.held if self.ready else 0
            self.ready, self.held = True, 0
            return result
        if self.held and (self.held != key or stamp != self.stamp):
            self.ready = False
        if self.ready:
            self.held, self.stamp = key, stamp
        return 0


class _Gui(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("flags", w.DWORD)] + [
        (key, w.HWND) for key in ("active", "focus", "capture", "menu", "move", "caret")
    ] + [("rect", w.RECT)]


class _LastInput(ctypes.Structure):
    _fields_ = [("size", w.UINT), ("tick", w.DWORD)]


class SceneWindow:
    def __init__(self, pid: int, executable: Path):
        if sys.platform != "win32" or type(pid) is not int or not 0 < pid < 2**32:
            raise ValueError("Invalid Explorer process")
        self.pid = pid
        user, kernel = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
        # Keep a process handle: neither a stale PID nor a recycled HWND can rearm the listener.
        def proc(lib, name, result, *args):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result
            return fn
        self._close = proc(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self._wait = proc(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        self._front = proc(user, "GetForegroundWindow", w.HWND)
        self._owner = proc(user, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
        self._class = proc(user, "GetClassNameW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
        self._gui = proc(user, "GetGUIThreadInfo", w.BOOL, w.DWORD, ctypes.POINTER(_Gui))
        self._last = proc(user, "GetLastInputInfo", w.BOOL, ctypes.POINTER(_LastInput))
        self._key = proc(user, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
        self.handle = proc(kernel, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x101000, False, pid)
        if not self.handle:
            raise ValueError("Explorer process unavailable")
        try:
            size = w.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            query = proc(kernel, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
            if not query(self.handle, 0, path, ctypes.byref(size)) or not executable.samefile(path.value):
                raise ValueError("Explorer executable mismatch")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self._close(self.handle)
            self.handle = None

    def sample(self) -> tuple[int, int] | None:
        hwnd = self._front()
        owner = w.DWORD()
        tid = self._owner(hwnd, ctypes.byref(owner)) if hwnd else 0
        if not tid or owner.value != self.pid or self._wait(self.handle, 0) != 258:
            return None
        gui, name = _Gui(size=ctypes.sizeof(_Gui)), ctypes.create_unicode_buffer(64)
        if (not self._gui(tid, ctypes.byref(gui)) or gui.focus != hwnd or gui.active != hwnd or gui.flags & 0x1e
                or not self._class(hwnd, name, len(name)) or name.value != "UnrealWindow"):
            return None
        stamp = _LastInput(size=ctypes.sizeof(_LastInput))
        if not self._last(ctypes.byref(stamp)) or self._front() != hwnd:
            return None
        # The activity timestamp vetoes chords without reading which ordinary key was typed.
        if any(self._key(k) < 0 for k in (1, 2, 4, 5, 6, VK_TAB, 0x5b, 0x5c)):
            return None
        pressed = [k for k in (VK_ALT, VK_SHIFT, VK_CONTROL) if self._key(k) < 0]
        if len(pressed) > 1 or self._front() != hwnd:
            return None
        return (pressed[0] if pressed else 0, stamp.tick)


class BrushInput:
    def __init__(self):
        self._lock = Lock()
        self._session = ""
        self._window: SceneWindow | None = None
        self._reader: object | None = None

    def bind(self, session: str, pid: int, executable: Path):
        window = SceneWindow(pid, executable)
        with self._lock:
            if self._window:
                self._window.close()
            self._session, self._window, self._reader = session, window, None

    def clear(self, session: str | None = None):
        with self._lock:
            if session is not None and self._session != session:
                return
            if self._window:
                self._window.close()
            self._session, self._window, self._reader = "", None, None

    def wait(self, session: str, mode: str, disconnected: Callable[[], bool]) -> dict:
        if mode not in ("edit", "roam"):
            raise ValueError("Invalid brush mode")
        reader, tap = object(), Tap()
        with self._lock:
            if not self._window or self._session != session:
                return {"available": False}
            self._reader = reader
        deadline = monotonic() + 8
        try:
            while monotonic() < deadline + (0.6 if tap.held else 0) and not disconnected():
                with self._lock:
                    if self._reader is not reader or self._window is None:
                        break
                    state = self._window.sample()
                key = tap.feed(state)
                action = ("edit" if mode == "roam" else "roam") if key == VK_ALT else (
                    {VK_SHIFT: "include", VK_CONTROL: "exclude"}.get(key) if mode == "edit" else None)
                if action:
                    return {"available": True, "action": action}
                sleep(.016 if state is not None or tap.held else .035)
        finally:
            with self._lock:
                if self._reader is reader:
                    self._reader = None
        return {"available": True}
