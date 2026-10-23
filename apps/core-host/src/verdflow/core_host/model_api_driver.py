"""Core WorkerDriver mapping for the Model API supervisor."""

from __future__ import annotations

from verdflow.contracts import JobInfo
from verdflow.core import WorkerDescriptor
from verdflow.model_api import MODEL_API_SUPPORTED_COMMANDS, ModelApiWorkerSupervisor


class ModelApiWorkerDriver:
    def __init__(self, supervisor: ModelApiWorkerSupervisor) -> None:
        self.supervisor = supervisor
        self._descriptor = WorkerDescriptor(
            worker_id=supervisor.worker_id,
            command_types=MODEL_API_SUPPORTED_COMMANDS,
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
