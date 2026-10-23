"""Versioned Strategy Worker command and supervisor configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile

from verdflow.contracts import (
    PLANTING_GENERATE_POINTS_COMMAND,
    PLANTING_GENERATION_COMMAND_SCHEMA,
    PLANTING_GENERATION_COMMAND_VERSION,
)

from .runner import StrategyWorkerConfig


STRATEGY_GENERATE_POINTS_COMMAND = PLANTING_GENERATE_POINTS_COMMAND
STRATEGY_SUPPORTED_COMMANDS = (STRATEGY_GENERATE_POINTS_COMMAND,)
STRATEGY_WORKER_ADAPTER_ID = "verdflow-strategy-worker"
STRATEGY_COMMAND_SCHEMA = PLANTING_GENERATION_COMMAND_SCHEMA
STRATEGY_COMMAND_VERSION = PLANTING_GENERATION_COMMAND_VERSION


@dataclass(frozen=True, slots=True)
class StrategyWorkerSupervisorConfig:
    worker: StrategyWorkerConfig = field(default_factory=StrategyWorkerConfig)
    heartbeat_interval_seconds: float = 5.0
    runtime_root: Path = field(
        default_factory=lambda: (
            Path(tempfile.gettempdir()) / "verdflow-strategy-runtime"
        )
    )

    def validate(self) -> None:
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.runtime_root.exists() and not self.runtime_root.is_dir():
            raise ValueError("runtime_root must be a directory")
