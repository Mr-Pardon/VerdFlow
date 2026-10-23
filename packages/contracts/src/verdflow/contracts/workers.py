"""External worker process provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkerProcessStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    EXITED = "exited"
    TERMINATED = "terminated"
    LOST = "lost"


@dataclass(frozen=True, slots=True)
class WorkerProcessInfo:
    job_id: str
    attempt_number: int
    adapter_id: str
    host: str
    pid: int
    started_at_token: str
    status: WorkerProcessStatus
    started_at: str
    heartbeat_at: str | None
    finished_at: str | None
    exit_code: int | None
