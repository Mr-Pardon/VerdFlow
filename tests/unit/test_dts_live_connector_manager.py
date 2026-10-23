from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
]

from verdflow.dts_adapter import (  # noqa: E402
    DtsLiveExecutionSnapshot,
    DtsLiveOperationStage,
)
from verdflow.system_host import (  # noqa: E402
    DtsLiveAuthorizationError,
    DtsLiveConfigurationRequest,
    DtsLiveConnectorManager,
    DtsLiveOperationBusyError,
    DtsLiveSessionNotFoundError,
)


class DtsLiveConnectorManagerTests(unittest.TestCase):
    def test_bootstrap_is_single_use_and_access_token_is_required(self) -> None:
        manager = DtsLiveConnectorManager(_resolve)
        bootstrap = manager.create_session()
        grant = manager.exchange_bootstrap(
            bootstrap.connector_session_id,
            bootstrap.bootstrap_nonce,
        )

        with self.assertRaises(DtsLiveAuthorizationError):
            manager.exchange_bootstrap(
                bootstrap.connector_session_id,
                bootstrap.bootstrap_nonce,
            )
        with self.assertRaises(DtsLiveAuthorizationError):
            manager.snapshot(bootstrap.connector_session_id, "wrong")
        self.assertIsNone(
            manager.snapshot(
                bootstrap.connector_session_id,
                grant.access_token,
            ).stage
        )

    def test_default_bootstrap_allows_slow_explorer_startup_then_expires(self) -> None:
        now = [100.0]
        manager = DtsLiveConnectorManager(_resolve, clock=lambda: now[0])
        slow_start = manager.create_session()

        now[0] += 14 * 60
        manager.exchange_bootstrap(
            slow_start.connector_session_id,
            slow_start.bootstrap_nonce,
        )

        expired = manager.create_session()
        now[0] += 15 * 60 + 1
        with self.assertRaises(DtsLiveAuthorizationError):
            manager.exchange_bootstrap(
                expired.connector_session_id,
                expired.bootstrap_nonce,
            )

    def test_configuration_is_resolved_once_and_locked_during_operation(self) -> None:
        requests: list[DtsLiveConfigurationRequest] = []

        def resolver(request: DtsLiveConfigurationRequest):
            requests.append(request)
            return _snapshot(request)

        manager = DtsLiveConnectorManager(resolver)
        bootstrap = manager.create_session()
        grant = manager.exchange_bootstrap(
            bootstrap.connector_session_id,
            bootstrap.bootstrap_nonce,
        )
        request = _request()
        command = manager.begin_selection(
            bootstrap.connector_session_id,
            grant.access_token,
            request,
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(command.kind.value, "begin_polygon_selection")
        frozen = manager.snapshot(
            bootstrap.connector_session_id,
            grant.access_token,
        ).execution
        self.assertIsNotNone(frozen)
        assert frozen is not None
        self.assertEqual(frozen.strategy_package_hash, HASH)
        with self.assertRaises(DtsLiveOperationBusyError):
            manager.begin_selection(
                bootstrap.connector_session_id,
                grant.access_token,
                request,
            )
        self.assertEqual(len(requests), 1)

        manager.cancel(bootstrap.connector_session_id, grant.access_token)
        manager.begin_selection(
            bootstrap.connector_session_id,
            grant.access_token,
            request,
        )
        self.assertEqual(len(requests), 2)

    def test_regeneration_resolves_configuration_and_reuses_selection(self) -> None:
        requests: list[DtsLiveConfigurationRequest] = []

        def resolver(request: DtsLiveConfigurationRequest):
            requests.append(request)
            return _snapshot(request)

        manager = DtsLiveConnectorManager(resolver)
        bootstrap = manager.create_session()
        grant = manager.exchange_bootstrap(
            bootstrap.connector_session_id,
            bootstrap.bootstrap_nonce,
        )
        command = manager.begin_selection(
            bootstrap.connector_session_id,
            grant.access_token,
            _request(),
        )
        accepted = manager.accept_selection(
            bootstrap.connector_session_id,
            grant.access_token,
            command.correlation,
            [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
        )
        manager.cancel(bootstrap.connector_session_id, grant.access_token)

        regenerated = manager.regenerate(
            bootstrap.connector_session_id,
            grant.access_token,
            _request(),
        )

        self.assertEqual(regenerated.regions, accepted.regions)
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            manager.snapshot(
                bootstrap.connector_session_id,
                grant.access_token,
            ).stage,
            DtsLiveOperationStage.SELECTION_READY,
        )
        with self.assertRaises(DtsLiveOperationBusyError):
            manager.regenerate(
                bootstrap.connector_session_id,
                grant.access_token,
                _request(),
            )

    def test_expired_session_is_removed_without_persisted_state(self) -> None:
        now = [100.0]
        manager = DtsLiveConnectorManager(
            _resolve,
            session_ttl_seconds=5,
            clock=lambda: now[0],
        )
        bootstrap = manager.create_session()
        grant = manager.exchange_bootstrap(
            bootstrap.connector_session_id,
            bootstrap.bootstrap_nonce,
        )
        now[0] = 106.0

        with self.assertRaises(DtsLiveSessionNotFoundError):
            manager.snapshot(
                bootstrap.connector_session_id,
                grant.access_token,
            )


HASH = "sha256:" + "a" * 64
PROFILE_HASH = "sha256:" + "b" * 64
CATALOG_HASH = "sha256:" + "c" * 64
EFFECTIVE_HASH = "sha256:" + "d" * 64


def _request() -> DtsLiveConfigurationRequest:
    return DtsLiveConfigurationRequest(
        strategy_package_hash=HASH,
        profile_id="profile",
    )


def _resolve(request: DtsLiveConfigurationRequest) -> DtsLiveExecutionSnapshot:
    return _snapshot(request)


def _snapshot(
    request: DtsLiveConfigurationRequest,
) -> DtsLiveExecutionSnapshot:
    return DtsLiveExecutionSnapshot(
        strategy_package_hash=request.strategy_package_hash,
        strategy_id="strategy",
        strategy_version="1.0.0",
        profile_id=request.profile_id,
        profile_semantic_hash=PROFILE_HASH,
        target_type="dts",
        asset_source_kind="profile",
        asset_source_id=request.profile_id or "profile",
        asset_source_generation=None,
        asset_source_hash=PROFILE_HASH,
        effective_binding_semantic_hash=EFFECTIVE_HASH,
    )


if __name__ == "__main__":
    unittest.main()
