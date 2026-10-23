"""Versioned project writer locks and short-lived recovery serialization."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ProjectLockedError, ProjectRecoveryError
from .processes import ProcessIdentity


LOCK_SCHEMA = "verdflow.project-lock"
LOCK_VERSION = 1


@dataclass(frozen=True, slots=True)
class LockRecord:
    schema: str
    version: int
    host: str
    pid: int
    process_started_at_token: str
    writer_instance_id: str
    token: str
    opened_at: str

    @classmethod
    def create(
        cls,
        identity: ProcessIdentity,
        writer_instance_id: str,
    ) -> LockRecord:
        return cls(
            schema=LOCK_SCHEMA,
            version=LOCK_VERSION,
            host=identity.host,
            pid=identity.pid,
            process_started_at_token=identity.started_at_token,
            writer_instance_id=writer_instance_id,
            token=uuid.uuid4().hex,
            opened_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def from_json(cls, value: Any) -> LockRecord:
        if not isinstance(value, dict):
            raise ValueError("Lock payload must be an object")
        record = cls(
            schema=str(value["schema"]),
            version=int(value["version"]),
            host=str(value["host"]),
            pid=int(value["pid"]),
            process_started_at_token=str(value["process_started_at_token"]),
            writer_instance_id=str(value["writer_instance_id"]),
            token=str(value["token"]),
            opened_at=str(value["opened_at"]),
        )
        if record.schema != LOCK_SCHEMA or record.version != LOCK_VERSION:
            raise ValueError("Unsupported lock record")
        if record.pid <= 0 or not all(
            (
                record.host,
                record.process_started_at_token,
                record.writer_instance_id,
                record.token,
                record.opened_at,
            )
        ):
            raise ValueError("Incomplete lock record")
        return record

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ProjectLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._token: str | None = None

    def acquire(
        self,
        identity: ProcessIdentity,
        writer_instance_id: str,
    ) -> LockRecord:
        record = LockRecord.create(identity, writer_instance_id)
        payload = json.dumps(
            record.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise ProjectLockedError(str(self.path)) from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        self._token = record.token
        return record

    def read(self) -> LockRecord:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return LockRecord.from_json(payload)
        except FileNotFoundError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ProjectRecoveryError(
                f"Project lock is malformed or from an unsupported version: {self.path}"
            ) from error

    def archive_if_owned(self, record: LockRecord, destination: Path) -> None:
        current = self.read()
        if current.token != record.token:
            raise ProjectRecoveryError(
                "Project lock changed while recovery was being evaluated"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.path, destination)

    def release(self) -> None:
        if self._token is None:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self._token:
                self.path.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        finally:
            self._token = None


class RecoveryGuard(AbstractContextManager["RecoveryGuard"]):
    """Kernel-backed mutex released automatically when the process exits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: Any | None = None

    def __enter__(self) -> RecoveryGuard:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            self._lock(stream)
        except OSError as error:
            stream.close()
            raise ProjectLockedError(
                f"Project lock recovery is already in progress: {self.path}"
            ) from error
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

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
