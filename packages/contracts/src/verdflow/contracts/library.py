"""User Library contracts shared by Core and Infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re


PACKAGE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
LIBRARY_RESOURCE_ID = re.compile(r"^res_[0-9a-f]{64}$")
LIBRARY_RESOURCE_REFERENCE = re.compile(
    r"^verdflow-resource:(res_[0-9a-f]{64})$"
)
@dataclass(frozen=True, slots=True)
class LibraryStrategyInfo:
    """Installed strategy metadata without storage implementation details."""

    library_entry_id: str
    package_id: str
    package_version: str
    package_hash: str
    display_name: str

    def __post_init__(self) -> None:
        _require_text(self.library_entry_id, "library_entry_id")
        _require_text(self.package_id, "package_id")
        _require_text(self.package_version, "package_version")
        require_package_hash(self.package_hash)
        _require_text(self.display_name, "display_name")


class StrategyTrustState(str, Enum):
    TRUSTED = "trusted"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class StrategyTrustDecision:
    """A user decision bound to one exact strategy package hash."""

    package_hash: str
    state: StrategyTrustState
    decided_at: str
    decided_by: str

    def __post_init__(self) -> None:
        require_package_hash(self.package_hash)
        if not isinstance(self.state, StrategyTrustState):
            raise ValueError("strategy trust state is invalid")
        _require_text(self.decided_at, "decided_at")
        _require_text(self.decided_by, "decided_by")


@dataclass(frozen=True, slots=True)
class AssetCatalogDocument:
    """Validated canonical current Catalog bytes prepared by Planting."""

    target_type: str
    provider_id: str
    catalog_id: str
    display_name: str
    semantic_hash: str
    canonical_json: bytes

    def __post_init__(self) -> None:
        _require_text(self.target_type, "target_type")
        _require_text(self.provider_id, "provider_id")
        _require_text(self.catalog_id, "catalog_id")
        _require_text(self.display_name, "display_name")
        _require_canonical_payload(self.semantic_hash, self.canonical_json)


@dataclass(frozen=True, slots=True)
class AssetCatalogInfo:
    """One mutable local Catalog; generation is concurrency metadata only."""

    catalog_entry_id: str
    target_type: str
    provider_id: str
    catalog_id: str
    display_name: str
    generation: int
    semantic_hash: str
    byte_size: int
    created_at: str
    updated_at: str
    origin_package_hash: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("catalog_entry_id", self.catalog_entry_id),
            ("target_type", self.target_type),
            ("provider_id", self.provider_id),
            ("catalog_id", self.catalog_id),
            ("display_name", self.display_name),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            _require_text(value, field)
        if not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        require_sha256_hash(self.semantic_hash)
        if not isinstance(self.byte_size, int) or self.byte_size <= 0:
            raise ValueError("byte_size must be a positive integer")
        if self.origin_package_hash is not None:
            require_package_hash(self.origin_package_hash)


@dataclass(frozen=True, slots=True)
class AssetCatalogRemovalInfo:
    target_type: str
    catalog_id: str
    removed: bool
    affected_profiles: int = 0

    def __post_init__(self) -> None:
        _require_text(self.target_type, "target_type")
        _require_text(self.catalog_id, "catalog_id")
        if not isinstance(self.removed, bool):
            raise ValueError("removed must be a boolean")
        if self.affected_profiles < 0:
            raise ValueError("affected_profiles must not be negative")


@dataclass(frozen=True, slots=True)
class LibraryResourceInfo:
    """Managed immutable bytes shared by user-library documents."""

    resource_id: str
    content_hash: str
    media_type: str
    byte_size: int
    created_at: str

    def __post_init__(self) -> None:
        require_library_resource_id(self.resource_id)
        require_sha256_hash(self.content_hash)
        _require_text(self.media_type, "media_type")
        if not isinstance(self.byte_size, int) or self.byte_size <= 0:
            raise ValueError("byte_size must be a positive integer")
        _require_text(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class RoleBindingProfileDocument:
    """Validated self-contained mutable Profile prepared by Planting."""

    profile_id: str
    display_name: str
    strategy_id: str
    strategy_version: str
    target_type: str
    provider_id: str
    catalog_id: str | None
    semantic_hash: str
    canonical_json: bytes

    def __post_init__(self) -> None:
        for field, value in (
            ("profile_id", self.profile_id),
            ("display_name", self.display_name),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
            ("target_type", self.target_type),
            ("provider_id", self.provider_id),
        ):
            _require_text(value, field)
        if self.catalog_id is not None:
            _require_text(self.catalog_id, "catalog_id")
        _require_canonical_payload(self.semantic_hash, self.canonical_json)


@dataclass(frozen=True, slots=True)
class RoleBindingProfileInfo:
    profile_entry_id: str
    profile_id: str
    display_name: str
    strategy_id: str
    strategy_version: str
    target_type: str
    provider_id: str
    catalog_id: str | None
    generation: int
    semantic_hash: str
    byte_size: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        for field, value in (
            ("profile_entry_id", self.profile_entry_id),
            ("profile_id", self.profile_id),
            ("display_name", self.display_name),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
            ("target_type", self.target_type),
            ("provider_id", self.provider_id),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            _require_text(value, field)
        if self.catalog_id is not None:
            _require_text(self.catalog_id, "catalog_id")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        require_sha256_hash(self.semantic_hash)
        if self.byte_size <= 0:
            raise ValueError("byte_size must be positive")


def require_package_hash(value: str) -> None:
    try:
        require_sha256_hash(value)
    except ValueError as error:
        raise ValueError("package_hash must use sha256:<64 lowercase hex>") from error


def require_library_resource_id(value: str) -> None:
    if not isinstance(value, str) or not LIBRARY_RESOURCE_ID.fullmatch(value):
        raise ValueError("resource_id must use res_<64 lowercase hex>")


def library_resource_reference(resource_id: str) -> str:
    require_library_resource_id(resource_id)
    return f"verdflow-resource:{resource_id}"


def parse_library_resource_reference(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("library resource reference must be a string")
    matched = LIBRARY_RESOURCE_REFERENCE.fullmatch(value)
    if matched is None:
        raise ValueError(
            "library resource reference must use verdflow-resource:res_<sha256>"
        )
    return matched.group(1)


def require_sha256_hash(value: str) -> None:
    if not isinstance(value, str) or not PACKAGE_HASH.fullmatch(value):
        raise ValueError("hash must use sha256:<64 lowercase hex>")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")


def _require_canonical_payload(semantic_hash: str, payload: bytes) -> None:
    require_sha256_hash(semantic_hash)
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("canonical_json must be non-empty bytes")
    actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if actual != semantic_hash:
        raise ValueError("semantic_hash does not match canonical_json bytes")
