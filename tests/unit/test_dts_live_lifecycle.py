from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
    str(ROOT / "apps" / "dts-live-connector" / "src"),
]

from verdflow.dts_live_connector import (  # noqa: E402
    DtsLiveLaunch,
    DtsLiveLifecycleService,
)


@dataclass(frozen=True)
class _Runtime:
    available: bool
    resolved_path: str | None


class _System:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtime_reads = 0

    def get_external_runtime(self, runtime_id: str) -> _Runtime:
        self.runtime_reads += 1
        self.last_runtime_id = runtime_id
        return _Runtime(True, str(self.root))


class _Application:
    def __init__(self, callbacks: dict[str, object]) -> None:
        self.base_url = "http://127.0.0.1:54321"
        self.callbacks = callbacks
        self.start_count = 0
        self.launch_count = 0
        self.closed = False
        self.discarded: list[str] = []

    def start(self) -> str:
        self.start_count += 1
        return self.base_url

    def create_launch(self) -> DtsLiveLaunch:
        self.launch_count += 1
        return DtsLiveLaunch(
            connector_session_id=f"session-{self.launch_count}",
            url=f"{self.base_url}/#session=session-{self.launch_count}",
            expires_at="2026-08-20T12:00:00Z",
        )

    def discard_session(self, session_id: str) -> None:
        self.discarded.append(session_id)

    def close(self) -> None:
        self.closed = True


class DtsLiveLifecycleTests(unittest.TestCase):
    def test_status_is_side_effect_free_and_start_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            installation = _installation(Path(temporary))
            system = _System(installation)
            applications: list[_Application] = []

            def factory(_system: object, **callbacks: object) -> _Application:
                application = _Application(callbacks)
                applications.append(application)
                return application

            lifecycle = DtsLiveLifecycleService(
                system,  # type: ignore[arg-type]
                application_factory=factory,  # type: ignore[arg-type]
            )

            initial = lifecycle.status()
            self.assertEqual(initial.state, "stopped")
            self.assertEqual(system.runtime_reads, 0)
            self.assertEqual(applications, [])

            started = lifecycle.start()
            repeated = lifecycle.start()
            self.assertEqual(started.state, "waiting_for_explorer")
            self.assertEqual(repeated.connector_session_id, "session-1")
            self.assertEqual(system.runtime_reads, 1)
            self.assertEqual(len(applications), 1)
            self.assertEqual(applications[0].start_count, 1)
            self.assertEqual(applications[0].launch_count, 1)

            connected = applications[0].callbacks["on_session_connected"]
            assert callable(connected)
            connected("session-1")
            self.assertEqual(lifecycle.status().state, "connected")

            stopped = lifecycle.stop()
            self.assertEqual(stopped.state, "stopped")
            self.assertTrue(applications[0].closed)
            self.assertIsNone(stopped.connector_base_url)

    def test_relaunch_replaces_only_the_temporary_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            installation = _installation(Path(temporary))
            application: _Application | None = None

            def factory(_system: object, **callbacks: object) -> _Application:
                nonlocal application
                application = _Application(callbacks)
                return application

            lifecycle = DtsLiveLifecycleService(
                _System(installation),  # type: ignore[arg-type]
                application_factory=factory,  # type: ignore[arg-type]
            )
            lifecycle.start()
            relaunched = lifecycle.relaunch()

            assert application is not None
            self.assertEqual(application.discarded, ["session-1"])
            self.assertEqual(relaunched.connector_session_id, "session-2")
            self.assertEqual(application.start_count, 1)


def _installation(root: Path) -> Path:
    (root / "SDK").mkdir(parents=True)
    (root / "SDK" / "ac_conf.js").write_text("", encoding="utf-8")
    (root / "SDK" / "ac.min.js").write_text("", encoding="utf-8")
    executable = (
        root
        / "AirCityExplorer"
        / "Binaries"
        / "Win64"
        / "DTS Explorer.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    return root


if __name__ == "__main__":
    unittest.main()
