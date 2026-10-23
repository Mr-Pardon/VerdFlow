from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "apps" / "studio-service" / "src"),
]

from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.studio_service import (  # noqa: E402
    StudioProjectQueryRuntime,
    StudioQueryService,
)


class StudioQueryRuntimeTests(unittest.TestCase):
    def test_timed_out_queued_query_is_not_executed_later(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_path = Path(temporary) / "studio-query.verdflow"
            with ProjectStore.create(project_path, "Studio Query"):
                pass
            runtime = StudioProjectQueryRuntime(project_path)
            runtime.start(5.0)
            entered, release, obsolete = Event(), Event(), Event()
            def blocked(_queries):
                entered.set()
                if not release.wait(3.0):
                    raise TimeoutError("test query was not released")
            try:
                with ThreadPoolExecutor(max_workers=1) as callers:
                    active = callers.submit(runtime.call, blocked, timeout_seconds=5.0)
                    try:
                        self.assertTrue(entered.wait(1.0))
                        with self.assertRaises(TimeoutError):
                            runtime.call(lambda _queries: obsolete.set(), timeout_seconds=0.05)
                    finally:
                        release.set()
                    active.result(timeout=1.0)
                    runtime.call(lambda _queries: None, timeout_seconds=1.0)
                    self.assertFalse(obsolete.is_set())
            finally:
                release.set()
                runtime.close(5.0)

    def test_service_does_not_open_project_until_first_studio_query(self) -> None:
        service = StudioQueryService(timeout_seconds=1.0)
        with patch(
            "verdflow.studio_service.runtime.StudioProjectQueryRuntime"
        ) as runtime_type:
            self.assertFalse(runtime_type.called)
            project_path = Path("D:/projects/fixture.verdflow").resolve()
            runtime = runtime_type.return_value
            runtime.project_path = project_path

            first = service._runtime("session_1", project_path)
            second = service._runtime("session_1", project_path)

            self.assertIs(first, runtime)
            self.assertIs(second, runtime)
            runtime.start.assert_called_once_with(1.0)
            service.close_project("session_1")
            runtime.close.assert_called_once_with(1.0)

    def test_runtime_owns_read_connection_outside_system_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_path = Path(temporary) / "studio-query.verdflow"
            with ProjectStore.create(project_path, "Studio Query"):
                pass
            runtime = StudioProjectQueryRuntime(project_path)
            runtime.start(5.0)
            try:
                observed = runtime.call(
                    lambda queries: type(queries).__name__,
                    timeout_seconds=1.0,
                )
                self.assertEqual(observed, "ProjectViewerQueries")
            finally:
                runtime.close(5.0)

    def test_system_host_and_cli_do_not_depend_on_studio_service(self) -> None:
        for relative in (
            Path("apps/system-host/src"),
            Path("apps/cli/src"),
            Path("apps/core-host/src"),
        ):
            for path in (ROOT / relative).rglob("*.py"):
                self.assertNotIn(
                    "verdflow.studio_service",
                    path.read_text(encoding="utf-8"),
                    str(path),
                )


if __name__ == "__main__":
    unittest.main()
