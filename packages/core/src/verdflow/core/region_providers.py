"""Shared execution identity for providers that produce a RegionSet."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegionSetProducerDefinition:
    """Common Worker command binding for typed Region-producing providers."""

    provider_id: str
    provider_version: str
    command_type: str
    command_version: str
    max_attempts: int = 2

    def validate(self) -> None:
        for field_name in (
            "provider_id",
            "provider_version",
            "command_type",
            "command_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            if value != value.strip():
                raise ValueError(f"{field_name} cannot contain outer whitespace")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
