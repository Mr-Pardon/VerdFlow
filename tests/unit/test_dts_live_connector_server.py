from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile
from threading import Event
import unittest
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
    str(ROOT / "apps" / "system-host" / "src"),
    str(ROOT / "apps" / "dts-live-connector" / "src"),
]

from verdflow.dts_adapter import DtsLiveExecutionSnapshot, DtsLiveCorrelation, DtsLiveElevationBatch  # noqa: E402
from verdflow.dts_live_connector import (  # noqa: E402
    DtsLiveConnectorConfig,
    DtsLiveConnectorServer,
)
from verdflow.system_host import (  # noqa: E402
    DtsLiveConfigurationOptions,
    DtsLiveConnectorManager,
    RoleBindingProfileDetail,
    RoleBindingProfileSaveRequest,
    RoleBindingProfileSummary,
)


class DtsLiveConnectorServerTests(unittest.TestCase):
    def test_asset_search_is_session_authorized_and_constrained_to_dts(self) -> None:
        from verdflow.system_host import AssetSearchPage
        calls = []
        def search(request):
            calls.append(request)
            return AssetSearchPage((), (), 0, 0, None, "revision", ())
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        with DtsLiveConnectorServer(manager, asset_search=search) as server:
            body = {"target_type": "dts", "provider_id": "dts.static-mesh", "catalog_ids": ["a", "b"], "query": "Tree"}
            url = server.base_url + "/api/v1/configuration/asset-search"
            status, _, _ = _request(url, document=body, origin=server.base_url)
            self.assertEqual(status, 401)
            _, _, headers = _request(server.base_url + "/api/v1/bootstrap/exchange",
                document={"connector_session_id": bootstrap.connector_session_id, "bootstrap_nonce": bootstrap.bootstrap_nonce}, origin=server.base_url)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            status, page, _ = _request(url, document=body, cookie=cookie, origin=server.base_url)
            self.assertEqual(status, 200)
            self.assertEqual(page["revision"], "revision")
            self.assertEqual(calls[0].catalog_ids, ("a", "b"))
            status, _, _ = _request(url, document={**body, "target_type": "blender"}, cookie=cookie, origin=server.base_url)
            self.assertEqual(status, 400)
            self.assertEqual(len(calls), 1)

    def test_builtin_brand_assets_are_served_without_stale_immutable_caching(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        source = ROOT / "assets" / "branding" / "generated" / "brand"
        with DtsLiveConnectorServer(manager) as server:
            for name in ("logo-light.png", "logo-dark.png", "icon-32.png", "icon-256.png"):
                with self.subTest(name=name):
                    status, payload, headers = _raw_request(server.base_url + "/brand/" + name)
                    self.assertEqual(status, 200)
                    self.assertEqual(payload, (source / name).read_bytes())
                    self.assertEqual(headers["Content-Type"], "image/png")
                    self.assertEqual(headers["Cache-Control"], "no-store")
            with self.assertRaises(HTTPError) as escaped:
                _raw_request(server.base_url + "/brand/../../outside.png")
            self.assertEqual(escaped.exception.code, 404)
            escaped.exception.close()
            with self.assertRaises(HTTPError) as foreign:
                _raw_request(server.base_url + "/brand/logo-dark.png", origin="https://example.com")
            self.assertEqual(foreign.exception.code, 403)
            foreign.exception.close()

    def test_64_regions_accept_spatially_reordered_chunks_and_final_callback(self) -> None:
        from dataclasses import asdict

        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        with DtsLiveConnectorServer(manager) as server:
            _, _, headers = _request(
                server.base_url + "/api/v1/bootstrap/exchange",
                document={"connector_session_id": bootstrap.connector_session_id,
                          "bootstrap_nonce": bootstrap.bootstrap_nonce},
                origin=server.base_url,
            )
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            regions = [
                {"region_id": str(i), "name": f"Region {i}",
                 "coordinates": [[i, 0, 0], [i + 1, 0, 0], [i, 1, 0]]}
                for i in range(64)
            ]
            status, selection, _ = _request(
                server.base_url + "/api/v1/operations/selection",
                document={"strategy_package_hash": HASH, "profile_id": "profile", "regions": regions},
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 202)
            correlation = DtsLiveCorrelation(**selection["correlation"])
            port = manager.trusted_port(bootstrap.connector_session_id)
            command = port.request_elevation(
                operation_id=correlation.operation_id, generation_token=correlation.generation_token,
                point_set_id="points", point_ids=[f"p{i}" for i in range(128)],
                lines=[{"start": [i, 0, 100], "end": [i, 0, -100]} for i in range(128)],
                batches=[DtsLiveElevationBatch(str(i), f"Region {i}", (i, 0, 0, i + 1, 1, 0), i * 2, 2)
                         for i in range(64)],
            )
            for offset in (1, 0):
                indices = list(range(offset, 128, 2))
                status, result, _ = _request(
                    server.base_url + "/api/v1/callbacks/elevation-chunk",
                    document={"correlation": asdict(command.correlation), "indices": indices,
                              "intersects": [{"LineIntersectPoint": [i, 0, 12]} for i in indices]},
                    cookie=cookie, origin=server.base_url,
                )
                self.assertEqual(status, 202, result)
            status, result, _ = _request(
                server.base_url + "/api/v1/callbacks/elevation",
                document={"correlation": asdict(command.correlation), "intersects": None,
                          "chunked": True, "failed_regions": []},
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 202, result)
            self.assertEqual(result["sample_count"], 128)

    def test_large_selection_light_poll_and_indexed_elevation_callback(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        ready = []
        with DtsLiveConnectorServer(manager, on_selection_ready=ready.append) as server:
            _, _, headers = _request(
                server.base_url + "/api/v1/bootstrap/exchange",
                document={"connector_session_id": bootstrap.connector_session_id, "bootstrap_nonce": bootstrap.bootstrap_nonce},
                origin=server.base_url,
            )
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            regions = [
                {"region_id": str(i), "name": "Region " + str(i) + "x" * 400,
                 "coordinates": [[i, 0, 0], [i + 1, 0, 0], [i, 1, 0]]}
                for i in range(2180)
            ]
            body = {"strategy_package_hash": HASH, "profile_id": "profile", "regions": regions}
            self.assertGreater(len(json.dumps(body)), 1024 * 1024)
            status, selection, _ = _request(
                server.base_url + "/api/v1/operations/selection", document=body,
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 202)
            self.assertEqual(len(ready[0].regions), 2180)
            status, snapshot, _ = _request(
                server.base_url + "/api/v1/session?include_regions=false", method="GET",
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 200)
            self.assertEqual(snapshot["selection_regions"], [])
            self.assertEqual(snapshot["selection_coordinate_count"], 6540)
            correlation = DtsLiveCorrelation(**selection["correlation"])
            port = manager.trusted_port(bootstrap.connector_session_id)
            command = port.request_elevation(
                operation_id=correlation.operation_id, generation_token=correlation.generation_token,
                point_set_id="points", point_ids=["p1"],
                lines=[{"start": [0, 0, 100], "end": [0, 0, -100]}],
                batches=[DtsLiveElevationBatch("0", "Region 0", (0, 0, 0, 1, 1, 0), 0, 1)],
            )
            from dataclasses import asdict
            payload = {"correlation": asdict(command.correlation), "indices": [0],
                       "intersects": [[{"LineIntersectPoint": [0, 0, 0]}]]}
            endpoint = server.base_url + "/api/v1/callbacks/elevation-chunk"
            status, _, _ = _request(endpoint, document=payload, origin=server.base_url)
            self.assertEqual(status, 401)
            for indices, expected in [([True], 400), ([1], 400), ([0], 202), ([0], 202)]:
                status, _, _ = _request(endpoint, document={**payload, "indices": indices}, cookie=cookie, origin=server.base_url)
                self.assertEqual(status, expected)
            status, _, _ = _request(
                server.base_url + "/api/v1/callbacks/elevation",
                document={"correlation": payload["correlation"], "intersects": None, "chunked": True},
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 202)
            status, _, _ = _request(endpoint, document=payload, cookie=cookie, origin=server.base_url)
            self.assertEqual(status, 400)

    def test_submit_region_collection_starts_once_and_keeps_busy_gate(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        ready = []
        with DtsLiveConnectorServer(manager, on_selection_ready=ready.append) as server:
            _, _, headers = _request(
                server.base_url + "/api/v1/bootstrap/exchange",
                document={"connector_session_id": bootstrap.connector_session_id,
                          "bootstrap_nonce": bootstrap.bootstrap_nonce},
                origin=server.base_url,
            )
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            configuration = {"strategy_package_hash": HASH, "profile_id": "profile"}
            for invalid in (None, [], "invalid"):
                status, _, _ = _request(
                    server.base_url + "/api/v1/operations/selection",
                    document={**configuration, "regions": invalid},
                    cookie=cookie, origin=server.base_url,
                )
                self.assertEqual(status, 400)
            regions = [
                {"region_id": str(i), "name": str(i), "coordinates": [[i, 0, 0], [i + 1, 0, 0], [i, 1, 0]]}
                for i in (0, 100)
            ]
            request = {**configuration, "regions": regions}
            status, selection, _ = _request(
                server.base_url + "/api/v1/operations/selection", document=request,
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 202)
            self.assertEqual(len(selection["regions"]), 2)
            self.assertEqual(len(ready), 1)
            status, _, _ = _request(
                server.base_url + "/api/v1/operations/selection", document=request,
                cookie=cookie, origin=server.base_url,
            )
            self.assertEqual(status, 409)
            self.assertEqual(len(ready), 1)

    def test_bootstrap_cookie_origin_and_operation_lock(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        ready = []
        connected = []
        connected_event = Event()
        closed = []

        def on_connected(session_id: str) -> None:
            connected.append(session_id)
            connected_event.set()

        with DtsLiveConnectorServer(
            manager,
            on_selection_ready=ready.append,
            on_session_connected=on_connected,
            on_session_closed=closed.append,
        ) as server:
            status, page, headers = _raw_request(server.base_url + "/")
            self.assertEqual(status, 200)
            self.assertIn(b'<div id="app"></div>', page)
            self.assertTrue(headers["Content-Type"].startswith("text/html"))
            script_match = re.search(rb'src="(/assets/[^"]+\.js)"', page)
            self.assertIsNotNone(script_match)
            status, _, headers = _raw_request(
                server.base_url + script_match.group(1).decode("ascii")
            )
            self.assertEqual(status, 200)
            self.assertTrue(
                headers["Content-Type"].startswith("text/javascript")
            )

            status, exchange, headers = _request(
                server.base_url + "/api/v1/bootstrap/exchange",
                document={
                    "connector_session_id": bootstrap.connector_session_id,
                    "bootstrap_nonce": bootstrap.bootstrap_nonce,
                },
                origin=server.base_url,
            )
            self.assertEqual(status, 200)
            self.assertNotIn("access_token", exchange)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.assertIn("HttpOnly", headers["Set-Cookie"])
            self.assertIn("SameSite=Strict", headers["Set-Cookie"])
            self.assertTrue(connected_event.wait(timeout=5.0))
            self.assertEqual(connected, [bootstrap.connector_session_id])

            status, denied, _ = _request(
                server.base_url + "/api/v1/session",
                method="GET",
            )
            self.assertEqual(status, 401)
            self.assertEqual(denied["error"]["code"], "authorization_failed")

            status, denied, _ = _request(
                server.base_url + "/api/v1/session",
                method="GET",
                cookie=cookie,
                origin="http://127.0.0.1:9",
            )
            self.assertEqual(status, 403)
            self.assertEqual(denied["error"]["code"], "origin_not_allowed")

            request = {
                "strategy_package_hash": HASH,
                "profile_id": "profile",
                "random_seed": 137,
                "generation_args": {
                    "strategy_parameters": {"overall_density": 0.35}
                },
            }
            status, command, _ = _request(
                server.base_url + "/api/v1/operations/selection",
                document=request,
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 202)

            status, busy, _ = _request(
                server.base_url + "/api/v1/operations/selection",
                document=request,
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 409)
            self.assertEqual(busy["error"]["code"], "operation_busy")

            status, selection, _ = _request(
                server.base_url + "/api/v1/callbacks/selection",
                document={
                    "correlation": command["correlation"],
                    "coordinates": [
                        [0, 0, 0],
                        [10, 0, 0],
                        [10, 10, 0],
                        [0, 0, 0],
                    ],
                },
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 202)
            self.assertEqual(len(selection["regions"][0]["coordinates"]), 3)

            status, snapshot, _ = _request(
                server.base_url + "/api/v1/session",
                method="GET",
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 200)
            self.assertEqual(snapshot["stage"], "selection_ready")
            self.assertEqual(
                snapshot["execution"]["strategy_package_hash"],
                HASH,
            )
            self.assertEqual(snapshot["execution"]["random_seed"], 137)
            self.assertEqual(
                snapshot["execution"]["generation_args"],
                {"strategy_parameters": {"overall_density": 0.35}},
            )
            self.assertEqual(len(ready), 1)
            self.assertEqual(
                ready[0].correlation.operation_id,
                command["correlation"]["operation_id"],
            )

            status, _, _ = _request(
                server.base_url + "/api/v1/operations/cancel",
                document={},
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 200)
            regenerated_request = {
                **request,
                "random_seed": 211,
                "generation_args": {
                    "strategy_parameters": {
                        "overall_density": 0.35,
                        "feature_group_min_count": 4,
                    }
                },
            }
            status, regenerated, _ = _request(
                server.base_url + "/api/v1/operations/regeneration",
                document=regenerated_request,
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 202)
            self.assertEqual(regenerated["regions"], selection["regions"])
            self.assertNotEqual(
                regenerated["correlation"]["operation_id"],
                command["correlation"]["operation_id"],
            )
            self.assertEqual(len(ready), 2)
            self.assertEqual(
                manager.snapshot(
                    bootstrap.connector_session_id,
                    cookie.split(".", 1)[1],
                ).execution.random_seed,
                211,
            )
            self.assertEqual(
                manager.snapshot(
                    bootstrap.connector_session_id,
                    cookie.split(".", 1)[1],
                ).execution.generation_args,
                regenerated_request["generation_args"],
            )

            status, response, _ = _request(
                server.base_url + "/api/v1/session/close",
                document={},
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["closed"])
            self.assertEqual(closed, [bootstrap.connector_session_id])

    def test_serves_webui_sdk_and_authenticated_configuration(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        saved_profiles: list[RoleBindingProfileSaveRequest] = []

        def save_profile(
            request: RoleBindingProfileSaveRequest,
        ) -> RoleBindingProfileDetail:
            saved_profiles.append(request)
            return _profile_detail(request.display_name)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            webui = root / "webui"
            sdk = root / "sdk"
            (webui / "assets").mkdir(parents=True)
            sdk.mkdir()
            (webui / "index.html").write_text("<div id='app'></div>", encoding="utf-8")
            (webui / "assets" / "app.js").write_text("export {};", encoding="utf-8")
            (sdk / "ac_conf.js").write_text("window.HostConfig={};", encoding="utf-8")
            (sdk / "ac.min.js").write_text("window.DigitalTwinAPI=function(){};", encoding="utf-8")
            with DtsLiveConnectorServer(
                manager,
                DtsLiveConnectorConfig(webui_root=webui, sdk_root=sdk),
                configuration_options=lambda: DtsLiveConfigurationOptions(
                    strategies=(),
                    catalogs=(),
                ),
                library_resource=lambda resource_id: (
                    _Resource(resource_id, "image/png"),
                    b"thumbnail-bytes",
                ),
                profile_detail=lambda profile_id: _profile_detail(profile_id),
                save_profile=save_profile,
            ) as server:
                status, payload, headers = _raw_request(server.base_url + "/")
                self.assertEqual(status, 200)
                self.assertIn(b"id='app'", payload)
                self.assertTrue(headers["Content-Type"].startswith("text/html"))

                status, _, headers = _raw_request(
                    server.base_url + "/assets/app.js"
                )
                self.assertEqual(status, 200)
                self.assertTrue(
                    headers["Content-Type"].startswith("text/javascript")
                )

                status, _, headers = _raw_request(
                    server.base_url + "/sdk/ac.min.js"
                )
                self.assertEqual(status, 200)
                self.assertTrue(
                    headers["Content-Type"].startswith("text/javascript")
                )

                _, _, exchange_headers = _request(
                    server.base_url + "/api/v1/bootstrap/exchange",
                    document={
                        "connector_session_id": bootstrap.connector_session_id,
                        "bootstrap_nonce": bootstrap.bootstrap_nonce,
                    },
                    origin=server.base_url,
                )
                cookie = exchange_headers["Set-Cookie"].split(";", 1)[0]
                status, document, _ = _request(
                    server.base_url + "/api/v1/configuration/options",
                    method="GET",
                    cookie=cookie,
                    origin=server.base_url,
                )
                self.assertEqual(status, 200)
                self.assertEqual(document, {"strategies": [], "catalogs": []})

                status, document, _ = _request(
                    server.base_url + "/api/v1/configuration/profiles/profile_test",
                    method="GET",
                    cookie=cookie,
                    origin=server.base_url,
                )
                self.assertEqual(status, 200)
                self.assertEqual(document["summary"]["display_name"], "profile_test")

                status, payload, headers = _raw_request(
                    server.base_url
                    + "/api/v1/configuration/resources/resource_test",
                    cookie=cookie,
                    origin=server.base_url,
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload, b"thumbnail-bytes")
                self.assertEqual(headers["Content-Type"], "image/png")

                status, document, _ = _request(
                    server.base_url + "/api/v1/configuration/profiles",
                    document={
                        "display_name": "现场映射",
                        "strategy_id": "strategy",
                        "strategy_version": "1.0.0",
                        "catalog_id": "catalog",
                        "assignments": {
                            "tree": {
                                "candidates": [
                                    {
                                        "asset_id": "SM_Tree",
                                        "weight": 1,
                                        "scale": {
                                            "mode": "uniform_random",
                                            "min": 0.8,
                                            "max": 1.2,
                                        },
                                    }
                                ]
                            }
                        },
                    },
                    cookie=cookie,
                    origin=server.base_url,
                )
                self.assertEqual(status, 201)
                self.assertEqual(document["summary"]["display_name"], "现场映射")

                status, document, _ = _request(
                    server.base_url + "/api/v1/configuration/profiles",
                    document={
                        "profile_id": "profile_test",
                        "display_name": "现场映射更新",
                        "strategy_id": "strategy",
                        "strategy_version": "1.0.0",
                        "catalog_id": "catalog",
                        "assignments": {
                            "tree": {
                                "candidates": [
                                    {
                                        "asset_id": "SM_Tree",
                                        "weight": 1,
                                        "scale": {"mode": "fixed", "value": 1},
                                    }
                                ]
                            }
                        },
                        "expected_generation": 7,
                    },
                    cookie=cookie,
                    origin=server.base_url,
                )
                self.assertEqual(status, 201)
                self.assertEqual(saved_profiles[-1].profile_id, "profile_test")
                self.assertEqual(saved_profiles[-1].expected_generation, 7)
                self.assertFalse(saved_profiles[0].replace_strategy_parameters)
                for preset in (None, {}, {"spacing": 4}):
                    status, _, _ = _request(
                        server.base_url + "/api/v1/configuration/profiles",
                        document={"profile_id": "profile_test", "display_name": "Preset",
                            "strategy_id": "strategy", "strategy_version": "1.0.0", "catalog_id": "catalog",
                            "assignments": {"tree": {"candidates": [{"asset_id": "SM_Tree", "weight": 1}]}},
                            "strategy_parameters": preset},
                        cookie=cookie, origin=server.base_url,
                    )
                    self.assertEqual(status, 201)
                    self.assertTrue(saved_profiles[-1].replace_strategy_parameters)
                    self.assertEqual(saved_profiles[-1].strategy_parameters, preset)

    def test_elevation_callback_has_a_separate_payload_limit(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        config = DtsLiveConnectorConfig(
            request_body_limit_bytes=512,
            elevation_request_body_limit_bytes=64 * 1024,
        )
        with DtsLiveConnectorServer(manager, config) as server:
            _, _, exchange_headers = _request(
                server.base_url + "/api/v1/bootstrap/exchange",
                document={
                    "connector_session_id": bootstrap.connector_session_id,
                    "bootstrap_nonce": bootstrap.bootstrap_nonce,
                },
                origin=server.base_url,
            )
            cookie = exchange_headers["Set-Cookie"].split(";", 1)[0]
            access_token = cookie.split(".", 1)[1]
            status, selection_command, _ = _request(
                server.base_url + "/api/v1/operations/selection",
                document={
                    "strategy_package_hash": HASH,
                    "profile_id": "profile",
                },
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 202)
            status, selection, _ = _request(
                server.base_url + "/api/v1/callbacks/selection",
                document={
                    "correlation": selection_command["correlation"],
                    "coordinates": [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
                },
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 202)

            point_count = 64
            elevation_command = manager.request_elevation(
                bootstrap.connector_session_id,
                access_token,
                operation_id=selection["correlation"]["operation_id"],
                generation_token=selection["correlation"]["generation_token"],
                point_set_id="points_generated",
                point_ids=tuple(f"point_{index}" for index in range(point_count)),
                lines=tuple(
                    {
                        "start": [float(index), 0.0, 100.0],
                        "end": [float(index), 0.0, -100.0],
                    }
                    for index in range(point_count)
                ),
            )
            elevation_document = {
                "correlation": {
                    "connector_session_id": (
                        elevation_command.correlation.connector_session_id
                    ),
                    "operation_id": elevation_command.correlation.operation_id,
                    "generation_token": (
                        elevation_command.correlation.generation_token
                    ),
                    "request_id": elevation_command.correlation.request_id,
                },
                "intersects": [
                    [{"LineIntersectPoint": [float(index), 0.0, 12.0]}]
                    for index in range(point_count)
                ],
            }
            self.assertGreater(
                len(json.dumps(elevation_document).encode("utf-8")),
                config.request_body_limit_bytes,
            )
            status, response, _ = _request(
                server.base_url + "/api/v1/callbacks/elevation",
                document=elevation_document,
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 202)
            self.assertEqual(
                response,
                {"accepted": True, "sample_count": point_count},
            )

            status, response, _ = _request(
                server.base_url + "/api/v1/operations/cancel",
                document={"padding": "x" * 1024},
                cookie=cookie,
                origin=server.base_url,
            )
            self.assertEqual(status, 413)
            self.assertEqual(response["error"]["code"], "payload_too_large")

    def test_client_disconnect_is_not_converted_to_internal_error(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        server = DtsLiveConnectorServer(manager)
        handler_type = server._handler_type()
        errors: list[tuple[object, object]] = []

        class StubHandler:
            def _error(self, status: object, code: object) -> None:
                errors.append((status, code))

        handler_type._known_error(  # type: ignore[arg-type]
            StubHandler(),
            ConnectionAbortedError(10053, "connection aborted"),
        )
        self.assertEqual(errors, [])


HASH = "sha256:" + "a" * 64


class _Resource:
    def __init__(self, resource_id: str, media_type: str) -> None:
        self.resource_id = resource_id
        self.media_type = media_type


def _profile_detail(display_name: str) -> RoleBindingProfileDetail:
    return RoleBindingProfileDetail(
        summary=RoleBindingProfileSummary(
            profile_entry_id="profile-entry",
            profile_id="profile_test",
            display_name=display_name,
            strategy_id="strategy",
            strategy_version="1.0.0",
            target_type="dts",
            provider_id="dts.static-mesh",
            catalog_id="catalog",
            generation=1,
            semantic_hash="sha256:" + "e" * 64,
            updated_at="2026-08-24T00:00:00Z",
        ),
        role_count=1,
        assignments={},
        catalog_drift={},
        drift_count=0,
    )


def _resolve(request):
    return DtsLiveExecutionSnapshot(
        strategy_package_hash=request.strategy_package_hash,
        strategy_id="strategy",
        strategy_version="1.0.0",
        profile_id=request.profile_id,
        profile_semantic_hash="sha256:" + "b" * 64,
        target_type="dts",
        asset_source_kind="profile",
        asset_source_id=request.profile_id or "profile",
        asset_source_generation=None,
        asset_source_hash="sha256:" + "b" * 64,
        effective_binding_semantic_hash="sha256:" + "d" * 64,
        random_seed=request.random_seed,
        generation_args=request.generation_args,
    )


def _request(
    url: str,
    *,
    method: str = "POST",
    document: dict | None = None,
    cookie: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict, object]:
    payload = None
    headers = {"Accept": "application/json"}
    if document is not None:
        payload = json.dumps(document).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie is not None:
        headers["Cookie"] = cookie
    if origin is not None:
        headers["Origin"] = origin
    request = Request(url, data=payload, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read()), response.headers


def _raw_request(
    url: str,
    *,
    cookie: str | None = None,
    origin: str | None = None,
) -> tuple[int, bytes, object]:
    headers = {}
    if cookie is not None:
        headers["Cookie"] = cookie
    if origin is not None:
        headers["Origin"] = origin
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:
        return response.status, response.read(), response.headers


if __name__ == "__main__":
    unittest.main()
