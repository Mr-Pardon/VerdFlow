from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "worker-runtime" / "src"))

from verdflow.worker_runtime import ProcessTreeGuard  # noqa: E402


class ProcessTreeGuardTests(unittest.TestCase):
    def test_terminate_reaps_attached_process(self) -> None:
        guard = ProcessTreeGuard()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **guard.popen_options(),
        )
        try:
            guard.attach(process)
            exit_code = guard.terminate(process, grace_seconds=2.0)

            self.assertIsNotNone(exit_code)
            self.assertIsNotNone(process.poll())
        finally:
            guard.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2.0)

    def test_close_reaps_attached_process_tree(self) -> None:
        guard = ProcessTreeGuard()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **guard.popen_options(),
        )
        try:
            guard.attach(process)
            guard.close()
            deadline = time.monotonic() + 2.0
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)

            self.assertIsNotNone(process.poll())
        finally:
            guard.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
