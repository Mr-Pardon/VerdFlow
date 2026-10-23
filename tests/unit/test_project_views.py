from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path / "src")
    for group in ("packages", "adapters", "apps")
    for path in (ROOT / group).iterdir()
    if (path / "src").is_dir()
]

from verdflow.contracts import (  # noqa: E402
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    ProjectInfo,
    ProjectWorkflowState,
    ScenePreparationInfo,
    ScenePreparationStage,
    ScenePreparationStatus,
)
from verdflow.system_host import ProjectBusinessState  # noqa: E402
from verdflow.system_host.project_views import project_business_summary  # noqa: E402


class ProjectViewTests(unittest.TestCase):
    def test_current_workflow_chain_drives_business_summary(self) -> None:
        project = ProjectInfo(
            project_id="project_1",
            name="Project",
            schema_version=17,
            created_at="2026-08-13T00:00:00+00:00",
            updated_at="2026-08-13T00:00:00+00:00",
            active_scene_revision_id=None,
        )
        workflow = ProjectWorkflowState(
            project_id="project_1",
            source_id="source",
            scene_preparation_execution_id="spe_1",
            material_flow_execution_id="flow",
            region_set_id="regions",
            plant_run_execution_id="pre_1",
            revision=3,
            updated_at="2026-08-13T00:02:00+00:00",
        )
        preparation = ScenePreparationInfo(
            execution_id="spe_1",
            idempotency_key="prepare",
            request_artifact_id="request",
            request_hash="hash",
            status=ScenePreparationStatus.SUCCEEDED,
            stage=ScenePreparationStage.COMPLETED,
            revision=3,
            source_id="source",
            inspection_job_id="inspection",
            scene_revision_id="scene",
            material_flow_execution_id="flow",
            region_set_id="regions",
            error_code=None,
            error_message=None,
            created_at="2026-08-13T00:00:00+00:00",
            updated_at="2026-08-13T00:01:00+00:00",
        )
        plant_run = PlantRunExecutionInfo(
            execution_id="pre_1",
            idempotency_key="plant",
            request_artifact_id="request",
            request_hash="hash",
            continuation_artifact_id=None,
            continuation_hash=None,
            status=PlantRunExecutionStatus.AWAITING_CONFIRMATION,
            stage=PlantRunExecutionStage.REVIEW,
            revision=3,
            generation_job_id="generation",
            generated_point_set_id="points",
            assignment_job_id="assignment",
            asset_assignment_set_id="assignments",
            elevation_job_id=None,
            elevation_point_set_id=None,
            export_job_id=None,
            export_id=None,
            error_code=None,
            error_message=None,
            created_at="2026-08-13T00:02:00+00:00",
            updated_at="2026-08-13T00:03:00+00:00",
        )

        summary = project_business_summary(
            project,
            workflow=workflow,
            scene_revision_id="scene",
            scene_importer_id="verdflow-blender-worker",
            scene_preparation=preparation,
            material_flow=None,
            plant_run=plant_run,
        )
        self.assertEqual(summary.state, ProjectBusinessState.AWAITING_POINT_REVIEW)
        self.assertEqual(summary.execution_id, "pre_1")
        self.assertEqual(summary.scene_revision_id, "scene")

        failed = replace(
            plant_run,
            status=PlantRunExecutionStatus.FAILED,
            error_code="failed",
            error_message="Generation failed",
        )
        summary = project_business_summary(
            project,
            workflow=workflow,
            scene_revision_id="scene",
            scene_importer_id="verdflow-blender-worker",
            scene_preparation=preparation,
            material_flow=None,
            plant_run=failed,
        )
        self.assertEqual(summary.state, ProjectBusinessState.FAILED)
        self.assertEqual(summary.error_code, "failed")

    def test_direct_region_source_is_ready_without_scene_preparation(self) -> None:
        project = ProjectInfo(
            project_id="project_1",
            name="Geospatial",
            schema_version=20,
            created_at="2026-09-14T00:00:00+00:00",
            updated_at="2026-09-14T00:01:00+00:00",
            active_scene_revision_id="scene_geo",
        )
        workflow = ProjectWorkflowState(
            project_id="project_1",
            source_id="source_assembly",
            scene_preparation_execution_id=None,
            material_flow_execution_id=None,
            region_set_id="regions_geo",
            plant_run_execution_id=None,
            revision=1,
            updated_at="2026-09-14T00:01:00+00:00",
        )

        summary = project_business_summary(
            project,
            workflow=workflow,
            scene_revision_id="scene_geo",
            scene_importer_id="verdflow.geospatial",
            scene_preparation=None,
            material_flow=None,
            plant_run=None,
        )

        self.assertEqual(summary.state, ProjectBusinessState.READY_TO_PLANT)
        self.assertEqual(summary.scene_revision_id, "scene_geo")
        self.assertEqual(summary.scene_importer_id, "verdflow.geospatial")
        self.assertEqual(summary.region_set_id, "regions_geo")
        self.assertIsNone(summary.scene_preparation_execution_id)


if __name__ == "__main__":
    unittest.main()
