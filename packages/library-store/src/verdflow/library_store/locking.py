"""Short-lived kernel lock for schema initialization and migration."""

from __future__ import annotations

from contextlib import AbstractContextManager
import os
from pathlib import Path
from typing import Any


class LibraryKernelGuard(AbstractContextManager["LibraryKernelGuard"]):
    """Serialize a short filesystem or schema mutation across local processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: Any | None = None

    def __enter__(self) -> LibraryKernelGuard:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_guard_byte()
        stream = self.path.open("r+b")
        stream.seek(0)
        try:
            self._lock(stream)
        except Exception:
            stream.close()
            raise
        self._stream = stream
        return self

    def __exit__(self, *_: object) -> None:
        if self._stream is None:
            return
        try:
            self._unlock(self._stream)
        finally:
            self._stream.close()
            self._stream = None

    @staticmethod
    def _lock(stream: Any) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _ensure_guard_byte(self) -> None:
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
