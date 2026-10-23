from __future__ import annotations

from io import BytesIO
import importlib.util
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Event, Thread
import textwrap
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path)
    for parent in (ROOT / "apps", ROOT / "adapters", ROOT / "packages")
    for path in parent.glob("*/src")
]

from verdflow.local_api.__main__ import _portable_shutdown_event, _wait_for_shutdown_stream


class _FailingStream:
    def read(self) -> bytes:
        raise OSError("closed")


class PortableShutdownTests(unittest.TestCase):
    def test_shutdown_pipe_watcher_is_opt_in(self) -> None:
        with (
            patch.dict(os.environ, {"VERDFLOW_SHUTDOWN_ON_STDIN_CLOSE": "0"}),
            patch("verdflow.local_api.__main__.Thread") as thread,
        ):
            event = _portable_shutdown_event()
        self.assertFalse(event.is_set())
        thread.assert_not_called()

    def test_stdin_eof_requests_shutdown(self) -> None:
        event = Event()
        _wait_for_shutdown_stream(BytesIO(), event)
        self.assertTrue(event.is_set())

    def test_stdin_failure_still_requests_shutdown(self) -> None:
        event = Event()
        with self.assertRaises(OSError):
            _wait_for_shutdown_stream(_FailingStream(), event)  # type: ignore[arg-type]
        self.assertTrue(event.is_set())

    @unittest.skipUnless(sys.platform == "win32", "Windows pipe lifecycle")
    def test_pipe_data_does_not_stop_service_but_eof_does(self) -> None:
        self._check_pipe_child("print('operation-ready', flush=True)")

    @unittest.skipUnless(
        sys.platform == "win32" and importlib.util.find_spec("numpy") is not None,
        "Windows NumPy runtime is required",
    )
    def test_first_native_import_while_shutdown_pipe_remains_open(self) -> None:
        self._check_pipe_child(textwrap.dedent("""\
            import numpy
            print('operation-ready', flush=True)
        """))

    @unittest.skipUnless(
        sys.platform == "win32" and all(
            importlib.util.find_spec(name) is not None
            for name in ("numpy", "shapely", "shapefile")
        ),
        "Windows geospatial runtime is required",
    )
    def test_first_shapefile_import_while_shutdown_pipe_remains_open(self) -> None:
        self._check_pipe_child(textwrap.dedent("""\
            from pathlib import Path
            from tempfile import TemporaryDirectory
            from verdflow.geospatial_worker import ShapefilePolygon, export_polygon_shapefile
            from verdflow.dts_live_connector.region_exchange import RegionExchangeService
            polygon = ShapefilePolygon('sample', 'Sample', (
                (0., 0., 0.), (10., 0., 0.), (10., 10., 0.), (0., 10., 0.),
            ))
            with TemporaryDirectory() as directory:
                source = Path(directory) / 'regions.zip'
                source.write_bytes(export_polygon_shapefile((polygon,)))
                result = RegionExchangeService(import_picker=lambda _: source).import_regions()
                assert result.imported and len(result.regions) == 1
            print('operation-ready', flush=True)
        """))

    def _check_pipe_child(self, operation: str) -> None:
        source_paths = [
            str(path)
            for parent in (ROOT / "apps", ROOT / "adapters", ROOT / "packages")
            for path in parent.glob("*/src")
        ]
        code = (
            f"import sys; sys.path[:0] = {source_paths!r}\n"
            "import time\n"
            "from verdflow.local_api.__main__ import _portable_shutdown_event\n"
            "stopped = _portable_shutdown_event()\n"
            "time.sleep(0.1)\n"
            "print('watcher-ready', flush=True)\n"
            + operation + "\n"
            "assert stopped.wait(10), 'parent pipe EOF was not observed'\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "VERDFLOW_SHUTDOWN_ON_STDIN_CLOSE": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output: Queue[bytes] = Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line.strip())

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        try:
            self.assertEqual(output.get(timeout=10), b"watcher-ready")
            self.assertEqual(output.get(timeout=10), b"operation-ready")
            assert process.stdin is not None
            process.stdin.write(b"parent-still-running\n")
            process.stdin.flush()
            time.sleep(0.2)
            self.assertIsNone(process.poll())
            process.stdin.close()
            process.stdin = None
            self.assertEqual(process.wait(timeout=5), 0)
        except Empty:
            self.fail("Native import or startup hung while the parent pipe was open")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdin is not None:
                process.stdin.close()
            reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
