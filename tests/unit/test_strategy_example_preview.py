from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Event
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path)
    for parent in (ROOT / "packages", ROOT / "apps", ROOT / "adapters")
    for path in parent.glob("*/src")
]

from verdflow.contracts import StrategyPreviewRequest, StrategyPreviewResult
from verdflow.core import (
    StrategyAccessError,
    StrategyExecutionAuthorizer,
    StrategyPreviewService,
)
from verdflow.library_store import LibraryStore
from verdflow.local_api import LocalApiConfig, LocalApiServer
from verdflow.planting import PlantingDocumentError, strategy_preview_sample
from verdflow.strategy_worker import (
    StrategyExecutionCancelled,
    StrategyExecutionError,
    StrategyPackageError,
    StrategyPreviewWorker,
    StrategyPreviewWorkerConfig,
    StrategyWorker,
    inspect_strategy_package,
)
from verdflow.system_host import SystemHost, SystemHostConfig
from verdflow.system_host.strategy_previews import StrategyPreviewRuntime


class StrategyExampleTests(unittest.TestCase):
    def test_optional_single_plot_declaration_is_bounded_and_not_required(self) -> None:
        self.assertIsNone(strategy_preview_sample({}))
        package = inspect_strategy_package(ROOT / "strategy-packs/lawn-feature-groups")
        sample = strategy_preview_sample(package.strategy)
        self.assertEqual(sample.display_name, "弧形花坛")
        self.assertEqual(len(sample.boundary_xy), 36)
        for field, value in (
            ("boundary_xy", [[0, 0], [0, 0], [0, 0]]),
            ("boundary_xy", [[0, 0], [1001, 0], [0, 1]]),
            ("boundary_xy", [[0, 0], [1, float("nan")], [0, 1]]),
            ("fixed_seed", True),
            ("version", "2"),
            ("display_name", ""),
        ):
            document = copy.deepcopy(package.strategy)
            document["extensions"]["parameter_preview"][field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(PlantingDocumentError),
            ):
                strategy_preview_sample(document)

    def test_original_generator_matches_projection_for_each_maintained_sample(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as store:
                for name in (
                    "layered-ecological-community-v2",
                    "lawn-feature-groups",
                    "universal-street-tree",
                ):
                    package = inspect_strategy_package(ROOT / "strategy-packs" / name)
                    _install(store, package)
                    service = StrategyPreviewService(
                        StrategyExecutionAuthorizer(store, store)
                    )
                    roles = [role["role_key"] for role in package.strategy["roles"]]
                    for excluded in ((), (roles[0],)):
                        prepared = service.prepare(
                            StrategyPreviewRequest(package.package_hash, {}, excluded),
                            package.strategy,
                        )
                        worker = StrategyPreviewWorker(package, root)
                        first = worker.generate_example(
                            prepared, cancellation_requested=lambda: False
                        )
                        second = worker.generate_example(
                            prepared, cancellation_requested=lambda: False
                        )
                        self.assertEqual(first, second)
                        sample = prepared.sample
                        context = {
                            "strategy": package.strategy,
                            "seed": sample.fixed_seed,
                            "selected_regions": [
                                {
                                    "id": "strategy_example",
                                    "rings": [
                                        {
                                            "role": "outer",
                                            "loop": [
                                                [x, y, 0] for x, y in sample.boundary_xy
                                            ],
                                        }
                                    ],
                                }
                            ],
                            "args": {
                                "strategy_parameters": dict(
                                    prepared.strategy_parameters
                                )
                            },
                            "excluded_role_keys": list(excluded),
                        }
                        formal = StrategyWorker({package.package_hash}).run(
                            package, context
                        )
                        projected = tuple(
                            (
                                p["generated_position"][0],
                                p["generated_position"][1],
                                prepared.role_keys.index(p["role_key"]),
                            )
                            for p in formal["points"]
                            if p["role_key"] not in excluded
                        )
                        self.assertEqual(first.points, projected)
                        self.assertEqual(
                            first.point_ids,
                            tuple(
                                p["id"]
                                for p in formal["points"]
                                if p["role_key"] not in excluded
                            ),
                        )
                    self.assertEqual({p.name for p in root.iterdir()}, {"library"})

    def test_trust_parameters_and_role_rules_are_the_same_as_formal_execution(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            LibraryStore.open(Path(temporary) / "library") as store,
        ):
            package = inspect_strategy_package(
                ROOT / "strategy-packs/layered-ecological-community-v2"
            )
            _install(store, package, trust=False)
            service = StrategyPreviewService(StrategyExecutionAuthorizer(store, store))
            with self.assertRaises(StrategyAccessError):
                service.prepare(
                    StrategyPreviewRequest(package.package_hash), package.strategy
                )
            store.trust_strategy(
                package.package_hash, decided_by="test", reason="synthetic test"
            )
            for values, excluded in (
                ({"unknown": 1}, ()),
                ({"groundcover_spacing_m": 0}, ()),
                ({"groundcover_spacing_m": float("nan")}, ()),
                ({}, ("unknown",)),
                ({}, ("groundcover_matrix", "focal_structure")),
                ({}, ("focal_structure", "focal_structure")),
            ):
                with (
                    self.subTest(values=values, excluded=excluded),
                    self.assertRaises(ValueError),
                ):
                    service.prepare(
                        StrategyPreviewRequest(package.package_hash, values, excluded),
                        package.strategy,
                    )
            store.revoke_strategy(
                package.package_hash, decided_by="test", reason="test"
            )
            with self.assertRaises(StrategyAccessError):
                service.prepare(
                    StrategyPreviewRequest(package.package_hash), package.strategy
                )

    def test_cancellation_budget_empty_results_and_package_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            shutil.copytree(
                ROOT / "strategy-packs/universal-street-tree",
                source,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            package = inspect_strategy_package(source)
            with LibraryStore.open(root / "library") as store:
                _install(store, package)
                prepared = StrategyPreviewService(
                    StrategyExecutionAuthorizer(store, store)
                ).prepare(
                    StrategyPreviewRequest(package.package_hash), package.strategy
                )
            worker = StrategyPreviewWorker(package, root)
            with self.assertRaises(StrategyExecutionCancelled):
                worker.generate_example(prepared, cancellation_requested=lambda: True)
            empty = replace(
                prepared,
                excluded_role_keys=tuple(
                    key for key in prepared.role_keys if key != "narrow_center_shrub"
                ),
            )
            self.assertEqual(
                worker.generate_example(
                    empty, cancellation_requested=lambda: False
                ).points,
                (),
            )
            limited = StrategyPreviewWorker(
                package, root, StrategyPreviewWorkerConfig(max_points=1)
            )
            with self.assertRaises(StrategyExecutionError):
                limited.generate_example(prepared, cancellation_requested=lambda: False)
            entry = source / "generator.py"
            entry.write_bytes(entry.read_bytes() + b"\n# modified\n")
            with self.assertRaises(StrategyPackageError):
                worker.generate_example(prepared, cancellation_requested=lambda: False)
            self.assertFalse(
                any(path.name.startswith("example-") for path in root.iterdir())
            )

    def test_memory_runtime_cancels_running_and_queued_work_and_closes(self) -> None:
        runtime = StrategyPreviewRuntime()
        entered = Event()

        def slow(cancelled):
            entered.set()
            while not cancelled():
                time.sleep(0.005)
            raise StrategyExecutionCancelled("cancelled")

        operation = runtime.submit("hash", slow)
        self.assertTrue(entered.wait(1))
        self.assertEqual(runtime.cancel(operation.preview_id).status, "cancelled")
        self.assertIsNone(runtime.get(operation.preview_id).result)
        for _ in range(5):
            runtime.submit("hash", slow)
        runtime.close()
        with self.assertRaises(ValueError):
            runtime.submit("hash", slow)

    def test_local_api_is_projectless_authenticated_and_cancellable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = "example-preview-test-" + "x" * 32
            with SystemHost(SystemHostConfig(library_path=root / "library")) as host:
                strategy = host.install_strategy(
                    ROOT / "strategy-packs/layered-ecological-community-v2"
                )
                package_hash = strategy.summary.package_hash
                self.assertIsNotNone(strategy.parameter_preview)
                with LocalApiServer(
                    host, LocalApiConfig(management_token=token)
                ) as api:

                    def request(path, method="GET", body=None, authenticated=True):
                        headers = {"Content-Type": "application/json"}
                        if authenticated:
                            headers["Authorization"] = "Bearer " + token
                        req = Request(
                            api.base_url + path,
                            method=method,
                            headers=headers,
                            data=(
                                json.dumps(body).encode() if body is not None else None
                            ),
                        )
                        try:
                            with urlopen(req, timeout=5) as response:
                                return response.status, json.load(response)
                        except HTTPError as error:
                            return error.code, json.load(error)

                    body = {"package_hash": package_hash}
                    self.assertEqual(
                        request("/api/v1/strategy-previews", "POST", body, False)[0],
                        403,
                    )
                    self.assertEqual(
                        request("/api/v1/strategy-previews", "POST", body)[0], 403
                    )
                    host.trust_strategy(package_hash)
                    status, operation = request(
                        "/api/v1/strategy-previews", "POST", body
                    )
                    self.assertEqual(status, 202)
                    path = "/api/v1/strategy-previews/" + operation["preview_id"]
                    deadline = time.monotonic() + 15
                    while (
                        operation["status"] in {"pending", "running"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                        _, operation = request(path)
                    self.assertEqual(operation["status"], "succeeded", operation)
                    self.assertEqual(
                        operation["result"]["schema"],
                        "verdflow.strategy-example-preview",
                    )
                    self.assertEqual(request(path, "DELETE")[1]["status"], "cancelled")
                    self.assertEqual(
                        request(
                            "/api/v1/strategy-previews", "POST", {**body, "seed": 123}
                        )[0],
                        400,
                    )
                    self.assertEqual(host.list_projects(), ())


def _install(store, package, trust=True):
    store.install_verified_strategy(
        package.root,
        package_id=package.package_id,
        package_version=package.package_version,
        package_hash=package.package_hash,
        display_name=package.strategy["display_name"],
    )
    if trust:
        store.trust_strategy(
            package.package_hash, decided_by="test", reason="synthetic test"
        )


if __name__ == "__main__":
    unittest.main()
