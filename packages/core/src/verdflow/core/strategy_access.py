"""Authorize trusted-local execution from user-library facts."""

from __future__ import annotations

from dataclasses import dataclass

from .library import (
    LibraryStrategyInfo,
    StrategyTrustDecision,
    StrategyTrustPort,
    StrategyTrustState,
    UserLibraryPort,
    require_package_hash,
)


class StrategyAccessError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StrategyExecutionAuthorization:
    strategy: LibraryStrategyInfo
    trust: StrategyTrustDecision


class StrategyExecutionAuthorizer:
    """Require an installed package and explicit trust for its exact hash."""

    def __init__(
        self,
        library: UserLibraryPort,
        trust: StrategyTrustPort,
    ) -> None:
        self._library = library
        self._trust = trust

    def authorize(self, package_hash: str) -> StrategyExecutionAuthorization:
        require_package_hash(package_hash)
        strategy = self._library.get_strategy_by_hash(package_hash)
        if strategy is None:
            raise StrategyAccessError(
                "strategy_not_installed",
                f"Strategy package is not installed: {package_hash}",
            )
        if strategy.package_hash != package_hash:
            raise StrategyAccessError(
                "strategy_library_mismatch",
                "User Library returned a strategy for a different package hash",
            )

        decision = self._trust.get_strategy_trust(package_hash)
        if decision is None:
            raise StrategyAccessError(
                "strategy_trust_required",
                f"Strategy package requires explicit user trust: {package_hash}",
            )
        if decision.package_hash != package_hash:
            raise StrategyAccessError(
                "strategy_trust_mismatch",
                "Trust Registry returned a decision for a different package hash",
            )
        if decision.state is StrategyTrustState.REVOKED:
            raise StrategyAccessError(
                "strategy_trust_revoked",
                f"Strategy package trust was revoked: {package_hash}",
            )
        if decision.state is not StrategyTrustState.TRUSTED:
            raise StrategyAccessError(
                "strategy_trust_required",
                f"Strategy package is not trusted: {package_hash}",
            )
        return StrategyExecutionAuthorization(strategy=strategy, trust=decision)
