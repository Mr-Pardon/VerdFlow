"""Composition root for the DTS Explorer WebUI and temporary Core session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable
from urllib.parse import urlencode

from verdflow.system_host import (
    DTS_EXPLORER_RUNTIME_ID,
    SystemHost,
    resolve_dts_explorer_installation,
)

from .server import DtsLiveConnectorConfig, DtsLiveConnectorServer
from .region_exchange import RegionExchangeService
from .previews import ConnectorPreviews


@dataclass(frozen=True, slots=True)
class DtsLiveLaunch:
    connector_session_id: str
    url: str
    expires_at: str


class DtsLiveConnectorApplication:
    """Expose only the narrow Explorer surface backed by one SystemHost."""

    def __init__(
        self,
        system: SystemHost,
        config: DtsLiveConnectorConfig | None = None,
        *,
        on_session_connected: Callable[[str], None] | None = None,
        on_session_closed: Callable[[str], None] | None = None,
    ) -> None:
        self._system = system
        base_config = config or DtsLiveConnectorConfig()
        runtime = system.get_external_runtime(DTS_EXPLORER_RUNTIME_ID)
        installation = (
            resolve_dts_explorer_installation(Path(runtime.resolved_path))
            if runtime.available and runtime.resolved_path is not None
            else None
        )
        sdk_root = installation.sdk_root if installation is not None else None
        region_exchange = RegionExchangeService()
        self._previews = ConnectorPreviews(system)
        self._server = DtsLiveConnectorServer(
            system.dts_live_connector,
            DtsLiveConnectorConfig(
                host=base_config.host,
                port=base_config.port,
                request_body_limit_bytes=base_config.request_body_limit_bytes,
                elevation_request_body_limit_bytes=(
                    base_config.elevation_request_body_limit_bytes
                ),
                command_wait_limit_seconds=base_config.command_wait_limit_seconds,
                webui_root=base_config.webui_root,
                sdk_root=sdk_root,
            ),
            on_selection_ready=system.submit_dts_live_selection,
            on_session_connected=on_session_connected,
            on_session_closed=lambda session_id: self._session_closed(
                session_id,
                on_session_closed,
            ),
            configuration_options=system.dts_live_configuration_options,
            asset_search=system.search_asset_catalogs,
            catalog_assets=lambda catalog_id: (
                system.get_asset_catalog_assets(
                    "dts",
                    catalog_id,
                    limit=1000,
                )
            ),
            library_resource=system.read_library_resource,
            profile_detail=system.get_role_binding_profile,
            save_profile=system.save_role_binding_profile,
            export_regions=region_exchange.export_regions,
            import_regions=region_exchange.import_regions,
            previews=self._previews,
        )
        self._sessions: set[str] = set()
        self._lock = RLock()

    def __enter__(self) -> DtsLiveConnectorApplication:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return self._server.base_url

    def start(self) -> str:
        return self._server.start()

    def create_launch(self) -> DtsLiveLaunch:
        self.start()
        bootstrap = self._system.create_dts_live_session()
        with self._lock:
            self._sessions.add(bootstrap.connector_session_id)
        fragment = urlencode(
            {
                "session": bootstrap.connector_session_id,
                "bootstrap": bootstrap.bootstrap_nonce,
            }
        )
        return DtsLiveLaunch(
            connector_session_id=bootstrap.connector_session_id,
            url=f"{self.base_url}/#{fragment}",
            expires_at=bootstrap.expires_at,
        )

    def discard_session(self, session_id: str) -> None:
        self._previews.close_session(session_id)
        with self._lock:
            self._sessions.discard(session_id)
        self._system.discard_dts_live_session(session_id)

    def close(self) -> None:
        self._server.close()
        with self._lock:
            sessions = tuple(self._sessions)
            self._sessions.clear()
        for session_id in sessions:
            self._system.discard_dts_live_session(session_id)

    def _session_closed(
        self,
        session_id: str,
        callback: Callable[[str], None] | None,
    ) -> None:
        with self._lock:
            self._sessions.discard(session_id)
        self._system.close_dts_live_session(session_id)
        if callback is not None:
            callback(session_id)
