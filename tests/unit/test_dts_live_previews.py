from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(p)
    for group in ("packages", "apps", "adapters")
    for p in (ROOT / group).glob("*/src")
]

from verdflow.contracts import StrategyPreviewOperation, StrategyPreviewRequest
from verdflow.dts_live_connector import DtsLiveConnectorServer
from verdflow.dts_live_connector.previews import ConnectorPreviews
from verdflow.system_host import (
    DtsLiveAuthorizationError,
    DtsLiveConnectorManager,
    SystemHost,
    SystemHostConfig,
)
from test_dts_live_connector_server import _request, _resolve


class DtsLivePreviewTests(unittest.TestCase):
    def test_authenticated_session_handles_are_isolated_and_cancelled(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            SystemHost(
                SystemHostConfig(library_path=Path(directory) / "library")
            ) as host,
        ):
            detail = host.install_strategy(
                ROOT / "strategy-packs/layered-ecological-community-v2"
            )
            manager = DtsLiveConnectorManager(_resolve)
            service = ConnectorPreviews(host)
            with DtsLiveConnectorServer(
                manager,
                previews=service,
                configuration_options=host.dts_live_configuration_options,
            ) as server:

                def cookie():
                    bootstrap = manager.create_session()
                    _, _, headers = _request(
                        server.base_url + "/api/v1/bootstrap/exchange",
                        document={
                            "connector_session_id": bootstrap.connector_session_id,
                            "bootstrap_nonce": bootstrap.bootstrap_nonce,
                        },
                        origin=server.base_url,
                    )
                    return headers["Set-Cookie"].split(";", 1)[0]

                first, second = cookie(), cookie()

                def request(path, body=None, method="POST", auth=first):
                    return _request(
                        server.base_url + path,
                        document=body,
                        method=method,
                        cookie=auth,
                        origin=server.base_url,
                    )[:2]

                body = {"package_hash": detail.summary.package_hash}
                self.assertEqual(
                    request("/api/v1/strategy-previews", body, auth=None)[0], 401
                )
                self.assertEqual(request("/api/v1/strategy-previews", body)[0], 403)
                host.trust_strategy(detail.summary.package_hash)
                _, options = request("/api/v1/configuration/options", method="GET")
                self.assertIsNotNone(options["strategies"][0]["parameter_preview"])
                status, operation = request("/api/v1/strategy-previews", body)
                self.assertEqual(status, 202)
                path = "/api/v1/strategy-previews/" + operation["preview_id"]
                self.assertEqual(request(path, method="GET", auth=second)[0], 401)
                self.assertEqual(request(path, method="DELETE", auth=second)[0], 401)
                deadline = time.monotonic() + 15
                while (
                    operation["status"] in {"pending", "running"}
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                    _, operation = request(path, method="GET")
                self.assertEqual(operation["status"], "succeeded", operation)
                self.assertTrue(operation["result"]["point_ids"])
                self.assertEqual(
                    request(
                        "/api/v1/asset-assignment-previews",
                        {"random_seed": 17, "points": [["p", "role"]], "roles": []},
                    ),
                    (200, {"choices": [[-1, 1.0]]}),
                )
                self.assertEqual(
                    request("/api/v1/strategy-previews/cancel-all", {})[0], 200
                )
                self.assertEqual(
                    host.get_strategy_preview(operation["preview_id"]).status,
                    "cancelled",
                )
                self.assertEqual(request(path, method="DELETE")[0], 200)
                _, next_operation = request("/api/v1/strategy-previews", body)
                self.assertEqual(request("/api/v1/session/close", {})[0], 200)
                self.assertEqual(
                    host.get_strategy_preview(next_operation["preview_id"]).status,
                    "cancelled",
                )
                self.assertEqual(request(path, method="GET")[0], 404)
            self.assertEqual(host.list_projects(), ())

    def test_late_submit_cannot_escape_session_or_server_cleanup(self):
        for close_server in (False, True):
            with self.subTest(close_server=close_server):
                entered, release = Event(), Event()
                cancelled, errors = [], []
                operation = StrategyPreviewOperation(uuid4().hex, "hash", "pending")

                class Backend:
                    def submit_strategy_preview(self, request):
                        entered.set()
                        release.wait(3)
                        return operation

                    def cancel_strategy_preview(self, preview_id):
                        cancelled.append(preview_id)
                        return replace(operation, status="cancelled")

                service = ConnectorPreviews(Backend())

                def submit():
                    try:
                        service.submit("session", StrategyPreviewRequest("hash"))
                    except Exception as error:
                        errors.append(error)

                thread = Thread(target=submit)
                thread.start()
                self.assertTrue(entered.wait(2))
                if close_server:
                    service.close()
                    service.reopen()
                else:
                    service.close_session("session")
                release.set()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(cancelled, [operation.preview_id])
                self.assertIsInstance(errors[0], DtsLiveAuthorizationError)
                with self.assertRaises(DtsLiveAuthorizationError):
                    service.get("session", operation.preview_id)

    def test_server_close_cancels_all_handles_and_per_session_budget_is_bounded(self):
        cancelled = []

        class Backend:
            def submit_strategy_preview(self, request):
                return StrategyPreviewOperation(uuid4().hex, "hash", "pending")

            def cancel_strategy_preview(self, key):
                cancelled.append(key)
                return StrategyPreviewOperation(key, "hash", "cancelled")

        service = ConnectorPreviews(Backend())
        operations = [
            service.submit("a", StrategyPreviewRequest("hash")) for _ in range(4)
        ]
        with self.assertRaises(ValueError):
            service.submit("a", StrategyPreviewRequest("hash"))
        other = service.submit("b", StrategyPreviewRequest("hash"))
        with self.assertRaises(DtsLiveAuthorizationError):
            service.cancel("b", operations[0].preview_id)
        service.close()
        self.assertEqual(
            set(cancelled), {op.preview_id for op in operations} | {other.preview_id}
        )


if __name__ == "__main__":
    unittest.main()
