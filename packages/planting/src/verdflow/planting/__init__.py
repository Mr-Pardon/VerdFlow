"""Strategy authoring and deterministic target asset assignment."""

from .authoring import load_catalog_yaml, load_profile_yaml, load_strategy_yaml
from .canonical import CanonicalRowHasher, canonical_array_chunks, canonical_json_bytes, point_set_row_hasher, semantic_hash
from .path_inputs import strategy_input_kind, validate_path_document
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
from .paired_rows import output_row_selected, paired_planting_rows
from .parameters import profile_generation_args, validate_profile_parameters
from .point_stream import (
    AssignmentSetReader, PointSetReader, canonical_point_document, encoded_point_rows, validated_point_rows,
)
from .strategy_preview import strategy_preview_sample
from .resolver import (
    resolve_asset_assignments, resolve_persisted_asset_assignments, resolve_assignment_preview,
    write_persisted_asset_assignments,
)
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
    validate_assignment_header,
    validate_assignment_row,
    validate_effective_binding_snapshot,
    validate_persisted_point_set_summary,
    validate_elevation_headers,
    validate_elevation_point_set,
    validate_elevation_row,
    validate_point_header,
    validate_point_set_v2,
    validate_point_set,
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
    "PointSetReader",
    "AssignmentSetReader",
    "paired_planting_rows",
    "output_row_selected",
    "CanonicalRowHasher",
    "point_set_row_hasher",
    "canonical_point_document",
    "encoded_point_rows",
    "validated_point_rows",
    "validate_point_header",
    "profile_generation_args",
    "validate_profile_parameters",
    "build_effective_binding_snapshot",
    "build_effective_binding_from_profile",
    "canonical_json_bytes",
    "canonical_array_chunks",
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
    "write_persisted_asset_assignments",
    "semantic_hash",
    "validate_target_asset_use_snapshot",
    "validate_assignment_set",
    "validate_assignment_header",
    "validate_assignment_row",
    "validate_effective_binding_snapshot",
    "validate_persisted_point_set_summary",
    "validate_elevation_headers",
    "validate_elevation_point_set",
    "validate_elevation_row",
    "validate_point_set_v2",
    "validate_point_set",
    "strategy_input_kind",
    "validate_path_document",
]
