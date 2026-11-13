"""Controlled recipe registry and explicit terrain binding for built-in Hosts."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Protocol

from verdflow.contracts import (
    CapabilityRef, InputRecipe, ResolvedPlantWorkflow, SamplingBinding,
    SamplingSourceRef,
)
from verdflow.core import (
    ElevationProviderDefinition, ExportProviderDefinition,
    RegionExtractionProviderDefinition, RegionSourceProviderDefinition,
    validate_sampling_binding,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore


class ExecutionProvider(Protocol):
    provider_id: str
    provider_version: str
    command_type: str
    command_version: str


def capability_ref(kind: str, provider: ExecutionProvider) -> CapabilityRef:
    return CapabilityRef(kind, provider.provider_id, provider.provider_version,
                         provider.command_type, provider.command_version)


class AdapterWorkflowRegistry:
    def __init__(
        self, *, recipes: tuple[InputRecipe, ...],
        region_sources: tuple[RegionSourceProviderDefinition, ...],
        region_extractors: tuple[RegionExtractionProviderDefinition, ...],
        samplers: tuple[ElevationProviderDefinition, ...],
        outputs: tuple[ExportProviderDefinition, ...],
    ) -> None:
        self._capabilities: dict[tuple[str, str], CapabilityRef] = {}
        for kind, providers in (("region_source", region_sources),
                                ("region_extraction", region_extractors),
                                ("elevation", samplers), ("output", outputs)):
            for provider in providers:
                ref = capability_ref(kind, provider)
                ref.validate()
                key = (kind, ref.provider_id)
                if key in self._capabilities:
                    raise ValueError(f"Duplicate {kind} capability: {ref.provider_id}")
                self._capabilities[key] = ref
        self._outputs = {item.provider_id: item for item in outputs}
        self._recipes: dict[str, InputRecipe] = {}
        sampling = {item.provider_id: item for item in samplers}
        for recipe in recipes:
            recipe.validate()
            if recipe.recipe_id in self._recipes:
                raise ValueError(f"Duplicate input recipe: {recipe.recipe_id}")
            self.require(recipe.preparation)
            self.require(recipe.sampling)
            provider = sampling[recipe.sampling.provider_id]
            if (recipe.sampling_source_kind != provider.sampling_source_kind
                    or recipe.configuration_schema != provider.configuration_schema
                    or recipe.failure_policy != provider.failure_policy):
                raise ValueError("Recipe sampling declaration differs from its capability")
            self._recipes[recipe.recipe_id] = recipe

    def require(self, reference: CapabilityRef) -> None:
        if self._capabilities.get((reference.kind, reference.provider_id)) != reference:
            raise ValueError(f"Exact {reference.kind} capability is unavailable: {reference.provider_id}")

    def validate_frozen(self, workflow: ResolvedPlantWorkflow | None) -> None:
        if workflow is None:
            return
        workflow.validate()
        self.require(workflow.sampling.provider)
        self.require(workflow.output)
        if self._outputs[workflow.export_provider_id].target_type != workflow.target_type:
            raise ValueError("Frozen output capability does not support this target")

    def recipe_for_scene(self, importer_id: str, *, recipe_id: str | None = None) -> InputRecipe:
        if recipe_id is not None:
            try:
                return self._recipes[recipe_id]
            except KeyError as error:
                raise ValueError(f"Input recipe is unavailable: {recipe_id}") from error
        # Compatibility bridge for existing SceneRevisions and specialized entry UIs.
        matches = [item for item in self._recipes.values() if importer_id in item.legacy_importer_ids]
        if len(matches) != 1:
            raise ValueError("Input recipe is missing or ambiguous; select an explicit recipe")
        return matches[0]

    def preparation(self, kind: str, provider_id: str) -> CapabilityRef:
        reference = self._capabilities.get((kind, provider_id))
        if kind not in {"region_source", "region_extraction"} or reference is None:
            raise ValueError("Preparation capability is unavailable for this input entry")
        return reference

    def output_for_target(self, target_type: str) -> CapabilityRef:
        matches = [item for item in self._outputs.values() if item.target_type == target_type]
        if len(matches) != 1:
            raise ValueError("Output capability is missing or ambiguous for the selected target")
        return capability_ref("output", matches[0])

    def resolve(
        self, store: ProjectStore, *, scene_revision_id: str, target_type: str,
        recipe_id: str | None = None, geometry_kind: str = "polygon",
        configuration: dict[str, Any] | None = None,
        terrain_source: SamplingSourceRef | None = None,
    ) -> ResolvedPlantWorkflow:
        scene = store.get_scene_revision(scene_revision_id)
        recipe = self.recipe_for_scene(scene.importer_id, recipe_id=recipe_id)
        if geometry_kind not in recipe.geometry_kinds:
            raise ValueError("Input recipe does not support this geometry kind")
        # Copy through canonical JSON so callers cannot mutate a frozen request later.
        config = json.loads(canonical_json_bytes(configuration or {}))
        if terrain_source is None:
            source = store.get_source(scene.source_id)
            assembly_id = member_id = None
            if recipe.sampling_source_kind == "source":
                matches = [(member, store.get_source(member.source_id)) for member in
                           store.list_source_assembly_members(source.source_id)
                           if store.get_source(member.source_id).kind.value == recipe.source_member_kind]
                if len(matches) != 1:
                    raise ValueError("Elevation resource is missing or ambiguous; bind one terrain source explicitly")
                member, source = matches[0]
                identity = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
                if member.source_to_scene_matrix_row_major != identity:
                    raise ValueError("Elevation resource must use aligned metric coordinates without transforms")
                assembly_id, member_id = member.assembly_source_id, member.source_member_id
            artifact = store.get_artifact(source.artifact_id)
            live = recipe.sampling_source_kind == "session"
            terrain_source = SamplingSourceRef(
                kind=recipe.sampling_source_kind, source_id=source.source_id,
                artifact_id=source.artifact_id, content_hash=artifact.content_hash,
                coordinate_frame_id=scene.coordinate_frame_id,
                scene_revision_id=scene_revision_id if recipe.sampling_source_kind != "source" else None,
                assembly_source_id=assembly_id, source_member_id=member_id,
                connector_session_id=config.get("connector_session_id") if live else None,
                operation_id=config.get("operation_id") if live else None,
                generation_token=config.get("generation_token") if live else None,
            )
        binding = SamplingBinding(recipe.sampling, terrain_source, recipe.configuration_schema,
                                  config, recipe.failure_policy)
        validate_sampling_binding(store, binding, scene.coordinate_frame_id)
        if terrain_source.kind != recipe.sampling_source_kind:
            raise ValueError("Terrain resource kind differs from the selected recipe")
        if recipe.source_member_kind is not None and store.get_source(terrain_source.source_id).kind.value != recipe.source_member_kind:
            raise ValueError("Terrain source format differs from the recipe resource slot")
        result = ResolvedPlantWorkflow(
            recipe.recipe_id, recipe.version, semantic_hash(json.loads(json.dumps(asdict(recipe)))), scene_revision_id,
            binding, self.output_for_target(target_type), target_type,
        )
        result.validate()
        return result
