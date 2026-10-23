"""Read material facts and finalize an explicit, exact material selection."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
from typing import Any, BinaryIO, Protocol

from verdflow.contracts import (
    MaterialClassification,
    MaterialFactItem,
    MaterialFactsView,
    MaterialSelectionItem,
    MaterialSelectionMode,
    MaterialSelectionRequest,
    MaterialTextureFact,
)
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime


class MaterialFactsReader(Protocol):
    def get_scene_revision(self, scene_revision_id: str): ...

    def open_artifact(
        self,
        artifact_id: str,
    ) -> AbstractContextManager[BinaryIO]: ...


class RegionMaterialSelectionReader(MaterialFactsReader, Protocol):
    def get_region_set(self, region_set_id: str): ...


@dataclass(frozen=True, slots=True)
class MaterialSelectionResult:
    rule_pack_artifact_id: str
    rule_pack: dict[str, Any]


def load_material_facts(
    reader: MaterialFactsReader,
    scene_revision_id: str,
) -> MaterialFactsView:
    normalized_scene_revision_id = _required_text(
        scene_revision_id,
        "scene_revision_id",
    )
    scene = reader.get_scene_revision(normalized_scene_revision_id)
    manifest = _read_json(
        reader,
        scene.scene_manifest_artifact_id,
        "Scene manifest",
    )
    facts = manifest.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("Scene manifest facts are required")
    artifact_id = _required_text(
        facts.get("material_fact_report_artifact_id"),
        "material_fact_report_artifact_id",
    )
    report = _read_json(reader, artifact_id, "Material facts")
    rows = report.get("materials")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Material facts materials are required")
    materials = tuple(_fact_item(row) for row in rows)
    names = [item.material_name for item in materials]
    if len(names) != len(set(names)):
        raise ValueError("Material facts contain duplicate material names")
    return MaterialFactsView(
        scene_revision_id=normalized_scene_revision_id,
        materials=materials,
    )


def load_material_selection(
    reader: MaterialFactsReader,
    scene_revision_id: str,
    rule_pack_artifact_id: str,
) -> MaterialSelectionRequest:
    """Reconstruct an exact full classification from a confirmed rule pack."""
    facts = load_material_facts(reader, scene_revision_id)
    rule_pack = _read_json(reader, rule_pack_artifact_id, "Rule pack")
    classifications: dict[str, MaterialClassification] = {}
    for rule in rule_pack.get("rules", []):
        if not isinstance(rule, dict):
            raise ValueError("Rule pack rule must be a JSON object")
        classification = {
            "general_greenbelt": MaterialClassification.GENERAL_GREEN,
            "street_greenbelt": MaterialClassification.STREET_TREE_GREEN,
        }.get(rule.get("greenbelt_type"))
        match = rule.get("match")
        names = match.get("material_names") if isinstance(match, dict) else None
        if classification is None or not isinstance(names, list):
            continue
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Rule pack material name is invalid")
            if name in classifications:
                raise ValueError(f"Rule pack classifies material twice: {name}")
            classifications[name] = classification
    fact_names = {item.material_name for item in facts.materials}
    unknown = sorted(set(classifications) - fact_names)
    if unknown:
        raise ValueError(
            "Rule pack references unknown materials: " + ", ".join(unknown)
        )
    return MaterialSelectionRequest(
        materials=tuple(
            MaterialSelectionItem(
                material_name=item.material_name,
                classification=classifications.get(
                    item.material_name,
                    MaterialClassification.NOT_GREEN,
                ),
            )
            for item in facts.materials
        )
    )


def load_region_material_selection(
    reader: RegionMaterialSelectionReader,
    scene_revision_id: str,
    region_set_id: str,
) -> MaterialSelectionRequest:
    """Recover a material-level selection from one extracted RegionSet."""
    facts = load_material_facts(reader, scene_revision_id)
    region_set = reader.get_region_set(
        _required_text(region_set_id, "region_set_id")
    )
    if region_set.scene_revision_id != scene_revision_id:
        raise ValueError("RegionSet does not belong to the requested scene")
    geometry = _read_json(
        reader,
        region_set.geometry_artifact_id,
        "Region geometry",
    )
    regions = geometry.get("regions")
    if not isinstance(regions, list):
        raise ValueError("Region geometry regions are required")

    selected: dict[str, MaterialClassification] = {}
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("Region geometry item must be a JSON object")
        classification = (
            MaterialClassification.STREET_TREE_GREEN
            if region.get("greenbelt_type") == "street_greenbelt"
            else MaterialClassification.GENERAL_GREEN
        )
        material_names = region.get("source_materials", [])
        if not isinstance(material_names, list):
            raise ValueError("Region source_materials must be a list")
        for value in material_names:
            material_name = _required_text(value, "source_material")
            previous = selected.get(material_name)
            if previous is not None and previous is not classification:
                raise ValueError(
                    "RegionSet maps one material to multiple classifications: "
                    + material_name
                )
            selected[material_name] = classification

    fact_names = {item.material_name for item in facts.materials}
    unknown = sorted(set(selected) - fact_names)
    if unknown:
        raise ValueError(
            "RegionSet references unknown materials: " + ", ".join(unknown)
        )
    return MaterialSelectionRequest(
        materials=tuple(
            MaterialSelectionItem(
                material_name=item.material_name,
                classification=selected.get(
                    item.material_name,
                    MaterialClassification.NOT_GREEN,
                ),
            )
            for item in facts.materials
        )
    )


class MaterialSelectionService:
    """Validate full material closure and persist an exact-name rule pack."""

    def __init__(self, store: ProjectStore, runtime: JobRuntime) -> None:
        self._store = store
        self._runtime = runtime

    def facts(self, scene_revision_id: str) -> MaterialFactsView:
        return load_material_facts(self._store, scene_revision_id)

    def finalize(
        self,
        scene_revision_id: str,
        request: MaterialSelectionRequest,
        *,
        selection_mode: MaterialSelectionMode,
        candidate_job_id: str | None = None,
    ) -> MaterialSelectionResult:
        if not isinstance(selection_mode, MaterialSelectionMode):
            raise ValueError("selection_mode is invalid")
        facts = self.facts(scene_revision_id)
        canonical = _canonical_selection(facts, request)
        rule_pack = _material_rule_pack(
            canonical,
            scene_revision_id=facts.scene_revision_id,
            selection_mode=selection_mode,
            candidate_job_id=candidate_job_id,
        )
        artifact = self._store.put_bytes(
            _canonical_json_bytes(rule_pack),
            media_type="application/vnd.verdflow.rule-pack+json",
            schema_id="verdflow.region-rule-pack",
            schema_version="1",
        )
        if candidate_job_id is not None:
            from verdflow.contracts import JobRetentionClass

            self._runtime.promote(
                candidate_job_id,
                retention_class=JobRetentionClass.AUDIT,
            )
        return MaterialSelectionResult(
            rule_pack_artifact_id=artifact.artifact_id,
            rule_pack=rule_pack,
        )


def _canonical_selection(
    facts: MaterialFactsView,
    request: MaterialSelectionRequest,
) -> tuple[tuple[str, MaterialClassification], ...]:
    if not isinstance(request, MaterialSelectionRequest):
        raise ValueError("material selection request is invalid")
    selected: dict[str, MaterialClassification] = {}
    for item in request.materials:
        name = _required_text(item.material_name, "material_name")
        if name in selected:
            raise ValueError(f"Material selection contains a duplicate: {name}")
        if not isinstance(item.classification, MaterialClassification):
            raise ValueError(f"Material classification is invalid: {name}")
        selected[name] = item.classification
    expected = tuple(item.material_name for item in facts.materials)
    missing = [name for name in expected if name not in selected]
    unknown = [name for name in selected if name not in set(expected)]
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing=" + ", ".join(missing))
        if unknown:
            details.append("unknown=" + ", ".join(unknown))
        raise ValueError(
            "Material selection must classify every fact exactly: "
            + "; ".join(details)
        )
    return tuple((name, selected[name]) for name in expected)


def _material_rule_pack(
    selection: tuple[tuple[str, MaterialClassification], ...],
    *,
    scene_revision_id: str,
    selection_mode: MaterialSelectionMode,
    candidate_job_id: str | None,
) -> dict[str, Any]:
    groups = (
        (
            MaterialClassification.STREET_TREE_GREEN,
            "selected-street-tree-green",
            "Street tree greenbelt",
            "street_greenbelt",
        ),
        (
            MaterialClassification.GENERAL_GREEN,
            "selected-general-green",
            "General greenbelt",
            "general_greenbelt",
        ),
    )
    rules: list[dict[str, Any]] = []
    for classification, rule_id, label, greenbelt_type in groups:
        names = [name for name, value in selection if value is classification]
        if not names:
            continue
        rules.append(
            {
                "id": rule_id,
                "label": label,
                "name_prefix": label.replace(" ", "_"),
                "tags": f"green,plantable,{greenbelt_type}",
                "confidence": "confirmed",
                "needs_review": False,
                "greenbelt_type": greenbelt_type,
                "classification_source": "explicit_material_selection",
                "match": {"material_names": names},
            }
        )
    if not rules:
        raise ValueError("Material selection contains no plantable green materials")
    return {
        "schema": "verdflow.region-rule-pack",
        "version": "1",
        "origin": {
            "scene_revision_id": scene_revision_id,
            "selection_mode": selection_mode.value,
            "candidate_job_id": candidate_job_id,
        },
        "rules": rules,
    }


def _fact_item(value: object) -> MaterialFactItem:
    if not isinstance(value, dict):
        raise ValueError("Material facts item must be an object")
    objects = value.get("objects", [])
    textures = value.get("textures", [])
    diffuse = value.get("diffuse_color", [])
    if not isinstance(objects, list) or not all(
        isinstance(item, str) for item in objects
    ):
        raise ValueError("Material fact objects are invalid")
    if not isinstance(textures, list):
        raise ValueError("Material fact textures are invalid")
    if not isinstance(diffuse, list) or not all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        for item in diffuse
    ):
        raise ValueError("Material fact diffuse_color is invalid")
    return MaterialFactItem(
        material_name=_required_text(value.get("name"), "material name"),
        face_count=_non_negative_integer(value.get("face_count", 0), "face_count"),
        object_count=_non_negative_integer(
            value.get("object_count", len(objects)),
            "object_count",
        ),
        object_names=tuple(objects),
        diffuse_color=tuple(float(item) for item in diffuse),
        textures=tuple(_texture_fact(item) for item in textures),
    )


def _texture_fact(value: object) -> MaterialTextureFact:
    if not isinstance(value, dict):
        raise ValueError("Material texture fact must be an object")
    return MaterialTextureFact(
        image_name=_optional_text(value.get("image")),
        source_relative_path=_optional_text(value.get("source_relative_path")),
    )


def _read_json(
    reader: MaterialFactsReader,
    artifact_id: str,
    label: str,
) -> dict[str, Any]:
    with reader.open_artifact(artifact_id) as stream:
        try:
            document = json.load(stream)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    return document


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional material fact text is invalid")
    return value.strip() or None


def _non_negative_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
