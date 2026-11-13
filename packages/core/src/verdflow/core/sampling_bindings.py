"""Validate frozen terrain references without interpreting external formats."""

from verdflow.contracts import ResolvedPlantWorkflow, SamplingBinding
from verdflow.project_store import ProjectStore


def validate_sampling_binding(
    store: ProjectStore, binding: SamplingBinding, coordinate_frame_id: str,
) -> None:
    binding.validate()
    ref = binding.source
    if ref.coordinate_frame_id != coordinate_frame_id:
        raise ValueError("Sampling terrain coordinate frame differs from generated points")
    source = store.get_source(ref.source_id)
    artifact = store.get_artifact(ref.artifact_id)
    if source.artifact_id != ref.artifact_id or artifact.content_hash != ref.content_hash:
        raise ValueError("Sampling terrain content differs from frozen binding")
    if ref.source_member_id is not None:
        members = store.list_source_assembly_members(ref.assembly_source_id)
        matches = [member for member in members if member.source_member_id == ref.source_member_id
                   and member.source_id == ref.source_id
                   and member.source_content_hash == ref.content_hash]
        if len(matches) != 1:
            raise ValueError("Sampling terrain member does not belong to its Assembly")
        if matches[0].source_to_scene_matrix_row_major != (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.):
            raise ValueError("Sampling terrain member requires canonical untransformed coordinates")
    if ref.scene_revision_id is not None:
        scene = store.get_scene_revision(ref.scene_revision_id)
        if scene.source_id != ref.source_id or scene.coordinate_frame_id != coordinate_frame_id:
            raise ValueError("Sampling scene differs from frozen terrain binding")


def validate_resolved_workflow(
    store: ProjectStore, workflow: ResolvedPlantWorkflow | None,
    scene_revision_id: str, target_type: str,
) -> None:
    if workflow is None:
        return
    workflow.validate()
    if workflow.scene_revision_id != scene_revision_id or workflow.target_type != target_type:
        raise ValueError("Resolved workflow differs from the execution scene or target")
    scene = store.get_scene_revision(scene_revision_id)
    validate_sampling_binding(store, workflow.sampling, scene.coordinate_frame_id)


def workflow_from_document(document: dict, legacy_version: str) -> ResolvedPlantWorkflow | None:
    version = str(document.get("version"))
    if version == legacy_version and "workflow" not in document:
        return None
    if version == str(int(legacy_version) + 1) and isinstance(document.get("workflow"), dict):
        return ResolvedPlantWorkflow.from_document(document["workflow"])
    raise ValueError("Unsupported workflow execution request version")
