"""Safe stale-lock recovery and durable recovery audit."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .database import ProjectDatabase
from .errors import ProjectLockedError, ProjectRecoveryError
from .layout import ProjectLayout
from .locking import LockRecord, ProjectLock, RecoveryGuard
from .processes import (
    ProcessInspector,
    ProcessStatus,
)


class RecoveryPolicy(StrEnum):
    SAFE = "safe"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    event_id: str
    occurred_at: str
    reason: str
    previous_lock: LockRecord
    archived_lock: Path


class ProjectRecoveryCoordinator:
    def __init__(
        self,
        layout: ProjectLayout,
        inspector: ProcessInspector,
        writer_instance_id: str,
    ) -> None:
        self.layout = layout
        self.inspector = inspector
        self.writer_instance_id = writer_instance_id

    def acquire_writer_lock(
        self,
        lock: ProjectLock,
        policy: RecoveryPolicy,
    ) -> RecoveryOutcome | None:
        identity = self.inspector.current_identity()
        with RecoveryGuard(self.layout.recovery_guard):
            try:
                lock.acquire(identity, self.writer_instance_id)
                return None
            except ProjectLockedError:
                if policy == RecoveryPolicy.REFUSE:
                    raise

            previous = lock.read()
            reason = self._stale_reason(previous)
            event_id = f"recovery_{uuid.uuid4().hex}"
            occurred_at = datetime.now(UTC).isoformat()
            archived = self.layout.recovery_logs / (
                f"{occurred_at.replace(':', '').replace('+', '_')}-"
                f"{event_id}-project.lock.json"
            )
            lock.archive_if_owned(previous, archived)
            try:
                lock.acquire(identity, self.writer_instance_id)
            except Exception:
                if not lock.path.exists() and archived.exists():
                    archived.replace(lock.path)
                raise
            return RecoveryOutcome(
                event_id=event_id,
                occurred_at=occurred_at,
                reason=reason,
                previous_lock=previous,
                archived_lock=archived,
            )

    def _stale_reason(self, record: LockRecord) -> str:
        current_host = self.inspector.current_identity().host
        if record.host.casefold() != current_host.casefold():
            raise ProjectRecoveryError(
                "Automatic recovery refused: project lock belongs to another host"
            )

        probe = self.inspector.probe(record.host, record.pid)
        if probe.status == ProcessStatus.DEAD:
            return "owner_process_not_running"
        if probe.status == ProcessStatus.UNKNOWN or probe.identity is None:
            raise ProjectRecoveryError(
                "Automatic recovery refused: lock owner identity is unknown"
            )
        if (
            probe.identity.started_at_token
            == record.process_started_at_token
        ):
            raise ProjectLockedError(
                f"Project is owned by active process {record.pid}"
            )
        return "pid_reused_by_different_process"

    def restore_previous_lock(
        self,
        lock: ProjectLock,
        outcome: RecoveryOutcome,
    ) -> bool:
        with RecoveryGuard(self.layout.recovery_guard):
            if lock.path.exists() or not outcome.archived_lock.exists():
                return False
            outcome.archived_lock.replace(lock.path)
            return True


class RecoveryAudit:
    def __init__(
        self,
        database: ProjectDatabase,
        layout: ProjectLayout,
    ) -> None:
        self.database = database
        self.layout = layout

    def record_success(
        self,
        outcome: RecoveryOutcome,
        *,
        checked_object_count: int,
        verified_content_hash_count: int,
        removed_object_count: int,
        removed_staging_file_count: int,
    ) -> Path:
        details = {
            "reason": outcome.reason,
            "archived_lock": outcome.archived_lock.relative_to(
                self.layout.root
            ).as_posix(),
            "checked_object_count": checked_object_count,
            "verified_content_hash_count": verified_content_hash_count,
            "removed_object_count": removed_object_count,
            "removed_staging_file_count": removed_staging_file_count,
        }
        previous_lock = outcome.previous_lock.to_dict()
        previous_lock_json = json.dumps(
            previous_lock,
            ensure_ascii=True,
            sort_keys=True,
        )
        details_json = json.dumps(details, ensure_ascii=True, sort_keys=True)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO recovery_events(
                    event_id, occurred_at, event_type,
                    previous_lock_json, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    outcome.event_id,
                    outcome.occurred_at,
                    "stale_lock_recovered",
                    previous_lock_json,
                    details_json,
                ),
            )

        audit_path = self.layout.recovery_logs / (
            f"{outcome.event_id}.audit.json"
        )
        payload = {
            "schema": "verdflow.project-recovery-audit",
            "version": 1,
            "event_id": outcome.event_id,
            "occurred_at": outcome.occurred_at,
            "event_type": "stale_lock_recovered",
            "previous_lock": previous_lock,
            "details": details,
        }
        _write_json_atomic(audit_path, payload)
        return audit_path

    def record_failure(
        self,
        outcome: RecoveryOutcome,
        error: BaseException,
    ) -> Path:
        _write_json_atomic(
            self.layout.recovery_blocker,
            {
                "schema": "verdflow.project-recovery-blocker",
                "version": 1,
                "event_id": outcome.event_id,
                "occurred_at": outcome.occurred_at,
                "reason": "recovery_failed_before_project_open",
            },
        )
        audit_path = self.layout.recovery_logs / (
            f"{outcome.event_id}.failed.audit.json"
        )
        payload = {
            "schema": "verdflow.project-recovery-audit",
            "version": 1,
            "event_id": outcome.event_id,
            "occurred_at": outcome.occurred_at,
            "event_type": "stale_lock_recovery_failed",
            "previous_lock": outcome.previous_lock.to_dict(),
            "details": {
                "reason": outcome.reason,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        }
        _write_json_atomic(audit_path, payload)
        return audit_path

    def clear_blocker(self) -> None:
        self.layout.recovery_blocker.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
