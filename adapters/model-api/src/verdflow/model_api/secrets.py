"""Secret resolution ports; values never enter provider descriptors."""

from __future__ import annotations

import os
from typing import Mapping, Protocol

from .errors import ModelApiSecretError
from .provider_config import ModelApiProviderRegistry


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str: ...


class EnvironmentSecretResolver:
    """Development-only resolver used until the System SecretStore exists."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        *,
        provider_registry: ModelApiProviderRegistry | None = None,
    ) -> None:
        self._environment = environment if environment is not None else os.environ
        self._provider_registry = provider_registry or ModelApiProviderRegistry()

    def resolve(self, secret_ref: str) -> str:
        for provider in self._provider_registry.list():
            if provider.secret_ref != secret_ref:
                continue
            environment_name = provider.development_environment_variable
            if environment_name is None:
                raise ModelApiSecretError(
                    f"Development secret mapping is not configured for reference: {secret_ref}"
                )
            value = self._environment.get(
                environment_name,
                "",
            ).strip()
            if not value:
                raise ModelApiSecretError(
                    f"Secret is not configured for reference: {secret_ref}"
                )
            return value
        raise ModelApiSecretError(f"Unknown secret reference: {secret_ref}")

    def resolve_provider(self, provider_id: str) -> str:
        return self.resolve(self._provider_registry.get(provider_id).secret_ref)
