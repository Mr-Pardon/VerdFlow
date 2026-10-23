"""Stateless Local API request documents; no Host or HTTP lifecycle."""

from __future__ import annotations
from typing import Any
from verdflow.contracts import (
    parse_strategy_preview_request,
    StrategyPreviewRequest,
    MaterialClassification,
    MaterialConfirmationMode,
    MaterialSelectionItem,
    MaterialSelectionMode,
    MaterialSelectionRequest,
    PlantRunReviewPolicy,
)
from verdflow.system_host import (
    MaterialFlowExecutionRequest,
    PlantExportRequest,
    PlantRunContinuationRequest,
    PlantRunExecutionRequest,
    PlantPreviewOutputRequest,
    PlantPreviewRequest,
    PlantingCompositionExportBatchRequest,
    PlantingCompositionExportRequest,
    RoleBindingProfileSaveRequest,
    ScenePreparationRequest,
)
from . import request_values


def strategy_preview_request(document: dict[str, Any]) -> StrategyPreviewRequest:
    return parse_strategy_preview_request(document)


def plant_run_request(
    document: dict[str, Any],
) -> PlantRunExecutionRequest:
    selected = document.get("selected_region_ids")
    if not isinstance(selected, list) or not selected or not all(
        isinstance(item, str) and item.strip() for item in selected
    ):
        raise ValueError(
            "selected_region_ids must be a non-empty string array"
        )
    review_policy_value = document.get(
        "review_policy",
        PlantRunReviewPolicy.AUTOMATIC.value,
    )
    try:
        review_policy = PlantRunReviewPolicy(review_policy_value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "review_policy must be required or automatic"
        ) from error
    offset = document.get("offset")
    if offset is not None and (
        not isinstance(offset, list) or len(offset) != 3
    ):
        raise ValueError("offset must contain three numbers")
    for field in (
        "generation_args",
        "elevation_options",
        "binding_overrides",
    ):
        value = document.get(field, {})
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be a JSON object")
    random_seed = document.get("random_seed")
    if not isinstance(random_seed, int) or isinstance(
        random_seed,
        bool,
    ):
        raise ValueError("random_seed must be an integer")
    assignment_seed = document.get("assignment_random_seed")
    if assignment_seed is not None and (
        not isinstance(assignment_seed, int)
        or isinstance(assignment_seed, bool)
    ):
        raise ValueError(
            "assignment_random_seed must be an integer"
        )
    for field in ("profile_id",):
        value = document.get(field)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(f"{field} must be a non-empty string")
    try:
        normalized_offset = (
            tuple(float(item) for item in offset)
            if offset is not None
            else None
        )
    except (TypeError, ValueError) as error:
        raise ValueError("offset must contain three numbers") from error
    return PlantRunExecutionRequest(
        scene_revision_id=request_values.required_text(
            document,
            "scene_revision_id",
        ),
        region_set_id=request_values.required_text(
            document,
            "region_set_id",
        ),
        selected_region_ids=tuple(
            item.strip() for item in selected
        ),
        strategy_package_hash=request_values.required_text(
            document,
            "strategy_package_hash",
        ),
        random_seed=random_seed,
        target_type=request_values.required_text(document, "target_type"),
        catalog_id=request_values.optional_text(document, "catalog_id"),
        review_policy=review_policy,
        elevation_provider_id=request_values.optional_text(
            document,
            "elevation_provider_id",
        ),
        export_provider_id=request_values.optional_text(
            document,
            "export_provider_id",
        ),
        offset=normalized_offset,
        profile_id=document.get("profile_id"),
        assignment_random_seed=assignment_seed,
        generation_args=document.get("generation_args", {}),
        elevation_options=document.get("elevation_options", {}),
        binding_overrides=document.get("binding_overrides", {}),
    )


