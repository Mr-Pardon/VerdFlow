"""Explicit, on-demand lifecycle for the DTS Live transport."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Callable

from verdflow.system_host import (
    DTS_EXPLORER_RUNTIME_ID,
    SystemHost,
    resolve_dts_explorer_installation,
)

from .application import DtsLiveConnectorApplication, DtsLiveLaunch


class DtsLiveServiceState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    WAITING_FOR_EXPLORER = "waiting_for_explorer"
    CONNECTED = "connected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DtsLiveServiceEvent:
    sequence: int
    occurred_at: str
    level: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DtsLiveServiceStatus:
    state: str
    connector_base_url: str | None
    connector_session_id: str | None
    launch_url: str | None
    launch_expires_at: str | None
    explorer_executable: str | None
    started_at: str | None
    webui_connected: bool
    last_error: str | None
    events: tuple[DtsLiveServiceEvent, ...]


ApplicationFactory = Callable[..., DtsLiveConnectorApplication]


class DtsLiveLifecycleService:
    """Create the Connector and temporary Core runtime only on explicit start."""

    def __init__(
        self,
        system: SystemHost,
        *,
        application_factory: ApplicationFactory = DtsLiveConnectorApplication,
        event_limit: int = 100,
    ) -> None:
        if event_limit <= 0:
            raise ValueError("event_limit must be positive")
        self._system = system
        self._application_factory = application_factory
        self._events: deque[DtsLiveServiceEvent] = deque(maxlen=event_limit)
        self._lock = RLock()
        self._application: DtsLiveConnectorApplication | None = None
        self._state = DtsLiveServiceState.STOPPED
        self._launch: DtsLiveLaunch | None = None
        self._explorer_executable: Path | None = None
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._sequence = 0

    def status(self) -> DtsLiveServiceStatus:
        """Return current state without resolving paths or starting resources."""
        with self._lock:
            return self._snapshot()

    def start(self) -> DtsLiveServiceStatus:
        with self._lock:
            if self._application is not None:
                if self._launch is None:
                    return self._create_launch_locked()
                return self._snapshot()
            self._state = DtsLiveServiceState.STARTING
            self._last_error = None
            self._record("info", "service_starting", "正在启动 DTS 实时种植服务")
            application: DtsLiveConnectorApplication | None = None
            try:
                runtime = self._system.get_external_runtime(
                    DTS_EXPLORER_RUNTIME_ID
                )
                if not runtime.available or runtime.resolved_path is None:
                    raise ValueError(
                        "DTS Explorer 根目录未配置或不可用，请先前往设置"
                    )
                installation = resolve_dts_explorer_installation(
                    Path(runtime.resolved_path)
                )
                application = self._application_factory(
                    self._system,
                    on_session_connected=self._session_connected,
                    on_session_closed=self._session_closed,
                )
                application.start()
                self._application = application
                self._explorer_executable = installation.executable_path
                self._started_at = _now()
                return self._create_launch_locked()
            except Exception as error:
                if application is not None:
                    application.close()
                self._application = None
                self._launch = None
                self._explorer_executable = None
                self._state = DtsLiveServiceState.FAILED
                self._last_error = str(error)
                self._record("error", "service_start_failed", str(error))
                raise

    def relaunch(self) -> DtsLiveServiceStatus:
        with self._lock:
            if self._application is None:
                return self.start()
            if self._launch is not None:
                self._application.discard_session(
                    self._launch.connector_session_id
                )
                self._record(
                    "info",
                    "session_replaced",
                    "旧的 DTS 实时会话已清理",
                )
                self._launch = None
            return self._create_launch_locked()

    def stop(self) -> DtsLiveServiceStatus:
        with self._lock:
            application = self._application
            self._application = None
            self._launch = None
            self._explorer_executable = None
            self._started_at = None
            self._last_error = None
            if application is not None:
                application.close()
                self._record(
                    "info",
                    "service_stopped",
                    "DTS 实时种植服务已停止，临时会话已清理",
                )
            self._state = DtsLiveServiceState.STOPPED
            return self._snapshot()

    close = stop

    def _create_launch_locked(self) -> DtsLiveServiceStatus:
        if self._application is None:
            raise RuntimeError("DTS Live Connector application is not running")
        self._launch = self._application.create_launch()
        self._state = DtsLiveServiceState.WAITING_FOR_EXPLORER
        self._last_error = None
        self._record(
            "info",
            "session_ready",
            "实时种植会话已就绪，等待 DTS Explorer WebUI 连接",
        )
        return self._snapshot()

    def _session_connected(self, session_id: str) -> None:
        with self._lock:
            if (
                self._launch is None
                or self._launch.connector_session_id != session_id
            ):
                return
            self._state = DtsLiveServiceState.CONNECTED
            self._record(
                "info",
                "explorer_connected",
                "DTS Explorer WebUI 已连接",
            )

    def _session_closed(self, session_id: str) -> None:
        with self._lock:
            if (
                self._launch is None
                or self._launch.connector_session_id != session_id
            ):
                return
            self._launch = None
            self._state = DtsLiveServiceState.WAITING_FOR_EXPLORER
            self._record(
                "warning",
                "session_closed",
                "DTS Explorer WebUI 会话已关闭，可重新创建会话",
            )

    def _record(self, level: str, code: str, message: str) -> None:
        self._sequence += 1
        self._events.append(
            DtsLiveServiceEvent(
                sequence=self._sequence,
                occurred_at=_now(),
                level=level,
                code=code,
                message=message,
            )
        )

    def _snapshot(self) -> DtsLiveServiceStatus:
        launch = self._launch
        application = self._application
        return DtsLiveServiceStatus(
            state=self._state.value,
            connector_base_url=(
                application.base_url if application is not None else None
            ),
            connector_session_id=(
                launch.connector_session_id if launch is not None else None
            ),
            launch_url=launch.url if launch is not None else None,
            launch_expires_at=(launch.expires_at if launch is not None else None),
            explorer_executable=(
                str(self._explorer_executable)
                if self._explorer_executable is not None
                else None
            ),
            started_at=self._started_at,
            webui_connected=self._state is DtsLiveServiceState.CONNECTED,
            last_error=self._last_error,
            events=tuple(self._events),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
