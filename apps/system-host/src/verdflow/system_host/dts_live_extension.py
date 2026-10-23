"""CoreHost extension composition for one ephemeral DTS Live session."""

from __future__ import annotations

from dataclasses import dataclass

from verdflow.core import (
    ElevationProviderDefinition,
    JobRuntime,
    RegionExtractionProviderDefinition,
)
from verdflow.core_host import CoreHostExtension
from verdflow.dts_adapter import (
    DTS_LIVE_ADAPTER_ID,
    DTS_LIVE_ADAPTER_VERSION,
    DTS_LIVE_ELEVATION_COMMAND,
    DTS_LIVE_REGION_SELECTION_COMMAND,
    DtsLiveCoreWorker,
)

from .dts_live_connector import DtsLiveTrustedSessionPort


@dataclass(frozen=True, slots=True)
class DtsLiveCoreHostExtensionFactory:
    """Bind exactly one Connector session to one temporary CoreHost."""

    port: DtsLiveTrustedSessionPort
    callback_poll_seconds: float = 1.0

    def build(self, runtime: JobRuntime) -> CoreHostExtension:
        worker = DtsLiveCoreWorker(
            runtime,
            self.port,
            callback_poll_seconds=self.callback_poll_seconds,
        )
        return CoreHostExtension(
            worker_drivers=(worker,),
            region_extraction_providers=(
                RegionExtractionProviderDefinition(
                    provider_id=DTS_LIVE_ADAPTER_ID,
                    provider_version=DTS_LIVE_ADAPTER_VERSION,
                    command_type=DTS_LIVE_REGION_SELECTION_COMMAND,
                    command_version="1",
                    max_attempts=1,
                ),
            ),
            elevation_providers=(
                ElevationProviderDefinition(
                    provider_id=DTS_LIVE_ADAPTER_ID,
                    provider_version=DTS_LIVE_ADAPTER_VERSION,
                    command_type=DTS_LIVE_ELEVATION_COMMAND,
                    command_version="1",
                    max_attempts=1,
                ),
            ),
        )
