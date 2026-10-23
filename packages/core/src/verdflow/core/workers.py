"""Worker capability registration independent of concrete adapters."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from verdflow.contracts import JobInfo


@dataclass(frozen=True, slots=True)
class WorkerDescriptor:
    worker_id: str
    command_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerReconciliation:
    worker_id: str
    recovered_process_count: int


class WorkerDriver(Protocol):
    @property
    def descriptor(self) -> WorkerDescriptor: ...

    def run_once(self) -> JobInfo | None: ...

    def reconcile(self) -> int: ...

    def request_cancel(self, job_id: str) -> None: ...


class WorkerRegistry:
    """Own deterministic command ownership without executing adapter logic."""

    def __init__(self) -> None:
        self._drivers: list[WorkerDriver] = []
        self._driver_by_id: dict[str, WorkerDriver] = {}
        self._driver_by_command: dict[str, WorkerDriver] = {}
        self._sealed = False
        self._lock = RLock()

    def register(self, driver: WorkerDriver) -> None:
        descriptor = driver.descriptor
        self._require_text(descriptor.worker_id, "worker_id")
        if not descriptor.command_types:
            raise ValueError("worker command_types cannot be empty")
        if len(set(descriptor.command_types)) != len(descriptor.command_types):
            raise ValueError("worker command_types cannot contain duplicates")
        for command_type in descriptor.command_types:
            self._require_text(command_type, "command_type")

        with self._lock:
            if self._sealed:
                raise RuntimeError("WorkerRegistry is sealed")
            if descriptor.worker_id in self._driver_by_id:
                raise ValueError(
                    f"Worker id is already registered: {descriptor.worker_id}"
                )
            conflicts = [
                command_type
                for command_type in descriptor.command_types
                if command_type in self._driver_by_command
            ]
            if conflicts:
                raise ValueError(
                    "Command type already has an owner: " + ", ".join(conflicts)
                )
            self._drivers.append(driver)
            self._driver_by_id[descriptor.worker_id] = driver
            for command_type in descriptor.command_types:
                self._driver_by_command[command_type] = driver

    def seal(self) -> None:
        with self._lock:
            self._sealed = True

    def descriptors(self) -> tuple[WorkerDescriptor, ...]:
        with self._lock:
            return tuple(driver.descriptor for driver in self._drivers)

    def reconcile_all(self) -> tuple[WorkerReconciliation, ...]:
        with self._lock:
            drivers = tuple(self._drivers)
        return tuple(
            WorkerReconciliation(
                worker_id=driver.descriptor.worker_id,
                recovered_process_count=driver.reconcile(),
            )
            for driver in drivers
        )

    def run_once(self) -> tuple[JobInfo, ...]:
        with self._lock:
            drivers = tuple(self._drivers)
        completed: list[JobInfo] = []
        for driver in drivers:
            job = driver.run_once()
            if job is not None:
                completed.append(job)
        return tuple(completed)

    def request_cancel(self, job: JobInfo) -> bool:
        with self._lock:
            driver = self._driver_by_command.get(job.command.command_type)
        if driver is None:
            return False
        driver.request_cancel(job.job_id)
        return True

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
