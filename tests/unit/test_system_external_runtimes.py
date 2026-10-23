from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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
    str(ROOT / "apps" / "studio-service" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
    str(ROOT / "apps" / "dts-live-connector" / "src"),
    str(ROOT / "apps" / "local-api" / "src"),
]

from verdflow.local_api import LocalApiConfig, LocalApiServer  # noqa: E402
from verdflow.system_host import (  # noqa: E402
    BLENDER_RUNTIME_ID,
    DTS_EXPLORER_RUNTIME_ID,
    ExternalRuntimeConfigStore,
    SystemHost,
    SystemHostConfig,
    resolve_dts_explorer_installation,
)


class ExternalRuntimeConfigStoreTests(unittest.TestCase):
    def test_manual_locators_round_trip_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blender = root / "blender.exe"
            blender.write_bytes(b"fixture")
            explorer = _create_dts_explorer(root / "DTS Explorer" / "v7.1")

            store = ExternalRuntimeConfigStore(root / "system")
            store.prepare()
            configured_blender = store.configure(
                BLENDER_RUNTIME_ID,
                str(blender),
            )
            configured_explorer = store.configure(
                DTS_EXPLORER_RUNTIME_ID,
                str(explorer),
            )

            self.assertTrue(configured_blender.available)
            self.assertEqual(configured_blender.resolution_source, "manual")
            self.assertTrue(configured_explorer.available)
            self.assertEqual(
                configured_explorer.required_names,
                (
                    "SDK/ac_conf.js",
                    "SDK/ac.min.js",
                    "AirCityExplorer/Binaries/Win64/DTS Explorer.exe",
                ),
            )

            reopened = ExternalRuntimeConfigStore(root / "system")
            reopened.prepare()
            loaded = {item.runtime_id: item for item in reopened.list()}
            self.assertEqual(loaded[BLENDER_RUNTIME_ID].resolved_path, str(blender.resolve()))
            self.assertEqual(
                loaded[DTS_EXPLORER_RUNTIME_ID].resolved_path,
                str(explorer.resolve()),
            )
            installation = resolve_dts_explorer_installation(explorer)
            self.assertEqual(installation.sdk_root, explorer.resolve() / "SDK")
            self.assertEqual(
                installation.executable_path,
                explorer.resolve()
                / "AirCityExplorer"
                / "Binaries"
                / "Win64"
                / "DTS Explorer.exe",
            )

            with patch(
                "verdflow.system_host.external_runtimes.locate_blender",
                return_value=None,
            ):
                cleared = reopened.clear(BLENDER_RUNTIME_ID)
            self.assertFalse(cleared.available)
            self.assertIsNone(cleared.configured_path)

    def test_invalid_locator_is_rejected_and_environment_can_be_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            explorer = _create_dts_explorer(root / "DTS Explorer" / "v7.1")
            store = ExternalRuntimeConfigStore(root / "system")
            store.prepare()

            with self.assertRaisesRegex(ValueError, "directory_not_found"):
                store.configure(
                    DTS_EXPLORER_RUNTIME_ID,
                    str(root / "missing"),
                )

            with patch.dict(
                os.environ,
                {"VERDFLOW_DTS_EXPLORER_ROOT": str(explorer)},
                clear=False,
            ):
                discovered = store.get(DTS_EXPLORER_RUNTIME_ID)
            self.assertTrue(discovered.available)
            self.assertEqual(discovered.resolution_source, "auto")
            self.assertEqual(discovered.resolved_path, str(explorer.resolve()))


class ExternalRuntimeLocalApiTests(unittest.TestCase):
    def test_runtime_management_requires_token_and_returns_live_status(self) -> None:
        token = "runtime-management-token-for-local-api-tests"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            explorer = _create_dts_explorer(root / "DTS Explorer" / "v7.1")
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                )
            ) as system:
                with LocalApiServer(
                    system,
                    LocalApiConfig(management_token=token),
                ) as server:
                    status, document = _request_json(
                        server.base_url + "/api/v1/external-runtimes"
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(len(document["items"]), 2)

                    url = (
                        server.base_url
                        + "/api/v1/external-runtimes/dts-explorer"
                    )
                    status, denied = _request_json(
                        url,
                        method="PUT",
                        document={"path": str(explorer)},
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(
                        denied["error"]["code"],
                        "management_auth_required",
                    )

                    headers = {"Authorization": f"Bearer {token}"}
                    status, configured = _request_json(
                        url,
                        method="PUT",
                        document={"path": str(explorer)},
                        headers=headers,
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(configured["available"])
                    self.assertEqual(configured["resolution_source"], "manual")

                    status, cleared = _request_json(
                        url,
                        method="DELETE",
                        headers=headers,
                    )
                    self.assertEqual(status, 200)
                    self.assertIsNone(cleared["configured_path"])

                    persisted = json.loads(
                        (root / "system" / "external-runtimes.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(persisted["manual_paths"], {})
                    self.assertNotIn(
                        str(explorer),
                        json.dumps(asdict(system.status())),
                    )


def _create_dts_explorer(root: Path) -> Path:
    sdk = root / "SDK"
    sdk.mkdir(parents=True)
    (sdk / "ac_conf.js").write_text("", encoding="utf-8")
    (sdk / "ac.min.js").write_text("", encoding="utf-8")
    executable = (
        root
        / "AirCityExplorer"
        / "Binaries"
        / "Win64"
        / "DTS Explorer.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    return root


def _request_json(
    url: str,
    *,
    method: str = "GET",
    document: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    payload = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if document is not None:
        payload = json.dumps(document).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=payload,
        method=method,
        headers=request_headers,
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
