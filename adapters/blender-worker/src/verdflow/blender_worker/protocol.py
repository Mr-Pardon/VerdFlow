"""Versioned Blender worker protocol configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BLENDER_PROBE_COMMAND = "verdflow.blender.probe"
BLENDER_INSPECT_ASSET_LIBRARY_COMMAND = (
    "verdflow.blender.inspect-asset-library"
)
BLENDER_INSPECT_SCENE_COMMAND = "verdflow.blender.inspect-scene"
BLENDER_EXTRACT_REGIONS_COMMAND = "verdflow.blender.extract-regions"
BLENDER_SAMPLE_ELEVATION_COMMAND = "verdflow.blender.sample-elevation"
BLENDER_SUPPORTED_COMMANDS = (
    BLENDER_PROBE_COMMAND,
    BLENDER_INSPECT_ASSET_LIBRARY_COMMAND,
    BLENDER_INSPECT_SCENE_COMMAND,
    BLENDER_EXTRACT_REGIONS_COMMAND,
    BLENDER_SAMPLE_ELEVATION_COMMAND,
)
BLENDER_WORKER_ADAPTER_ID = "verdflow-blender-worker"
WORKER_PROTOCOL_VERSION = "1"
CAPABILITY_BUNDLE_VERSION = "1.7.0"


class BlenderWorkerError(RuntimeError):
    """Raised when the external Blender worker cannot complete safely."""


@dataclass(frozen=True, slots=True)
class BlenderWorkerConfig:
    blender_executable: Path
    runtime_root: Path
    scene_cache_root: Path | None = None
    poll_interval_seconds: float = 0.1
    ready_timeout_seconds: float = 20.0
    heartbeat_timeout_seconds: float = 5.0
    blocking_operation_timeout_seconds: float = 300.0
    execution_timeout_seconds: float = 600.0
    termination_grace_seconds: float = 3.0

    def validate(self) -> None:
        if not self.blender_executable.is_file():
            raise BlenderWorkerError(
                f"Blender executable was not found: {self.blender_executable}"
            )
        for field, value in (
            ("poll_interval_seconds", self.poll_interval_seconds),
            ("ready_timeout_seconds", self.ready_timeout_seconds),
            ("heartbeat_timeout_seconds", self.heartbeat_timeout_seconds),
            (
                "blocking_operation_timeout_seconds",
                self.blocking_operation_timeout_seconds,
            ),
            ("execution_timeout_seconds", self.execution_timeout_seconds),
            ("termination_grace_seconds", self.termination_grace_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{field} must be positive")
