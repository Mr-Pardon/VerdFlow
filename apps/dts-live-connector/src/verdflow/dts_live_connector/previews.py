"""Connector-owned preview handles; authorization never becomes a global API proxy."""

from threading import RLock
from typing import Protocol

from verdflow.contracts import (
    AssignmentPreviewRequest,
    AssignmentPreviewResult,
    StrategyPreviewRequest,
    StrategyPreviewOperation,
)
from verdflow.system_host import DtsLiveAuthorizationError


class PreviewBackend(Protocol):
    def submit_strategy_preview(
        self, request: StrategyPreviewRequest
    ) -> StrategyPreviewOperation: ...
    def get_strategy_preview(self, preview_id: str) -> StrategyPreviewOperation: ...
    def cancel_strategy_preview(self, preview_id: str) -> StrategyPreviewOperation: ...
    def preview_asset_assignments(
        self, request: AssignmentPreviewRequest
    ) -> AssignmentPreviewResult: ...


class ConnectorPreviews:
    def __init__(self, backend: PreviewBackend) -> None:
        self._backend = backend
        self._lock = RLock()
        self._owners: dict[str, str] = {}
        self._epochs: dict[str, int] = {}
        self._pending: dict[str, int] = {}
        self._generation = 0
        self._closed = False

    def reopen(self) -> None:
        with self._lock:
            self._closed = False

    def submit(
        self, session_id: str, request: StrategyPreviewRequest
    ) -> StrategyPreviewOperation:
        with self._lock:
            if self._closed:
                raise DtsLiveAuthorizationError("Preview service is closed")
            count = sum(owner == session_id for owner in self._owners.values())
            if count + self._pending.get(session_id, 0) >= 4:
                raise ValueError("Too many session previews")
            epoch = self._epochs.get(session_id, 0)
            generation = self._generation
            self._pending[session_id] = self._pending.get(session_id, 0) + 1
        try:
            operation = self._backend.submit_strategy_preview(request)
        finally:
            with self._lock:
                self._pending[session_id] -= 1
                if not self._pending[session_id]:
                    self._pending.pop(session_id)
        with self._lock:
            stale = (
                self._closed
                or generation != self._generation
                or epoch != self._epochs.get(session_id, 0)
            )
            if not stale:
                self._owners[operation.preview_id] = session_id
        if stale:
            self._backend.cancel_strategy_preview(operation.preview_id)
            raise DtsLiveAuthorizationError("Preview session was closed")
        return operation

    def get(self, session_id: str, preview_id: str) -> StrategyPreviewOperation:
        with self._lock:
            if self._owners.get(preview_id) != session_id:
                raise DtsLiveAuthorizationError(
                    "Preview does not belong to this session"
                )
        return self._backend.get_strategy_preview(preview_id)

    def cancel(self, session_id: str, preview_id: str) -> StrategyPreviewOperation:
        with self._lock:
            owner = self._owners.get(preview_id)
            if owner is not None and owner != session_id:
                raise DtsLiveAuthorizationError(
                    "Preview does not belong to this session"
                )
            self._owners.pop(preview_id, None)
        return (
            self._backend.cancel_strategy_preview(preview_id)
            if owner
            else StrategyPreviewOperation(preview_id, "", "cancelled")
        )

    def assignments(self, request: AssignmentPreviewRequest) -> AssignmentPreviewResult:
        return self._backend.preview_asset_assignments(request)

    def close_session(self, session_id: str) -> None:
        with self._lock:
            self._epochs[session_id] = self._epochs.get(session_id, 0) + 1
            ids = [key for key, owner in self._owners.items() if owner == session_id]
            for key in ids:
                self._owners.pop(key)
        self._cancel_all(ids)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            ids = list(self._owners)
            self._owners.clear()
        self._cancel_all(ids)

    def _cancel_all(self, ids: list[str]) -> None:
        for key in ids:
            try:
                self._backend.cancel_strategy_preview(key)
            except (ValueError, RuntimeError):
                # The SystemHost owns final cleanup if it is already shutting down.
                pass
