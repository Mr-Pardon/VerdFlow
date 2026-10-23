"""Versioned external Model API worker protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
import tempfile


MODEL_API_ANALYZE_MATERIALS_COMMAND = "verdflow.model-api.analyze-materials"
MODEL_API_SUPPORTED_COMMANDS = (MODEL_API_ANALYZE_MATERIALS_COMMAND,)
MODEL_API_WORKER_ADAPTER_ID = "verdflow-model-api-worker"
MODEL_API_WORKER_PROTOCOL_VERSION = "1"
MODEL_API_KEY_ENVIRONMENT = "VERDFLOW_MODEL_API_KEY"


class ModelApiWorkerError(RuntimeError):
    """The supervised Model API attempt could not complete safely."""


@dataclass(frozen=True, slots=True)
class ModelApiWorkerConfig:
    runtime_root: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "verdflow-model-api"
    )
    python_executable: Path = Path(sys.executable)
    poll_interval_seconds: float = 0.1
    ready_timeout_seconds: float = 10.0
    heartbeat_timeout_seconds: float = 15.0
    execution_timeout_seconds: float = 300.0
    request_timeout_seconds: float = 240.0
    heartbeat_interval_seconds: float = 2.0
    termination_grace_seconds: float = 3.0

    def validate(self) -> None:
        if not self.python_executable.is_file():
            raise ModelApiWorkerError(
                f"Python executable was not found: {self.python_executable}"
            )
        for field_name in (
            "poll_interval_seconds",
            "ready_timeout_seconds",
            "heartbeat_timeout_seconds",
            "execution_timeout_seconds",
            "request_timeout_seconds",
            "heartbeat_interval_seconds",
            "termination_grace_seconds",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.request_timeout_seconds >= self.execution_timeout_seconds:
            raise ValueError(
                "request_timeout_seconds must be below execution_timeout_seconds"
            )
        if self.heartbeat_interval_seconds >= self.heartbeat_timeout_seconds:
            raise ValueError(
                "heartbeat_interval_seconds must be below heartbeat_timeout_seconds"
            )
        if self.runtime_root.exists() and not self.runtime_root.is_dir():
            raise ValueError("runtime_root must be a directory")
