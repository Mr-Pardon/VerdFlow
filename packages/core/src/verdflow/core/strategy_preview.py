"""Authorize and normalize display-only strategy examples without a project."""

from __future__ import annotations

from typing import Any, Callable, Protocol

from verdflow.contracts import (
    StrategyPreviewInput,
    StrategyPreviewRequest,
    StrategyPreviewResult,
)
from verdflow.planting import strategy_preview_sample, validate_profile_parameters

from .strategy_access import StrategyExecutionAuthorizer


class StrategyPreviewPort(Protocol):
    def generate_example(
        self,
        request: StrategyPreviewInput,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> StrategyPreviewResult: ...


class StrategyPreviewService:
    """Same trust and public parameter rules; never creates a PlantingRun."""

    def __init__(self, authorizer: StrategyExecutionAuthorizer) -> None:
        self._authorizer = authorizer

    def prepare(
        self, request: StrategyPreviewRequest, strategy: dict[str, Any]
    ) -> StrategyPreviewInput:
        authorization = self._authorizer.authorize(request.package_hash)
        if (strategy.get("strategy_id"), strategy.get("strategy_version")) != (
            authorization.strategy.package_id,
            authorization.strategy.package_version,
        ):
            raise ValueError(
                "Preview strategy identity does not match the authorized package"
            )
        sample = strategy_preview_sample(strategy)
        if sample is None:
            raise ValueError("This strategy does not provide a parameter example")
        validate_profile_parameters(
            strategy, {"strategy_parameters": request.strategy_parameters}
        )
        roles = tuple(item["role_key"] for item in strategy["roles"])
        excluded = request.excluded_role_keys
        if (
            not isinstance(excluded, tuple)
            or any(not isinstance(role, str) for role in excluded)
            or len(set(excluded)) != len(excluded)
            or set(excluded) - set(roles)
            or not set(roles) - set(excluded)
        ):
            raise ValueError("Preview must retain at least one declared role")
        values = {
            item["key"]: item["default"]
            for item in strategy.get("generation_parameters", [])
        }
        values.update(request.strategy_parameters)
        return StrategyPreviewInput(
            request.package_hash,
            sample,
            tuple(sorted(values.items())),
            roles,
            tuple(sorted(excluded)),
        )

    @staticmethod
    def run(
        request: StrategyPreviewInput,
        port: StrategyPreviewPort,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> StrategyPreviewResult:
        return port.generate_example(
            request, cancellation_requested=cancellation_requested
        )
