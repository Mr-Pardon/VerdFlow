"""Strategy authoring and deterministic target asset assignment."""

from .authoring import load_catalog_yaml, load_profile_yaml, load_strategy_yaml
from .canonical import canonical_json_bytes, semantic_hash
from .binding_drafts import build_binding_draft, catalog_binding_candidate, profile_from_binding_draft
from .binding_profiles import (
    binding_catalog_drift,
    build_profile_v2,
    catalog_asset_hashes,
    sync_profile_catalog_metadata,
    validate_binding_strategy,
    complete_profile_assignments,
    update_profile_v2,
)
from .documents import (
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
)
from .effective import (
    build_effective_binding_from_draft,
    build_effective_binding_from_profile,
    build_effective_binding_snapshot,
)
from .errors import PlantingDocumentError
from .parameters import profile_generation_args, validate_profile_parameters
from .strategy_preview import strategy_preview_sample
from .resolver import resolve_asset_assignments, resolve_persisted_asset_assignments, resolve_assignment_preview
from .selection import (
    filter_point_roles,
    normalize_asset_selection,
    role_enabled,
    select_output_assets,
    select_persisted_output_assets,
)
from .snapshots import extract_target_asset_use_snapshot
from .validation import (
    validate_profile,
    validate_target_asset_use_snapshot,
    validate_assignment_set,
    validate_effective_binding_snapshot,
    validate_persisted_point_set_summary,
    validate_elevation_point_set,
    validate_point_set_v2,
)

__all__ = [
    "validate_profile",
    "build_binding_draft",
    "catalog_binding_candidate",
    "profile_from_binding_draft",
    "build_effective_binding_from_draft",
    "resolve_assignment_preview",
    "strategy_preview_sample",
    "filter_point_roles",
    "normalize_asset_selection",
    "role_enabled",
    "select_output_assets",
    "select_persisted_output_assets",
    "PlantingDocumentError",
    "profile_generation_args",
    "validate_profile_parameters",
    "build_effective_binding_snapshot",
    "build_effective_binding_from_profile",
    "canonical_json_bytes",
    "binding_catalog_drift",
    "build_profile_v2",
    "update_profile_v2",
    "catalog_asset_hashes",
    "sync_profile_catalog_metadata",
    "validate_binding_strategy",
    "complete_profile_assignments",
    "extract_target_asset_use_snapshot",
    "load_catalog_yaml",
    "load_profile_yaml",
    "load_strategy_yaml",
    "prepare_asset_catalog_document",
    "prepare_role_binding_profile_document",
    "resolve_asset_assignments",
    "resolve_persisted_asset_assignments",
    "semantic_hash",
    "validate_target_asset_use_snapshot",
    "validate_assignment_set",
    "validate_effective_binding_snapshot",
    "validate_persisted_point_set_summary",
    "validate_elevation_point_set",
    "validate_point_set_v2",
]