def plant_preview_request(
    document: dict[str, Any],
) -> PlantPreviewRequest:
    forbidden = {
        "review_policy",
        "elevation_provider_id",
        "export_provider_id",
        "offset",
        "elevation_options",
    }.intersection(document)
    if forbidden:
        raise ValueError(
            "Plant preview output fields are not accepted: "
            + ", ".join(sorted(forbidden))
        )
    selected = document.get("selected_region_ids")
    if not isinstance(selected, list) or not selected or not all(
        isinstance(item, str) and item.strip() for item in selected
    ):
        raise ValueError(
            "selected_region_ids must be a non-empty string array"
        )
    for field in ("generation_args", "binding_overrides"):
        value = document.get(field, {})
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be a JSON object")
    random_seed = document.get("random_seed")
    if random_seed is not None and (
        not isinstance(random_seed, int)
        or isinstance(random_seed, bool)
    ):
        raise ValueError("random_seed must be an integer")
    assignment_seed = document.get("assignment_random_seed")
    if assignment_seed is not None and (
        not isinstance(assignment_seed, int)
        or isinstance(assignment_seed, bool)
    ):
        raise ValueError(
            "assignment_random_seed must be an integer"
        )
    profile_id = request_values.optional_text(document, "profile_id")
    return PlantPreviewRequest(
        scene_revision_id=request_values.required_text(
            document,
            "scene_revision_id",
        ),
        region_set_id=request_values.required_text(
            document,
            "region_set_id",
        ),
        selected_region_ids=tuple(
            item.strip() for item in selected
        ),
        strategy_package_hash=request_values.required_text(
            document,
            "strategy_package_hash",
        ),
        target_type=request_values.required_text(document, "target_type"),
        catalog_id=request_values.optional_text(document, "catalog_id"),
        random_seed=random_seed,
        profile_id=profile_id,
        assignment_random_seed=assignment_seed,
        generation_args=document.get("generation_args", {}),
        binding_overrides=document.get("binding_overrides", {}),
    )


def plant_preview_output_request(
    document: dict[str, Any],
) -> PlantPreviewOutputRequest:
    forbidden = {
        "elevation_provider_id",
        "export_provider_id",
        "elevation_options",
    }.intersection(document)
    if forbidden:
        raise ValueError(
            "Plant preview output providers are resolved by the system"
        )
    offset = document.get("offset", [0.0, 0.0, 0.0])
    if not isinstance(offset, list) or len(offset) != 3:
        raise ValueError("offset must contain three numbers")
    try:
        normalized_offset = tuple(float(item) for item in offset)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "offset must contain three numbers"
        ) from error
    from verdflow.planting import normalize_asset_selection
    return PlantPreviewOutputRequest(
        offset=normalized_offset,
        selected_asset_ids=normalize_asset_selection(document.get("selected_asset_ids")),
    )


def planting_composition_export_request(
    document: dict[str, Any],
) -> PlantingCompositionExportRequest:
    from verdflow.planting import normalize_asset_selection

    payload = dict(document)
    forbidden = {
        "elevation_provider_id",
        "export_provider_id",
        "elevation_options",
    }.intersection(payload)
    if forbidden:
        raise ValueError(
            "Planting composition export providers are resolved by "
            "the system"
        )
    offset = payload.pop("offset", [0.0, 0.0, 0.0])
    if not isinstance(offset, list) or len(offset) != 3:
        raise ValueError("offset must contain three numbers")
    try:
        normalized_offset = tuple(float(item) for item in offset)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "offset must contain three numbers"
        ) from error
    batch_documents = payload.pop("batches", None)
    request_values.reject_unknown(payload)
    if not isinstance(batch_documents, list) or not batch_documents:
        raise ValueError("batches must contain at least one batch")
    batches: list[PlantingCompositionExportBatchRequest] = []
    for item in batch_documents:
        if not isinstance(item, dict):
            raise ValueError("Each composition batch must be an object")
        batch = dict(item)
        batch_id = request_values.required_text(batch, "batch_id")
        batch.pop("batch_id", None)
        selected_asset_ids = normalize_asset_selection(
            batch.pop("selected_asset_ids", None)
        )
        request_values.reject_unknown(batch)
        batches.append(
            PlantingCompositionExportBatchRequest(
                batch_id=batch_id,
                selected_asset_ids=selected_asset_ids,
            )
        )
    return PlantingCompositionExportRequest(
        offset=normalized_offset,
        batches=tuple(batches),
    )


def plant_export_request(
    document: dict[str, Any],
) -> PlantExportRequest:
    forbidden = {
        "review_policy",
        "elevation_provider_id",
        "export_provider_id",
        "elevation_options",
    }.intersection(document)
    if forbidden:
        raise ValueError(
            "Plant export providers and review policy are resolved "
            "by the system"
        )
    offset = document.pop("offset", [0.0, 0.0, 0.0])
    if not isinstance(offset, list) or len(offset) != 3:
        raise ValueError("offset must contain three numbers")
    try:
        normalized_offset = tuple(float(item) for item in offset)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "offset must contain three numbers"
        ) from error
    return PlantExportRequest(
        planting=plant_preview_request(document),
        offset=normalized_offset,
    )


def plant_run_continuation(
    document: dict[str, Any],
) -> PlantRunContinuationRequest:
    offset = document.get("offset")
    if not isinstance(offset, list) or len(offset) != 3:
        raise ValueError("offset must contain three numbers")
    elevation_options = document.get("elevation_options", {})
    if not isinstance(elevation_options, dict):
        raise ValueError("elevation_options must be a JSON object")
    try:
        normalized_offset = tuple(float(item) for item in offset)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "offset must contain three numbers"
        ) from error
    return PlantRunContinuationRequest(
        elevation_provider_id=request_values.required_text(
            document,
            "elevation_provider_id",
        ),
        export_provider_id=request_values.required_text(
            document,
            "export_provider_id",
        ),
        offset=normalized_offset,
        elevation_options=elevation_options,
    )


