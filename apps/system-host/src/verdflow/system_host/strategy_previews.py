"""Bounded in-memory lifecycle for display previews, separate from project jobs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import BoundedSemaphore, Event, RLock, Thread
import time
from typing import Callable
from uuid import uuid4

from verdflow.contracts import StrategyPreviewOperation, StrategyPreviewResult
from verdflow.strategy_worker import StrategyExecutionCancelled


@dataclass
class _Preview:
    operation: StrategyPreviewOperation
    cancelled: Event
    created: float
    thread: Thread | None = None


class StrategyPreviewRuntime:
    def __init__(self) -> None:
        self._lock = RLock()
        self._slots = BoundedSemaphore(2)
        self._items: dict[str, _Preview] = {}
        self._closed = False

    def submit(
        self,
        package_hash: str,
        generate: Callable[[Callable[[], bool]], StrategyPreviewResult],
    ) -> StrategyPreviewOperation:
        with self._lock:
            if self._closed:
                raise ValueError("Example preview runtime is closed")
            self._prune()
            if (
                sum(
                    item.thread is not None and item.thread.is_alive()
                    for item in self._items.values()
                )
                >= 8
            ):
                raise ValueError("Too many example previews; retry shortly")
            operation = StrategyPreviewOperation(uuid4().hex, package_hash, "pending")
            item = _Preview(operation, Event(), time.monotonic())
            item.thread = Thread(
                target=self._run,
                args=(item, generate),
                daemon=True,
                name="verdflow-example-preview",
            )
            self._items[operation.preview_id] = item
            item.thread.start()
            return operation

    def get(self, preview_id: str) -> StrategyPreviewOperation:
        with self._lock:
            self._prune()
            item = self._items.get(preview_id)
            if item is None:
                raise ValueError("Example preview has expired")
            return item.operation

    def cancel(self, preview_id: str) -> StrategyPreviewOperation:
        with self._lock:
            item = self._items.get(preview_id)
            if item is None:
                return StrategyPreviewOperation(preview_id, "", "cancelled")
            item.cancelled.set()
            item.operation = replace(item.operation, status="cancelled", result=None)
            return item.operation

    def close(self) -> None:
        with self._lock:
            self._closed = True
            items = tuple(self._items.values())
            for item in items:
                item.cancelled.set()
        for item in items:
            if item.thread is not None:
                item.thread.join(5.0)
                if item.thread.is_alive():
                    raise TimeoutError("Example preview did not stop")
        with self._lock:
            self._items.clear()

    def _prune(self) -> None:
        terminal = [
            item
            for item in self._items.values()
            if item.operation.status not in {"pending", "running"}
            and (item.thread is None or not item.thread.is_alive())
        ]
        for index, item in enumerate(
            sorted(terminal, key=lambda item: item.created, reverse=True)
        ):
            if index >= 4 or time.monotonic() - item.created > 180:
                self._items.pop(item.operation.preview_id, None)

    def _run(
        self,
        item: _Preview,
        generate: Callable[[Callable[[], bool]], StrategyPreviewResult],
    ) -> None:
        acquired = False
        try:
            while not acquired:
                if item.cancelled.is_set():
                    raise StrategyExecutionCancelled("Example preview cancelled")
                acquired = self._slots.acquire(timeout=0.05)
            with self._lock:
                if item.cancelled.is_set():
                    raise StrategyExecutionCancelled("Example preview cancelled")
                item.operation = replace(item.operation, status="running")
            result = generate(item.cancelled.is_set)
            with self._lock:
                if not item.cancelled.is_set():
                    item.operation = replace(
                        item.operation, status="succeeded", result=result
                    )
        except StrategyExecutionCancelled:
            with self._lock:
                item.operation = replace(
                    item.operation, status="cancelled", result=None
                )
        except Exception as error:
            with self._lock:
                if not item.cancelled.is_set():
                    item.operation = replace(
                        item.operation, status="failed", error_message=str(error)
                    )
        finally:
            if acquired:
                self._slots.release()
