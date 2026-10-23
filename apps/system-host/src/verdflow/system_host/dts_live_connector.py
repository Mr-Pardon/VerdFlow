"""System-scoped lifecycle and authorization for DTS Live connector sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import hmac
import secrets
from threading import RLock
import time
from typing import Callable, Sequence

from verdflow.dts_adapter import (
    DtsElevationSample,
    DtsLiveElevationBatch,
    DtsLiveCommand,
    DtsLiveCorrelation,
    DtsLiveDeliveryReceipt,
    DtsLiveExecutionSnapshot,
    DtsLiveOperationStage,
    DtsLiveSelection,
    DtsLiveSession,
    DtsLiveSessionSnapshot,
)

from .models import DtsLiveConfigurationRequest


_TERMINAL_STAGES = {
    DtsLiveOperationStage.COMPLETED,
    DtsLiveOperationStage.CANCELLED,
    DtsLiveOperationStage.SUPERSEDED,
    DtsLiveOperationStage.FAILED,
}


class DtsLiveConnectorError(RuntimeError):
    """Base failure for the SystemHost-owned DTS Live connector."""


class DtsLiveSessionNotFoundError(DtsLiveConnectorError):
    pass


class DtsLiveAuthorizationError(DtsLiveConnectorError):
    pass


class DtsLiveOperationBusyError(DtsLiveConnectorError):
    pass


@dataclass(frozen=True, slots=True)
class DtsLiveBootstrap:
    connector_session_id: str
    bootstrap_nonce: str = field(repr=False)
    expires_at: str


@dataclass(frozen=True, slots=True)
class DtsLiveAccessGrant:
    connector_session_id: str
    access_token: str = field(repr=False)
    expires_at: str


@dataclass(slots=True)
class _ManagedSession:
    session: DtsLiveSession
    created_at: float
    expires_at: float
    bootstrap_digest: bytes | None
    bootstrap_expires_at: float
    access_digest: bytes | None = None
    begin_pending: bool = False


class DtsLiveConnectorManager:
    """Own ephemeral sessions without persisting tokens or project history."""

    def __init__(
        self,
        execution_resolver: Callable[
            [DtsLiveConfigurationRequest],
            DtsLiveExecutionSnapshot,
        ],
        *,
        session_ttl_seconds: float = 4 * 60 * 60,
        bootstrap_ttl_seconds: float = 15 * 60,
        max_sessions: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        if bootstrap_ttl_seconds <= 0:
            raise ValueError("bootstrap_ttl_seconds must be positive")
        if max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self._execution_resolver = execution_resolver
        self._session_ttl_seconds = session_ttl_seconds
        self._bootstrap_ttl_seconds = bootstrap_ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, _ManagedSession] = {}
        self._lock = RLock()

    def create_session(self) -> DtsLiveBootstrap:
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            if len(self._sessions) >= self._max_sessions:
                raise DtsLiveConnectorError("DTS Live session limit reached")
            session = DtsLiveSession()
            nonce = secrets.token_urlsafe(32)
            managed = _ManagedSession(
                session=session,
                created_at=now,
                expires_at=now + self._session_ttl_seconds,
                bootstrap_digest=_digest(nonce),
                bootstrap_expires_at=now + self._bootstrap_ttl_seconds,
            )
            self._sessions[session.connector_session_id] = managed
            return DtsLiveBootstrap(
                connector_session_id=session.connector_session_id,
                bootstrap_nonce=nonce,
                expires_at=_deadline_iso(
                    self._bootstrap_ttl_seconds,
                ),
            )

    def exchange_bootstrap(
        self,
        connector_session_id: str,
        bootstrap_nonce: str,
    ) -> DtsLiveAccessGrant:
        with self._lock:
            managed = self._get(connector_session_id)
            now = self._clock()
            if (
                managed.bootstrap_digest is None
                or now > managed.bootstrap_expires_at
                or not hmac.compare_digest(
                    managed.bootstrap_digest,
                    _digest(bootstrap_nonce),
                )
            ):
                raise DtsLiveAuthorizationError(
                    "DTS Live bootstrap is invalid or expired"
                )
            access_token = secrets.token_urlsafe(48)
            managed.bootstrap_digest = None
            managed.access_digest = _digest(access_token)
            return DtsLiveAccessGrant(
                connector_session_id=connector_session_id,
                access_token=access_token,
                expires_at=_deadline_iso(
                    max(0.0, managed.expires_at - now),
                ),
            )

    def begin_selection(
        self,
        connector_session_id: str,
        access_token: str,
        request: DtsLiveConfigurationRequest,
        regions: object = None,
    ) -> DtsLiveCommand | DtsLiveSelection:
        with self._lock:
            managed = self._authorize(connector_session_id, access_token)
            current = managed.session.snapshot()
            if (
                managed.begin_pending
                or current.stage is not None
                and current.stage not in _TERMINAL_STAGES
            ):
                raise DtsLiveOperationBusyError(
                    "Current DTS Live operation must finish or be cancelled"
                )
            managed.begin_pending = True
        try:
            execution = self._execution_resolver(request)
            with self._lock:
                current_managed = self._authorize(
                    connector_session_id,
                    access_token,
                )
                if current_managed is not managed:
                    raise DtsLiveSessionNotFoundError(
                        "DTS Live session changed during configuration"
                    )
                return managed.session.begin_selection(execution, regions)
        finally:
            with self._lock:
                managed.begin_pending = False

    def regenerate(
        self,
        connector_session_id: str,
        access_token: str,
        request: DtsLiveConfigurationRequest,
    ) -> DtsLiveSelection:
        with self._lock:
            managed = self._authorize(connector_session_id, access_token)
            current = managed.session.snapshot()
            if (
                managed.begin_pending
                or current.stage is None
                or current.stage not in _TERMINAL_STAGES
            ):
                raise DtsLiveOperationBusyError(
                    "Current DTS Live operation must finish before regeneration"
                )
            if current.selection_coordinate_count < 3:
                raise DtsLiveConnectorError(
                    "DTS Live regeneration requires a previous selection"
                )
            managed.begin_pending = True
        try:
            execution = self._execution_resolver(request)
            with self._lock:
                current_managed = self._authorize(
                    connector_session_id,
                    access_token,
                )
                if current_managed is not managed:
                    raise DtsLiveSessionNotFoundError(
                        "DTS Live session changed during configuration"
                    )
                return managed.session.begin_regeneration(execution)
        finally:
            with self._lock:
                managed.begin_pending = False

    def commands_after(
        self,
        connector_session_id: str,
        access_token: str,
        sequence: int,
        *,
        timeout_seconds: float = 0.0,
    ) -> tuple[DtsLiveCommand, ...]:
        managed = self._authorize(connector_session_id, access_token)
        return managed.session.commands_after(
            sequence,
            timeout_seconds=timeout_seconds,
        )

    def accept_selection(
        self,
        connector_session_id: str,
        access_token: str,
        correlation: DtsLiveCorrelation,
        coordinates: object,
    ) -> DtsLiveSelection:
        managed = self._authorize(connector_session_id, access_token)
        return managed.session.accept_selection(correlation, coordinates)

    def request_elevation(
        self,
        connector_session_id: str,
        access_token: str,
        *,
        operation_id: str,
        generation_token: int,
        point_set_id: str,
        point_ids: Sequence[str],
        lines: Sequence[dict[str, object]],
        batches: Sequence[DtsLiveElevationBatch] = (),
    ) -> DtsLiveCommand:
        managed = self._authorize(connector_session_id, access_token)
        return managed.session.request_elevation(
            operation_id=operation_id,
            generation_token=generation_token,
            point_set_id=point_set_id,
            point_ids=point_ids,
            lines=lines,
            batches=batches,
        )

    def accept_elevation(
        self,
        connector_session_id: str,
        access_token: str,
        correlation: DtsLiveCorrelation,
        intersects: object,
        *, failed_regions: object = None, chunked: bool = False,
    ) -> tuple[DtsElevationSample, ...]:
        managed = self._authorize(connector_session_id, access_token)
        return managed.session.accept_elevation(correlation, intersects, failed_regions=failed_regions, chunked=chunked)

    def accept_elevation_chunk(self, connector_session_id: str, access_token: str,
                               correlation: DtsLiveCorrelation, indices: object, intersects: object) -> int:
        return self._authorize(connector_session_id, access_token).session.accept_elevation_chunk(
            correlation, indices, intersects,
        )

    def request_delivery(
        self,
        connector_session_id: str,
        access_token: str,
        *,
        operation_id: str,
        generation_token: int,
        export_id: str,
        xml_path: str,
    ) -> DtsLiveCommand:
        managed = self._authorize(connector_session_id, access_token)
        return managed.session.request_delivery(
            operation_id=operation_id,
            generation_token=generation_token,
            export_id=export_id,
            xml_path=xml_path,
        )

    def accept_delivery(
        self,
        connector_session_id: str,
        access_token: str,
        correlation: DtsLiveCorrelation,
        *,
        export_id: str,
        succeeded: bool,
        message: str | None = None,
    ) -> DtsLiveDeliveryReceipt:
        managed = self._authorize(connector_session_id, access_token)
        return managed.session.accept_delivery(
            correlation,
            export_id=export_id,
            succeeded=succeeded,
            message=message,
        )

    def snapshot(
        self,
        connector_session_id: str,
        access_token: str,
    ) -> DtsLiveSessionSnapshot:
        return self._authorize(
            connector_session_id,
            access_token,
        ).session.snapshot()

    def cancel(
        self,
        connector_session_id: str,
        access_token: str,
    ) -> DtsLiveSessionSnapshot:
        return self._authorize(
            connector_session_id,
            access_token,
        ).session.cancel_current()

    def close_session(
        self,
        connector_session_id: str,
        access_token: str,
    ) -> None:
        self._authorize(connector_session_id, access_token)
        with self._lock:
            self._sessions.pop(connector_session_id, None)

    def close_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def discard_session(self, connector_session_id: str) -> None:
        """Trusted lifecycle cleanup used when runtime preparation fails."""
        with self._lock:
            self._sessions.pop(connector_session_id, None)

    def trusted_port(
        self,
        connector_session_id: str,
    ) -> DtsLiveTrustedSessionPort:
        with self._lock:
            self._get(connector_session_id)
        return DtsLiveTrustedSessionPort(self, connector_session_id)

    def _trusted_session(self, connector_session_id: str) -> DtsLiveSession:
        with self._lock:
            return self._get(connector_session_id).session

    def _authorize(
        self,
        connector_session_id: str,
        access_token: str,
    ) -> _ManagedSession:
        with self._lock:
            managed = self._get(connector_session_id)
            if (
                managed.access_digest is None
                or not hmac.compare_digest(
                    managed.access_digest,
                    _digest(access_token),
                )
            ):
                raise DtsLiveAuthorizationError(
                    "DTS Live session authorization failed"
                )
            return managed

    def _get(self, connector_session_id: str) -> _ManagedSession:
        now = self._clock()
        self._remove_expired(now)
        managed = self._sessions.get(connector_session_id)
        if managed is None:
            raise DtsLiveSessionNotFoundError(
                f"DTS Live session was not found: {connector_session_id}"
            )
        return managed

    def _remove_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, managed in self._sessions.items()
            if now > managed.expires_at
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


def _digest(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        return b""
    return hashlib.sha256(value.encode("utf-8")).digest()


def _deadline_iso(seconds: float) -> str:
    timestamp = datetime.now(UTC).timestamp() + seconds
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


class DtsLiveTrustedSessionPort:
    """Capability passed only to the temporary CoreHost DTS Worker."""

    def __init__(
        self,
        manager: DtsLiveConnectorManager,
        connector_session_id: str,
    ) -> None:
        self._manager = manager
        self.connector_session_id = connector_session_id

    def selection(
        self,
        operation_id: str,
        generation_token: int,
    ) -> DtsLiveSelection:
        return self._session().current_selection(
            operation_id,
            generation_token,
        )

    def execution(
        self,
        operation_id: str,
        generation_token: int,
    ) -> DtsLiveExecutionSnapshot:
        return self._session().current_execution(
            operation_id,
            generation_token,
        )

    def request_elevation(
        self,
        *,
        operation_id: str,
        generation_token: int,
        point_set_id: str,
        point_ids: Sequence[str],
        lines: Sequence[dict[str, object]],
        batches: Sequence[DtsLiveElevationBatch] = (),
    ) -> DtsLiveCommand:
        return self._session().request_elevation(
            operation_id=operation_id,
            generation_token=generation_token,
            point_set_id=point_set_id,
            point_ids=point_ids,
            lines=lines,
            batches=batches,
        )

    def wait_for_elevation(
        self,
        correlation: DtsLiveCorrelation,
        *,
        timeout_seconds: float,
    ) -> tuple[DtsElevationSample, ...] | None:
        return self._session().wait_for_elevation(
            correlation,
            timeout_seconds=timeout_seconds,
        )

    def request_delivery(
        self,
        *,
        operation_id: str,
        generation_token: int,
        export_id: str,
        xml_path: str,
    ) -> DtsLiveCommand:
        return self._session().request_delivery(
            operation_id=operation_id,
            generation_token=generation_token,
            export_id=export_id,
            xml_path=xml_path,
        )

    def wait_for_delivery(
        self,
        correlation: DtsLiveCorrelation,
        *,
        timeout_seconds: float,
    ) -> DtsLiveDeliveryReceipt | None:
        return self._session().wait_for_delivery(
            correlation,
            timeout_seconds=timeout_seconds,
        )

    def cancel(self) -> DtsLiveSessionSnapshot:
        return self._session().cancel_current()

    def fail(
        self,
        operation_id: str,
        generation_token: int,
        message: str,
    ) -> DtsLiveSessionSnapshot:
        return self._session().fail_current(
            operation_id,
            generation_token,
            message,
        )

    def _session(self) -> DtsLiveSession:
        return self._manager._trusted_session(self.connector_session_id)