def material_flow_request(
    document: dict[str, Any],
) -> MaterialFlowExecutionRequest:
    mode_value = document.get(
        "confirmation_mode",
        MaterialConfirmationMode.MANUAL.value,
    )
    if not isinstance(mode_value, str):
        raise ValueError(
            "confirmation_mode must be manual or automatic"
        )
    allow_contains = document.get("allow_contains", False)
    import_settings = document.get("import_settings", {})
    if not isinstance(allow_contains, bool):
        raise ValueError("allow_contains must be a boolean")
    if not isinstance(import_settings, dict):
        raise ValueError("import_settings must be a JSON object")
    selection_mode = MaterialSelectionMode(
        document.get(
            "selection_mode",
            MaterialSelectionMode.AI_ASSISTED.value,
        )
    )
    return MaterialFlowExecutionRequest(
        scene_revision_id=request_values.required_text(
            document,
            "scene_revision_id",
        ),
        analysis_provider_id=request_values.optional_text(
            document,
            "analysis_provider_id",
        ),
        region_provider_id=request_values.required_text(
            document,
            "region_provider_id",
        ),
        confirmation_mode=MaterialConfirmationMode(mode_value),
        selection_mode=selection_mode,
        model=request_values.optional_text(document, "model"),
        allow_contains=allow_contains,
        import_settings=import_settings,
    )


def scene_preparation_request(
    document: dict[str, Any],
) -> ScenePreparationRequest:
    mode_value = document.get(
        "confirmation_mode",
        MaterialConfirmationMode.MANUAL.value,
    )
    if not isinstance(mode_value, str):
        raise ValueError(
            "confirmation_mode must be manual or automatic"
        )
    allow_contains = document.get("allow_contains", False)
    import_settings = document.get("import_settings", {})
    if not isinstance(allow_contains, bool):
        raise ValueError("allow_contains must be a boolean")
    if not isinstance(import_settings, dict):
        raise ValueError("import_settings must be a JSON object")
    selection_mode = MaterialSelectionMode(
        document.get(
            "selection_mode",
            MaterialSelectionMode.AI_ASSISTED.value,
        )
    )
    return ScenePreparationRequest(
        source_id=request_values.required_text(document, "source_id"),
        analysis_provider_id=request_values.optional_text(
            document,
            "analysis_provider_id",
        ),
        region_provider_id=request_values.required_text(
            document,
            "region_provider_id",
        ),
        confirmation_mode=MaterialConfirmationMode(mode_value),
        selection_mode=selection_mode,
        model=request_values.optional_text(document, "model"),
        allow_contains=allow_contains,
        import_settings=import_settings,
    )


def role_binding_profile_save_request(
    document: dict[str, Any],
) -> RoleBindingProfileSaveRequest:
    assignments = document.get("assignments")
    if not isinstance(assignments, dict) or not assignments:
        raise ValueError("assignments must be a non-empty object")
    return RoleBindingProfileSaveRequest(
        profile_id=request_values.optional_text(document, "profile_id"),
        source_profile_id=request_values.optional_text(document, "source_profile_id"),
        display_name=request_values.required_text(
            document,
            "display_name",
        ),
        strategy_id=request_values.required_text(
            document,
            "strategy_id",
        ),
        strategy_version=request_values.required_text(
            document,
            "strategy_version",
        ),
        target_type=request_values.required_text(
            document,
            "target_type",
        ),
        catalog_id=request_values.optional_text(document, "catalog_id"),
        assignments=assignments,
        strategy_parameters=document.get("strategy_parameters"),
        replace_strategy_parameters="strategy_parameters" in document,
        expected_generation=request_values.required_integer(
            document,
            "expected_generation",
            minimum=0,
        ),
    )


def material_selection(
    document: dict[str, Any],
) -> MaterialSelectionRequest | None:
    rows = document.get("materials")
    if rows is None:
        return None
    if not isinstance(rows, list) or not rows:
        raise ValueError("materials must be a non-empty array")
    items: list[MaterialSelectionItem] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("material selection item must be an object")
        items.append(
            MaterialSelectionItem(
                material_name=request_values.required_text(
                    row,
                    "material_name",
                ),
                classification=MaterialClassification(
                    request_values.required_text(row, "classification")
                ),
            )
        )
    return MaterialSelectionRequest(materials=tuple(items))
