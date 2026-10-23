from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "asset-package" / "src",
    ROOT / "packages" / "worker-runtime" / "src",
    ROOT / "packages" / "project-store" / "src",
    ROOT / "packages" / "library-store" / "src",
    ROOT / "packages" / "planting" / "src",
    ROOT / "packages" / "profile-package" / "src",
    ROOT / "packages" / "core" / "src",
    ROOT / "adapters" / "blender-worker" / "src",
    ROOT / "adapters" / "dts" / "src",
    ROOT / "adapters" / "model-api" / "src",
    ROOT / "adapters" / "strategy-worker" / "src",
    ROOT / "apps" / "core-host" / "src",
    ROOT / "apps" / "system-host" / "src",
    ROOT / "apps" / "studio-service" / "src",
    ROOT / "apps" / "dts-live-connector" / "src",
    ROOT / "apps" / "local-api" / "src",
]
sys.path[:0] = [str(path) for path in SOURCE_ROOTS]

from verdflow.local_api import LocalApiServer  # noqa: E402
from verdflow.local_api.__main__ import main as local_api_main  # noqa: E402
from verdflow.system_host import (  # noqa: E402
    InMemorySecretStore,
    SystemHost,
    SystemHostAlreadyRunningError,
    SystemHostConfig,
    discover_system_endpoint,
)


class SystemInstanceTests(unittest.TestCase):
    def test_failed_start_releases_singleton_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system_root = root / "system"
            system_root.mkdir()
            (system_root / "model-providers.json").write_text(
                "[]",
                encoding="utf-8",
            )
            failed = SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=system_root,
                ),
                secret_store=InMemorySecretStore(),
            )
            with self.assertRaises(ValueError):
                failed.start()

            (system_root / "model-providers.json").unlink()
            recovered = SystemHost(
                failed.config,
                secret_store=InMemorySecretStore(),
            )
            recovered.start()
            recovered.close()

    def test_same_system_root_is_singleton_and_different_roots_can_coexist(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = SystemHost(
                SystemHostConfig(
                    library_path=root / "library-a",
                    system_path=root / "system-a",
                ),
                secret_store=InMemorySecretStore(),
            )
            duplicate = SystemHost(
                SystemHostConfig(
                    library_path=root / "library-b",
                    system_path=root / "system-a",
                ),
                secret_store=InMemorySecretStore(),
            )
            independent = SystemHost(
                SystemHostConfig(
                    library_path=root / "library-c",
                    system_path=root / "system-c",
                ),
                secret_store=InMemorySecretStore(),
            )
            first.start()
            try:
                with self.assertRaises(SystemHostAlreadyRunningError):
                    duplicate.start()
                independent.start()
                independent.close()
            finally:
                first.close()

            replacement = SystemHost(
                first.config,
                secret_store=InMemorySecretStore(),
            )
            replacement.start()
            replacement.close()

    def test_local_api_publishes_endpoint_and_second_launcher_reuses_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_path = root / "library"
            system_root = root / "system-config"
            with SystemHost(
                SystemHostConfig(library_path=library_path),
                secret_store=InMemorySecretStore(),
            ) as system:
                with LocalApiServer(system) as server:
                    endpoint = discover_system_endpoint(system_root)
                    self.assertIsNotNone(endpoint)
                    assert endpoint is not None
                    self.assertEqual(endpoint.base_url, server.base_url)
                    with urlopen(
                        server.base_url + "/api/v1/health",
                        timeout=5.0,
                    ) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(health["instance_id"], endpoint.instance_id)

                    output = io.StringIO()
                    with redirect_stdout(output):
                        result = local_api_main(
                            [
                                "--library-path",
                                str(library_path),
                                "--port",
                                "0",
                            ]
                        )
                    reused = json.loads(output.getvalue())
                    self.assertEqual(result, 0)
                    self.assertTrue(reused["reused"])
                    self.assertEqual(reused["base_url"], server.base_url)

                self.assertIsNone(discover_system_endpoint(system_root))

    def test_killed_owner_releases_cross_process_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_path = root / "library"
            child = self._start_owner_process(library_path)
            try:
                ready = child.stdout.readline().strip()
                self.assertTrue(ready.startswith("ready http://127.0.0.1:"))
                duplicate = SystemHost(
                    SystemHostConfig(library_path=library_path),
                    secret_store=InMemorySecretStore(),
                )
                with self.assertRaises(SystemHostAlreadyRunningError) as caught:
                    duplicate.start()
                self.assertIsNotNone(caught.exception.endpoint)

                child.kill()
                child.wait(timeout=10.0)
                replacement = self._start_after_process_exit(library_path)
                self.assertIsNone(
                    discover_system_endpoint(root / "system-config")
                )
                replacement.close()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10.0)
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()

    @staticmethod
    def _start_owner_process(library_path: Path) -> subprocess.Popen[str]:
        script = "\n".join(
            [
                "import sys, time",
                "from pathlib import Path",
                "from verdflow.local_api import LocalApiServer",
                "from verdflow.system_host import SystemHost, SystemHostConfig",
                "host = SystemHost(SystemHostConfig(library_path=Path(sys.argv[1])))",
                "host.start()",
                "server = LocalApiServer(host)",
                "server.start()",
                "print('ready ' + server.base_url, flush=True)",
                "time.sleep(60)",
            ]
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(path) for path in SOURCE_ROOTS]
        )
        return subprocess.Popen(
            [sys.executable, "-c", script, str(library_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

    @staticmethod
    def _start_after_process_exit(library_path: Path) -> SystemHost:
        deadline = time.monotonic() + 10.0
        while True:
            host = SystemHost(
                SystemHostConfig(library_path=library_path),
                secret_store=InMemorySecretStore(),
            )
            try:
                host.start()
                return host
            except SystemHostAlreadyRunningError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
