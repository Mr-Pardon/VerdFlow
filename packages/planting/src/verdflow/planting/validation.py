"""Versioned document validation and Phase 2A domain invariants."""

from __future__ import annotations

import copy
import math
from functools import lru_cache
from importlib.resources import files
import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from verdflow.contracts import parse_library_resource_reference

from .canonical import semantic_hash
from .errors import PlantingDocumentError
from .parameters import validate_parameter_values


SCHEMAS = {
    "strategy": "strategy-v2.schema.json",
    "profile": "role-binding-profile-v2.schema.json",
    "catalog": "target-asset-catalog-v1.schema.json",
    "points_v2": "point-set-v2.schema.json",
    "effective_binding": "effective-binding-snapshot-v1.schema.json",
    "target_asset_use_snapshot": "target-asset-use-snapshot-v1.schema.json",
    "assignment_set": "asset-assignment-set-v1.schema.json",
}


def validate_document(value: Any, schema_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlantingDocumentError("Document root must be an object")
    validator = _validator(schema_name)
    errors = sorted(validator.iter_errors(value), key=_error_sort_key)
    if errors:
        error = errors[0]
        path = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise PlantingDocumentError(f"{path}: {error.message}")
    return value


def validate_strategy(value: Any) -> dict[str, Any]:
    document = validate_document(value, "strategy")
    _require_unique(document["roles"], "role_key", "strategy roles")
    generation_parameters = document.get("generation_parameters", [])
    role_keys = {role["role_key"] for role in document["roles"]}
    _require_unique(
        generation_parameters,
        "key",
        "strategy generation parameters",
    )
    for parameter in generation_parameters:
        unknown_roles = set(parameter.get("visible_for_roles", [])) - role_keys
        if unknown_roles:
            raise PlantingDocumentError(
                f"generation parameter {parameter['key']!r} references unknown roles: "
                + ", ".join(sorted(unknown_roles))
            )
        minimum = float(parameter["min"])
        maximum = float(parameter["max"])
        default = float(parameter["default"])
        step = float(parameter["step"])
        if not all(math.isfinite(item) for item in (minimum, maximum, default, step)):
            raise PlantingDocumentError(
                f"generation parameter {parameter['key']!r} requires finite numbers"
            )
        if minimum > maximum or not minimum <= default <= maximum:
            raise PlantingDocumentError(
                f"generation parameter {parameter['key']!r} requires min <= default <= max"
            )
    return document


def validate_profile(value: Any) -> dict[str, Any]:
    document = validate_document(value, "profile")
    return _validate_self_contained_profile(document)


def validate_binding_draft(value: Any) -> dict[str, Any]:
    """Validate executable choices without inventing a Library Profile."""
    return _validate_self_contained_profile(validate_document(value, "binding_draft"))


def _validate_self_contained_profile(document: dict[str, Any]) -> dict[str, Any]:
    if "strategy_parameters" in document:
        validate_parameter_values(document["strategy_parameters"])
    enabled_count = 0
    for role_key, assignment in document["assignments"].items():
        candidates = assignment["candidates"]
        if assignment.get("enabled", True):
            enabled_count += 1
            if not candidates:
                raise PlantingDocumentError(f"Enabled role {role_key!r} requires candidates")
        _require_unique(candidates, "asset_id", f"assignments.{role_key}.candidates")
        for index, candidate in enumerate(candidates):
            if not math.isfinite(float(candidate["weight"])) or candidate["weight"] <= 0:
                raise PlantingDocumentError("Candidate weight must be finite and positive")
            _validate_scale_policy(
                candidate["scale"],
                f"assignments.{role_key}.candidates[{index}].scale",
            )
            for field in ("reference_radius_m", "reference_height_m"):
                _validate_reference_dimension(
                    candidate.get(field),
                    f"assignments.{role_key}.candidates[{index}].{field}",
                )
            target = candidate["target_asset_ref"]
            if target["target_type"] != document["target"]["target_type"]:
                raise PlantingDocumentError(
                    f"assignments.{role_key}.candidates[{index}] target_type differs from Profile"
                )
            if target["provider_id"] != document["target"]["provider_id"]:
                raise PlantingDocumentError(
                    f"assignments.{role_key}.candidates[{index}] provider_id differs from Profile"
                )
    if not enabled_count:
        raise PlantingDocumentError("At least one role must be enabled")
    return document


def validate_catalog(value: Any) -> dict[str, Any]:
    document = validate_document(value, "catalog")
    _require_unique(document["assets"], "asset_id", "catalog assets")
    asset_ids = {item["asset_id"] for item in document["assets"]}
    for index, asset in enumerate(document["assets"]):
        thumbnail = asset.get("thumbnail")
        if isinstance(thumbnail, str):
            try:
                parse_library_resource_reference(thumbnail)
            except ValueError as error:
                raise PlantingDocumentError(
                    f"assets[{index}].thumbnail must reference a managed "
                    "VerdFlow library resource"
                ) from error
        _validate_scale_policy(
            asset.get("suggested_scale"),
            f"assets[{index}].suggested_scale",
        )
        for field in ("reference_radius_m", "reference_height_m"):
            _validate_reference_dimension(asset.get(field), f"assets[{index}].{field}")
    _require_unique(document.get("collections", []), "collection_id", "collections")
    for collection in document.get("collections", []):
        members = collection["asset_ids"]
        if len(members) != len(set(members)):
            raise PlantingDocumentError(
                f"collection {collection['collection_id']!r} contains duplicate assets"
            )
        missing = sorted(set(members) - asset_ids)
        if missing:
            raise PlantingDocumentError(
                f"collection {collection['collection_id']!r} references unknown assets: "
                + ", ".join(missing)
            )
    return document


def validate_point_set_v2(value: Any) -> dict[str, Any]:
    document = validate_document(value, "points_v2")
    points = document["points"]
    if document["point_count"] != len(points):
        raise PlantingDocumentError("point_count does not match points length")
    _require_unique(points, "id", "points")

    for index, point in enumerate(points):
        path = f"points[{index}]"
        _finite_vector(point["generated_position"], 3, f"{path}.generated_position")
        _finite_vector(point["resolved_position"], 3, f"{path}.resolved_position")
        quaternion = _finite_vector(
            point["rotation_quaternion_xyzw"],
            4,
            f"{path}.rotation_quaternion_xyzw",
        )
        if sum(component * component for component in quaternion) <= 0.0:
            raise PlantingDocumentError(f"{path}.rotation_quaternion_xyzw is zero")

        if document["kind"] == "generated":
            if point["terrain_sample"] is not None:
                raise PlantingDocumentError(f"{path}.terrain_sample must be null")
            if point["resolved_position"] != point["generated_position"]:
                raise PlantingDocumentError(
                    f"{path}.resolved_position must equal generated_position"
                )
        else:
            sample = point["terrain_sample"]
            if not isinstance(sample, dict) or "hit" not in sample:
                raise PlantingDocumentError(f"{path}.terrain_sample requires hit")
            if sample["hit"]:
                missing = [
                    field
                    for field in ("position", "normal", "target")
                    if field not in sample
                ]
                if missing:
                    raise PlantingDocumentError(
                        f"{path}.terrain_sample requires {missing[0]} on hit"
                    )
                position = _finite_vector(sample["position"], 3, f"{path}.terrain_sample.position")
                normal = _finite_vector(sample["normal"], 3, f"{path}.terrain_sample.normal")
                if sum(component * component for component in normal) <= 0.0:
                    raise PlantingDocumentError(
                        f"{path}.terrain_sample.normal is zero"
                    )
                if point["resolved_position"] != position:
                    raise PlantingDocumentError(
                        f"{path}.resolved_position must equal terrain sample position"
                    )
            elif point["resolved_position"] != point["generated_position"]:
                raise PlantingDocumentError(
                    f"{path}.resolved_position must preserve generated_position on miss"
                )
    return document


def validate_persisted_point_set_summary(
    value: Any,
    *,
    expected_point_count: int,
) -> dict[str, Any]:
    """Check the persisted Core identity without repeating per-point validation."""

    if not isinstance(value, dict):
        raise PlantingDocumentError("Persisted PointSet must be an object")
    if (
        value.get("schema") != "verdflow.points"
        or str(value.get("version")) != "2"
    ):
        raise PlantingDocumentError("Persisted PointSet schema is unsupported")
    points = value.get("points")
    point_count = value.get("point_count")
    if not isinstance(points, list):
        raise PlantingDocumentError("Persisted PointSet points must be an array")
    if (
        not isinstance(point_count, int)
        or isinstance(point_count, bool)
        or point_count != expected_point_count
        or len(points) != expected_point_count
    ):
        raise PlantingDocumentError(
            "Persisted PointSet point_count differs from its Core record"
        )
    if not isinstance(value.get("strategy"), dict):
        raise PlantingDocumentError("Persisted PointSet strategy is missing")
    return value


def validate_persisted_assignment_set_summary(
    value: Any,
    *,
    expected_point_count: int,
    expected_semantic_hash: str,
) -> dict[str, Any]:
    """Check an immutable Core assignment reference, not its full schema again."""
    if not isinstance(value, dict):
        raise PlantingDocumentError("Persisted AssignmentSet must be an object")
    rows = value.get("assignments")
    if (
        value.get("schema") != "verdflow.asset-assignment-set"
        or str(value.get("version")) != "1"
        or not isinstance(rows, list)
    ):
        raise PlantingDocumentError("Persisted AssignmentSet schema is unsupported")
    if (
        not isinstance(value.get("point_count"), int)
        or isinstance(value["point_count"], bool)
        or value["point_count"] != expected_point_count
        or len(rows) != expected_point_count
    ):
        raise PlantingDocumentError("Persisted AssignmentSet count differs from its Core record")
    if value.get("semantic_hash") != expected_semantic_hash:
        raise PlantingDocumentError("Persisted AssignmentSet hash differs from its Core record")
    return value


def validate_elevation_point_set(
    value: Any,
    parent: dict[str, Any],
    *,
    expected_point_count: int,
) -> dict[str, Any]:
    """Validate elevation deltas against an already accepted immutable parent."""
    source = validate_persisted_point_set_summary(
        parent, expected_point_count=expected_point_count,
    )
    document = validate_persisted_point_set_summary(
        value, expected_point_count=expected_point_count,
    )
    if (
        source.get("kind") != "generated"
        or source.get("resolution_policy") != "generated_position"
    ):
        raise PlantingDocumentError("Elevation parent must be a generated PointSet")
    if (
        document.get("offset_applied") is not False
        or document.get("coordinate_space") != "source_scene_world"
        or document.get("kind") != "elevation_resolved"
        or document.get("resolution_policy") != "sample_hit_else_generated"
    ):
        raise PlantingDocumentError("Elevation PointSet requires unshifted elevation_resolved coordinates")
    mutable_header = {"kind", "resolution_policy", "points"}
    if (
        set(document) != set(source)
        or any(document[key] != source[key] for key in source if key not in mutable_header)
    ):
        raise PlantingDocumentError("Elevation sampling changed point document provenance")
    mutable_point = {"terrain_sample", "resolved_position"}
    sample_fields = {"hit", "position", "normal", "target", "face_index", "source_member_id"}
    pairs = zip(source["points"], document["points"], strict=True)
    for index, (original, point) in enumerate(pairs):
        path = f"points[{index}]"
        if (
            not isinstance(point, dict)
            or set(point) != set(original)
            or any(point[key] != original[key] for key in original if key not in mutable_point)
        ):
            raise PlantingDocumentError("Elevation sampling changed immutable planting fields")
        # Python considers True == 1; equality alone cannot protect numeric fields.
        _finite_vector(point["generated_position"], 3, f"{path}.generated_position")
        _finite_vector(point["rotation_quaternion_xyzw"], 4, f"{path}.rotation_quaternion_xyzw")
        position = _finite_vector(point["resolved_position"], 3, f"{path}.resolved_position")
        sample = point["terrain_sample"]
        if (
            not isinstance(sample, dict)
            or not isinstance(sample.get("hit"), bool)
            or set(sample) - sample_fields
        ):
            raise PlantingDocumentError(f"{path}.terrain_sample is invalid")
        for field in ("position", "normal"):
            if field in sample:
                _finite_vector(sample[field], 3, f"{path}.terrain_sample.{field}")
        if "target" in sample and not isinstance(sample["target"], str):
            raise PlantingDocumentError(f"{path}.terrain_sample.target must be a string")
        if "face_index" in sample and (
            not isinstance(sample["face_index"], int)
            or isinstance(sample["face_index"], bool)
        ):
            raise PlantingDocumentError(f"{path}.terrain_sample.face_index must be an integer")
        if "source_member_id" in sample and (
            not isinstance(sample["source_member_id"], str)
            or not sample["source_member_id"]
        ):
            raise PlantingDocumentError(f"{path}.terrain_sample.source_member_id must be non-empty")
        if sample["hit"]:
            if not {"position", "normal", "target"}.issubset(sample):
                raise PlantingDocumentError(f"{path}.terrain_sample requires position, normal and target on hit")
            if sum(component * component for component in sample["normal"]) <= 0:
                raise PlantingDocumentError(f"{path}.terrain_sample.normal is zero")
            if position != sample["position"]:
                raise PlantingDocumentError(f"{path}.resolved_position must equal terrain sample position")
        elif position != original["generated_position"]:
            raise PlantingDocumentError(f"{path}.resolved_position must preserve generated_position on miss")
    return document


def validate_effective_binding_snapshot(value: Any) -> dict[str, Any]:
    document = validate_document(value, "effective_binding")
    _validate_embedded_hash(document)
    for role_key, assignment in document["assignments"].items():
        candidates = assignment["candidates"]
        _require_unique(candidates, "asset_id", f"assignments.{role_key}.candidates")
        for index, candidate in enumerate(candidates):
            _validate_scale_policy(
                candidate["scale"],
                f"assignments.{role_key}.candidates[{index}].scale",
            )
    return document


def validate_target_asset_use_snapshot(value: Any) -> dict[str, Any]:
    document = validate_document(value, "target_asset_use_snapshot")
    _validate_embedded_hash(document)
    _require_unique(document["assets"], "asset_id", "assets")
    for index, asset in enumerate(document["assets"]):
        for field in ("reference_radius_m", "reference_height_m"):
            _validate_reference_dimension(asset.get(field), f"assets[{index}].{field}")
    return document


def validate_assignment_set(value: Any) -> dict[str, Any]:
    document = validate_document(value, "assignment_set")
    _validate_embedded_hash(document)
    if document["point_count"] != len(document["assignments"]):
        raise PlantingDocumentError("point_count does not match assignments length")
    _require_unique(document["assignments"], "point_id", "assignments")
    return document


def with_semantic_hash(payload: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(payload)
    document["semantic_hash"] = semantic_hash(document)
    return document


def _validate_embedded_hash(document: dict[str, Any]) -> None:
    payload = dict(document)
    claimed = payload.pop("semantic_hash")
    actual = semantic_hash(payload)
    if claimed != actual:
        raise PlantingDocumentError(
            f"semantic_hash mismatch: expected {actual}, got {claimed}"
        )


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    filename = SCHEMAS.get("profile" if schema_name == "binding_draft" else schema_name)
    if filename is None:
        raise PlantingDocumentError(f"Unknown planting schema {schema_name!r}")
    resource = files("verdflow.planting.schemas").joinpath(filename)
    schema = json.loads(resource.read_text(encoding="utf-8"))
    if schema_name == "binding_draft":
        schema = {
            "$schema": schema["$schema"],
            "type": "object",
            "required": ["schema", "version", "target", "assignments"],
            "properties": {
                "schema": {"const": "verdflow.role-binding-draft"},
                "version": {"const": "1"},
                "target": schema["properties"]["target"],
                "assignments": schema["properties"]["assignments"],
            },
            "additionalProperties": False,
            "$defs": schema["$defs"],
        }
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise RuntimeError(f"Invalid bundled schema {filename}: {error.message}") from error
    return Draft202012Validator(schema)


def _error_sort_key(error: ValidationError) -> tuple[str, str]:
    return ("/".join(str(item) for item in error.absolute_path), error.message)


def _require_unique(items: list[dict[str, Any]], key: str, label: str) -> None:
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        raise PlantingDocumentError(f"{label} require unique {key}")


def _validate_scale_policy(value: Any, path: str) -> None:
    if value is None:
        return
    mode = value["mode"]
    if mode == "fixed":
        number = float(value["value"])
        if not math.isfinite(number) or number <= 0.0:
            raise PlantingDocumentError(f"{path}.value must be finite and positive")
        return
    minimum = float(value["min"])
    maximum = float(value["max"])
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise PlantingDocumentError(f"{path} must contain finite bounds")
    if minimum <= 0.0 or maximum <= 0.0 or minimum > maximum:
        raise PlantingDocumentError(f"{path} requires 0 < min <= max")


def _validate_reference_dimension(value: Any, path: str) -> None:
    if value is None:
        return
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise PlantingDocumentError(f"{path} must be finite and positive")


def _finite_vector(value: Any, length: int, path: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise PlantingDocumentError(f"{path} must contain {length} numbers")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        raise PlantingDocumentError(f"{path} must contain {length} numbers")
    try:
        result = [float(item) for item in value]
    except OverflowError as error:
        raise PlantingDocumentError(f"{path} must contain finite numbers") from error
    if not all(math.isfinite(item) for item in result):
        raise PlantingDocumentError(f"{path} must contain finite numbers")
    return result
