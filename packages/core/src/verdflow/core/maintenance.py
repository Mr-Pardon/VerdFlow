"""Deterministic same-thread scheduling for bounded maintenance tasks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from math import isfinite
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class MaintenanceDescriptor:
    task_id: str
    interval_seconds: float
    retry_interval_seconds: float = 60.0
    max_retry_interval_seconds: float = 1800.0
    continuation_interval_seconds: float = 1.0
    run_on_start: bool = True

    def validate(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("maintenance task_id is required")
        if not isfinite(self.interval_seconds) or self.interval_seconds <= 0:
            raise ValueError("maintenance interval_seconds must be positive")
        if (
            not isfinite(self.retry_interval_seconds)
            or self.retry_interval_seconds <= 0
        ):
            raise ValueError(
                "maintenance retry_interval_seconds must be positive"
            )
        if (
            not isfinite(self.max_retry_interval_seconds)
            or self.max_retry_interval_seconds < self.retry_interval_seconds
        ):
            raise ValueError(
                "maintenance max_retry_interval_seconds must be at least "
                "retry_interval_seconds"
            )
        if (
            not isfinite(self.continuation_interval_seconds)
            or self.continuation_interval_seconds <= 0
        ):
            raise ValueError(
                "maintenance continuation_interval_seconds must be positive"
            )


@dataclass(frozen=True, slots=True)
class MaintenanceTaskResult:
    did_work: bool
    has_more: bool = False
    metrics: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class MaintenanceExecution:
    task_id: str
    succeeded: bool
    result: MaintenanceTaskResult | None
    error_type: str | None = None
    error_message: str | None = None
    consecutive_failures: int = 0
    next_run_delay_seconds: float = 0.0


class MaintenanceTask(Protocol):
    @property
    def descriptor(self) -> MaintenanceDescriptor: ...

    def run_once(self) -> MaintenanceTaskResult: ...


@dataclass(slots=True)
class _ScheduledTask:
    task: MaintenanceTask
    next_due: float = 0.0
    consecutive_failures: int = 0
    last_execution: MaintenanceExecution | None = None


class MaintenanceScheduler:
    """Run due tasks without owning threads, timers or persistence."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._monotonic = monotonic
        self._tasks: list[_ScheduledTask] = []
        self._task_ids: set[str] = set()
        self._started = False

    def register(self, task: MaintenanceTask) -> None:
        if self._started:
            raise RuntimeError("MaintenanceScheduler is already started")
        descriptor = task.descriptor
        descriptor.validate()
        if descriptor.task_id in self._task_ids:
            raise ValueError(
                f"Maintenance task is already registered: {descriptor.task_id}"
            )
        self._tasks.append(_ScheduledTask(task))
        self._task_ids.add(descriptor.task_id)

    def start(self) -> None:
        now = self._monotonic()
        for scheduled in self._tasks:
            descriptor = scheduled.task.descriptor
            scheduled.consecutive_failures = 0
            scheduled.next_due = (
                now if descriptor.run_on_start else now + descriptor.interval_seconds
            )
        self._started = True

    def run_due(self) -> tuple[MaintenanceExecution, ...]:
        if not self._started:
            raise RuntimeError("MaintenanceScheduler has not started")
        executions: list[MaintenanceExecution] = []
        for scheduled in self._tasks:
            if self._monotonic() < scheduled.next_due:
                continue
            descriptor = scheduled.task.descriptor
            try:
                result = scheduled.task.run_once()
                if not isinstance(result, MaintenanceTaskResult):
                    raise TypeError(
                        "Maintenance task must return MaintenanceTaskResult"
                    )
            except Exception as error:
                scheduled.consecutive_failures += 1
                retry_delay = descriptor.retry_interval_seconds
                for _ in range(scheduled.consecutive_failures - 1):
                    if retry_delay >= descriptor.max_retry_interval_seconds:
                        break
                    retry_delay = min(
                        retry_delay * 2,
                        descriptor.max_retry_interval_seconds,
                    )
                scheduled.next_due = (
                    self._monotonic() + retry_delay
                )
                execution = MaintenanceExecution(
                    task_id=descriptor.task_id,
                    succeeded=False,
                    result=None,
                    error_type=type(error).__name__,
                    error_message=str(error),
                    consecutive_failures=scheduled.consecutive_failures,
                    next_run_delay_seconds=retry_delay,
                )
                scheduled.last_execution = execution
                executions.append(execution)
            else:
                scheduled.consecutive_failures = 0
                delay = (
                    descriptor.continuation_interval_seconds
                    if result.has_more
                    else descriptor.interval_seconds
                )
                scheduled.next_due = self._monotonic() + delay
                execution = MaintenanceExecution(
                    task_id=descriptor.task_id,
                    succeeded=True,
                    result=result,
                    next_run_delay_seconds=delay,
                )
                scheduled.last_execution = execution
                executions.append(execution)
        return tuple(executions)

    def last_executions(self) -> tuple[MaintenanceExecution, ...]:
        return tuple(
            scheduled.last_execution
            for scheduled in self._tasks
            if scheduled.last_execution is not None
        )
