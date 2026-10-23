from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.core import (  # noqa: E402
    LibraryStrategyInfo,
    StrategyAccessError,
    StrategyExecutionAuthorizer,
    StrategyTrustDecision,
    StrategyTrustState,
)


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


@dataclass
class FakeLibrary:
    strategy: LibraryStrategyInfo | None

    def get_strategy_by_hash(self, package_hash: str) -> LibraryStrategyInfo | None:
        return self.strategy


@dataclass
class FakeTrustRegistry:
    decision: StrategyTrustDecision | None

    def get_strategy_trust(self, package_hash: str) -> StrategyTrustDecision | None:
        return self.decision


def strategy(package_hash: str = HASH_A) -> LibraryStrategyInfo:
    return LibraryStrategyInfo(
        library_entry_id="strategy-entry-1",
        package_id="universal_natural_grove",
        package_version="2.0.0",
        package_hash=package_hash,
        display_name="通用自然聚落",
    )


def decision(
    state: StrategyTrustState,
    package_hash: str = HASH_A,
) -> StrategyTrustDecision:
    return StrategyTrustDecision(
        package_hash=package_hash,
        state=state,
        decided_at="2026-08-05T12:00:00Z",
        decided_by="local-user",
    )


class StrategyAccessTests(unittest.TestCase):
    def test_exact_installed_hash_and_manual_trust_authorize_execution(self) -> None:
        authorization = StrategyExecutionAuthorizer(
            FakeLibrary(strategy()),
            FakeTrustRegistry(decision(StrategyTrustState.TRUSTED)),
        ).authorize(HASH_A)

        self.assertEqual(authorization.strategy.package_hash, HASH_A)
        self.assertEqual(authorization.trust.state, StrategyTrustState.TRUSTED)

    def test_installed_or_project_snapshotted_package_is_not_implicitly_trusted(self) -> None:
        authorizer = StrategyExecutionAuthorizer(
            FakeLibrary(strategy()),
            FakeTrustRegistry(None),
        )

        with self.assertRaises(StrategyAccessError) as captured:
            authorizer.authorize(HASH_A)

        self.assertEqual(captured.exception.code, "strategy_trust_required")

    def test_revoked_or_mismatched_trust_is_rejected(self) -> None:
        with self.assertRaises(StrategyAccessError) as revoked:
            StrategyExecutionAuthorizer(
                FakeLibrary(strategy()),
                FakeTrustRegistry(decision(StrategyTrustState.REVOKED)),
            ).authorize(HASH_A)
        self.assertEqual(revoked.exception.code, "strategy_trust_revoked")

        with self.assertRaises(StrategyAccessError) as mismatch:
            StrategyExecutionAuthorizer(
                FakeLibrary(strategy()),
                FakeTrustRegistry(decision(StrategyTrustState.TRUSTED, HASH_B)),
            ).authorize(HASH_A)
        self.assertEqual(mismatch.exception.code, "strategy_trust_mismatch")

    def test_missing_package_and_malformed_hash_are_rejected(self) -> None:
        authorizer = StrategyExecutionAuthorizer(
            FakeLibrary(None),
            FakeTrustRegistry(None),
        )
        with self.assertRaises(StrategyAccessError) as missing:
            authorizer.authorize(HASH_A)
        self.assertEqual(missing.exception.code, "strategy_not_installed")

        with self.assertRaisesRegex(ValueError, "package_hash"):
            authorizer.authorize("universal_natural_grove@2.0.0")

    def test_library_cannot_substitute_a_different_package_hash(self) -> None:
        with self.assertRaises(StrategyAccessError) as mismatch:
            StrategyExecutionAuthorizer(
                FakeLibrary(strategy(HASH_B)),
                FakeTrustRegistry(decision(StrategyTrustState.TRUSTED)),
            ).authorize(HASH_A)
        self.assertEqual(mismatch.exception.code, "strategy_library_mismatch")


if __name__ == "__main__":
    unittest.main()
