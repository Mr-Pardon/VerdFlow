from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "worker-runtime" / "src"))

from verdflow.worker_runtime import ProcessTreeGuard, read_windows_memory_headroom  # noqa: E402


class ProcessTreeGuardTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Job Object limit")
    def test_windows_process_tree_rejects_commit_above_its_limit(self) -> None:
        memory = read_windows_memory_headroom()
        self.assertIsNotNone(memory)
        self.assertGreater(memory.total_physical_bytes, 0)
        self.assertLessEqual(memory.available_physical_bytes, memory.total_physical_bytes)
        if memory.available_commit_bytes < 512 * 1024**2:
            self.skipTest("Insufficient system commit headroom for the Windows allocation probe")
        script = """\
import ctypes
import sys
sys.stdin.buffer.read(1)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
allocate = kernel32.VirtualAlloc
allocate.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.c_ulong]
allocate.restype = ctypes.c_void_p
assert allocate(None, 1024**2, 0x3000, 0x04)
assert not allocate(None, 128 * 1024**2, 0x3000, 0x04)
"""
        guard = ProcessTreeGuard(memory_limit_bytes=64 * 1024**2)
        try:
            with subprocess.Popen(
                [sys.executable, "-c", script], stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                **guard.popen_options(),
            ) as process:
                try:
                    guard.attach(process)
                    _, stderr = process.communicate(b"1", timeout=10)
                    self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
                finally:
                    if process.poll() is None:
                        process.kill()
        finally:
            guard.close()

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
