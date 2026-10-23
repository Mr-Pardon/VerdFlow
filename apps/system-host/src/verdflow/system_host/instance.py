"""Process singleton and endpoint discovery for one SystemHost system root."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from concurrent.futures import Future
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from threading import Event, Thread
import time
from typing import BinaryIO
from urllib.parse import urlsplit
from uuid import uuid4

from verdflow.project_store import ProcessIdentity


SYSTEM_ENDPOINT_SCHEMA = "verdflow.system-endpoint"
SYSTEM_ENDPOINT_VERSION = "1"
SYSTEM_API_PROTOCOL_VERSION = "1"
MAX_ENDPOINT_BYTES = 64 * 1024
_ERROR_ALREADY_EXISTS = 183


@dataclass(frozen=True, slots=True)
class SystemEndpointInfo:
    schema: str
    version: str
    endpoint_id: str
    instance_id: str
    system_root_id: str
    host: str
    pid: int
    process_started_at_token: str
    base_url: str
    protocol_version: str
    published_at: str

    @classmethod
    def create(
        cls,
        *,
        endpoint_id: str,
        instance_id: str,
        system_root_id: str,
        process_identity: ProcessIdentity,
        base_url: str,
        protocol_version: str,
    ) -> SystemEndpointInfo:
        return cls(
            schema=SYSTEM_ENDPOINT_SCHEMA,
            version=SYSTEM_ENDPOINT_VERSION,
            endpoint_id=_required_text(endpoint_id, "endpoint_id"),
            instance_id=_required_text(instance_id, "instance_id"),
            system_root_id=_required_text(system_root_id, "system_root_id"),
            host=_required_text(process_identity.host, "host"),
            pid=process_identity.pid,
            process_started_at_token=_required_text(
                process_identity.started_at_token,
                "process_started_at_token",
            ),
            base_url=_validate_base_url(base_url),
            protocol_version=_required_text(
                protocol_version,
                "protocol_version",
            ),
            published_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def from_document(cls, document: object) -> SystemEndpointInfo:
        if not isinstance(document, dict):
            raise ValueError("System endpoint must be an object")
        if document.get("schema") != SYSTEM_ENDPOINT_SCHEMA:
            raise ValueError("System endpoint schema is unsupported")
        if document.get("version") != SYSTEM_ENDPOINT_VERSION:
            raise ValueError("System endpoint version is unsupported")
        pid = document.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("System endpoint pid is invalid")
        return cls(
            schema=SYSTEM_ENDPOINT_SCHEMA,
            version=SYSTEM_ENDPOINT_VERSION,
            endpoint_id=_required_text(document.get("endpoint_id"), "endpoint_id"),
            instance_id=_required_text(document.get("instance_id"), "instance_id"),
            system_root_id=_required_text(
                document.get("system_root_id"),
                "system_root_id",
            ),
            host=_required_text(document.get("host"), "host"),
            pid=pid,
            process_started_at_token=_required_text(
                document.get("process_started_at_token"),
                "process_started_at_token",
            ),
            base_url=_validate_base_url(document.get("base_url")),
            protocol_version=_required_text(
                document.get("protocol_version"),
                "protocol_version",
            ),
            published_at=_required_text(
                document.get("published_at"),
                "published_at",
            ),
        )

    def to_document(self) -> dict[str, object]:
        return asdict(self)


class SystemEndpointStore:
    """Publish discoverable transport metadata without owning the singleton."""

    def __init__(self, system_root: Path) -> None:
        self.root = system_root.resolve()
        self.path = self.root / "system-endpoint.json"

    def read(self) -> SystemEndpointInfo | None:
        if not self.path.is_file():
            return None
        if self.path.stat().st_size > MAX_ENDPOINT_BYTES:
            raise ValueError("System endpoint exceeds size limit")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("System endpoint is not valid UTF-8 JSON") from error
        endpoint = SystemEndpointInfo.from_document(document)
        if endpoint.system_root_id != system_root_id(self.root):
            raise ValueError("System endpoint belongs to another system root")
        return endpoint

    def publish(self, endpoint: SystemEndpointInfo) -> None:
        if endpoint.system_root_id != system_root_id(self.root):
            raise ValueError("System endpoint belongs to another system root")
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            endpoint.to_document(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_ENDPOINT_BYTES:
            raise ValueError("System endpoint exceeds size limit")
        temporary = self.root / f".{self.path.name}.{uuid4().hex}.tmp"
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _replace_with_retry(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self, *, instance_id: str, endpoint_id: str) -> bool:
        try:
            current = self.read()
        except ValueError:
            return False
        if current is None:
            return False
        if (
            current.instance_id != instance_id
            or current.endpoint_id != endpoint_id
        ):
            return False
        self.path.unlink(missing_ok=True)
        return True

    def clear_stale(self) -> None:
        self.path.unlink(missing_ok=True)


class SystemHostInstanceGuard:
    """Hold one process-level singleton for a normalized system root."""

    def __init__(self, system_root: Path) -> None:
        self.root = system_root.resolve()
        self._release_event = Event()
        self._thread: Thread | None = None
        self._ready: Future[bool] | None = None
        self._stream: BinaryIO | None = None

    def acquire(self, timeout_seconds: float = 10.0) -> bool:
        if self._thread is not None or self._stream is not None:
            raise RuntimeError("SystemHost instance guard is already acquired")
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            self._ready = Future()
            self._thread = Thread(
                target=self._hold_windows_mutex,
                name="verdflow-system-instance-guard",
                daemon=True,
            )
            self._thread.start()
            acquired = self._ready.result(timeout=timeout_seconds)
            if not acquired:
                self._thread.join(timeout_seconds)
                self._thread = None
                self._ready = None
            return acquired
        return self._acquire_posix_lock()

    def release(self, timeout_seconds: float = 10.0) -> None:
        thread = self._thread
        if thread is not None:
            self._release_event.set()
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise TimeoutError("SystemHost instance guard did not stop")
            self._thread = None
            self._ready = None
            return
        stream = self._stream
        if stream is not None:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
            self._stream = None

    def _hold_windows_mutex(self) -> None:
        assert self._ready is not None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(
            None,
            True,
            _windows_mutex_name(self.root),
        )
        if not handle:
            self._ready.set_exception(
                OSError(
                    ctypes.get_last_error(),
                    "Could not create the SystemHost singleton mutex",
                )
            )
            return
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self._ready.set_result(False)
            return
        self._ready.set_result(True)
        self._release_event.wait()
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)

    def _acquire_posix_lock(self) -> bool:
        import fcntl

        stream = (self.root / ".system-host.lock").open("a+b")
        try:
            fcntl.flock(
                stream.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            stream.close()
            return False
        self._stream = stream
        return True


def discover_system_endpoint(system_root: Path) -> SystemEndpointInfo | None:
    return SystemEndpointStore(system_root).read()


def system_root_id(system_root: Path) -> str:
    normalized = str(system_root.resolve()).casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _windows_mutex_name(system_root: Path) -> str:
    return f"Global\\VerdFlow.SystemHost.{system_root_id(system_root)}"


def _validate_base_url(value: object) -> str:
    text = _required_text(value, "base_url")
    parsed = urlsplit(text)
    if parsed.scheme != "http":
        raise ValueError("System endpoint base_url must use HTTP loopback")
    if parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("System endpoint base_url must use a loopback host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("System endpoint base_url contains unsupported parts")
    if parsed.path not in {"", "/"}:
        raise ValueError("System endpoint base_url cannot contain a path")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("System endpoint base_url port is invalid") from error
    if port is None:
        raise ValueError("System endpoint base_url requires an explicit port")
    return text.rstrip("/")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _replace_with_retry(source: Path, target: Path) -> None:
    for attempt in range(20):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)
