"""Stable command identities and local runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


GEOSPATIAL_ADAPTER_ID = "verdflow.geospatial"
GEOSPATIAL_PREPARE_REGIONS_COMMAND = "verdflow.geospatial.prepare-regions"
GEOSPATIAL_SAMPLE_ELEVATION_COMMAND = "verdflow.geospatial.sample-elevation"
GEOSPATIAL_SUPPORTED_COMMANDS = (
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
)
GEOSPATIAL_PROTOCOL_VERSION = "1"


@dataclass(frozen=True, slots=True)
class GeospatialWorkerConfig:
    runtime_root: Path
    cache_root: Path
    python_executable: Path = Path(sys.executable)
    timeout_seconds: float = 300.0
    heartbeat_interval_seconds: float = 2.0
    poll_interval_seconds: float = 0.1
    termination_grace_seconds: float = 3.0

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.heartbeat_interval_seconds >= self.timeout_seconds:
            raise ValueError("heartbeat interval must be below timeout")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.termination_grace_seconds <= 0:
            raise ValueError("termination_grace_seconds must be positive")
