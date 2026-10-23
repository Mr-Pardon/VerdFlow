"""Core WorkerDriver mapping for the Blender supervisor."""

from __future__ import annotations

from verdflow.blender_worker import (
    BLENDER_SUPPORTED_COMMANDS,
    BlenderWorkerSupervisor,
)
from verdflow.contracts import JobInfo
from verdflow.core import WorkerDescriptor


class BlenderWorkerDriver:
    def __init__(self, supervisor: BlenderWorkerSupervisor) -> None:
        self.supervisor = supervisor
        self._descriptor = WorkerDescriptor(
            worker_id=supervisor.worker_id,
            command_types=BLENDER_SUPPORTED_COMMANDS,
        )

    @property
    def descriptor(self) -> WorkerDescriptor:
        return self._descriptor

    def run_once(self) -> JobInfo | None:
        return self.supervisor.run_once()

    def reconcile(self) -> int:
        return self.supervisor.reconcile_recorded_processes()

    def request_cancel(self, job_id: str) -> None:
        self.supervisor.request_cancel(job_id)
