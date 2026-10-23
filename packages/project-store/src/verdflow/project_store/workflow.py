"""Persistence for the single current workflow chain of a project."""

from __future__ import annotations

from verdflow.contracts import (
    MaterialFlowExecutionInfo,
    PlantRunExecutionInfo,
    ProjectWorkflowState,
    ScenePreparationInfo,
)

from .database import ProjectDatabase
from .errors import ProjectSchemaError


_SELECT = """
SELECT project_id, source_id, scene_preparation_execution_id,
       material_flow_execution_id, region_set_id,
       plant_run_execution_id, revision, updated_at
FROM project_workflow_state
"""


class ProjectWorkflowRepository:
    """Move one current-chain pointer without interpreting business stages."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def get(self) -> ProjectWorkflowState:
        row = self.database.connection.execute(_SELECT + " LIMIT 1").fetchone()
        if row is None:
            raise ProjectSchemaError("Project workflow state is missing")
        return _from_row(row)

    def start_scene_preparation(
        self,
        execution: ScenePreparationInfo,
        *,
        updated_at: str,
    ) -> ProjectWorkflowState:
        current = self.get()
        if current.source_id not in {None, execution.source_id}:
            raise ProjectSchemaError(
                "Project source is fixed; create another project for a new source"
            )
        return self._update(
            current,
            source_id=execution.source_id,
            scene_preparation_execution_id=execution.execution_id,
            material_flow_execution_id=execution.material_flow_execution_id,
            region_set_id=execution.region_set_id,
            plant_run_execution_id=None,
            updated_at=updated_at,
            clear_composition=True,
        )

    def synchronize_scene_preparation(
        self,
        execution: ScenePreparationInfo,
        *,
        updated_at: str,
    ) -> ProjectWorkflowState:
        current = self.get()
        if current.scene_preparation_execution_id != execution.execution_id:
            return current
        if (
            current.material_flow_execution_id is not None
            and current.material_flow_execution_id
            != execution.material_flow_execution_id
        ):
            return current
        return self._update(
            current,
            source_id=current.source_id,
            scene_preparation_execution_id=execution.execution_id,
            material_flow_execution_id=execution.material_flow_execution_id,
            region_set_id=execution.region_set_id,
            plant_run_execution_id=current.plant_run_execution_id,
            updated_at=updated_at,
            clear_composition=(
                current.region_set_id is not None
                and execution.region_set_id != current.region_set_id
            ),
        )

    def start_material_flow(
        self,
        execution: MaterialFlowExecutionInfo,
        *,
        updated_at: str,
    ) -> ProjectWorkflowState:
        current = self.get()
        if current.source_id is None:
            raise ProjectSchemaError(
                "Material Flow cannot become current before a project source"
            )
        return self._update(
            current,
            source_id=current.source_id,
            scene_preparation_execution_id=current.scene_preparation_execution_id,
            material_flow_execution_id=execution.execution_id,
            region_set_id=execution.region_set_id,
            plant_run_execution_id=None,
            updated_at=updated_at,
            clear_composition=True,
        )

    def synchronize_material_flow(
        self,
        execution: MaterialFlowExecutionInfo,
        *,
        updated_at: str,
    ) -> ProjectWorkflowState:
        current = self.get()
        if current.material_flow_execution_id != execution.execution_id:
            return current
        return self._update(
            current,
            source_id=current.source_id,
            scene_preparation_execution_id=current.scene_preparation_execution_id,
            material_flow_execution_id=execution.execution_id,
            region_set_id=execution.region_set_id,
            plant_run_execution_id=current.plant_run_execution_id,
            updated_at=updated_at,
            clear_composition=(
                current.region_set_id is not None
                and execution.region_set_id != current.region_set_id
            ),
        )

    def activate_region_source(
        self,
        *,
        source_id: str,
        region_set_id: str,
        updated_at: str,
    ) -> ProjectWorkflowState:
        """Make direct Region source output the current planting context."""
        current = self.get()
        if current.source_id not in {None, source_id}:
            raise ProjectSchemaError(
                "Project source is fixed; create another project for a new source"
            )
        if (
            current.source_id == source_id
            and current.region_set_id == region_set_id
            and current.scene_preparation_execution_id is None
            and current.material_flow_execution_id is None
        ):
            return current
        return self._update(
            current,
            source_id=source_id,
            scene_preparation_execution_id=None,
            material_flow_execution_id=None,
            region_set_id=region_set_id,
            plant_run_execution_id=None,
            updated_at=updated_at,
            clear_composition=True,
        )

    def start_plant_run(
        self,
        execution: PlantRunExecutionInfo,
        *,
        region_set_id: str,
        updated_at: str,
    ) -> ProjectWorkflowState:
        current = self.get()
        if current.region_set_id != region_set_id:
            raise ProjectSchemaError(
                "Plant Run RegionSet is not the current project RegionSet"
            )
        return self._update(
            current,
            source_id=current.source_id,
            scene_preparation_execution_id=current.scene_preparation_execution_id,
            material_flow_execution_id=current.material_flow_execution_id,
            region_set_id=current.region_set_id,
            plant_run_execution_id=execution.execution_id,
            updated_at=updated_at,
            clear_composition=False,
        )

    def _update(
        self,
        current: ProjectWorkflowState,
        *,
        source_id: str | None,
        scene_preparation_execution_id: str | None,
        material_flow_execution_id: str | None,
        region_set_id: str | None,
        plant_run_execution_id: str | None,
        updated_at: str,
        clear_composition: bool,
    ) -> ProjectWorkflowState:
        next_revision = current.revision + 1
        with self.database.transaction() as connection:
            if clear_composition:
                connection.execute(
                    "DELETE FROM planting_compositions WHERE project_id = ?",
                    (current.project_id,),
                )
            cursor = connection.execute(
                """
                UPDATE project_workflow_state
                SET source_id = ?, scene_preparation_execution_id = ?,
                    material_flow_execution_id = ?, region_set_id = ?,
                    plant_run_execution_id = ?, revision = ?, updated_at = ?
                WHERE project_id = ? AND revision = ?
                """,
                (
                    source_id,
                    scene_preparation_execution_id,
                    material_flow_execution_id,
                    region_set_id,
                    plant_run_execution_id,
                    next_revision,
                    updated_at,
                    current.project_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectSchemaError("Project workflow revision is stale")
        return ProjectWorkflowState(
            project_id=current.project_id,
            source_id=source_id,
            scene_preparation_execution_id=scene_preparation_execution_id,
            material_flow_execution_id=material_flow_execution_id,
            region_set_id=region_set_id,
            plant_run_execution_id=plant_run_execution_id,
            revision=next_revision,
            updated_at=updated_at,
        )


def _from_row(row: object) -> ProjectWorkflowState:
    return ProjectWorkflowState(
        project_id=row["project_id"],
        source_id=row["source_id"],
        scene_preparation_execution_id=row["scene_preparation_execution_id"],
        material_flow_execution_id=row["material_flow_execution_id"],
        region_set_id=row["region_set_id"],
        plant_run_execution_id=row["plant_run_execution_id"],
        revision=int(row["revision"]),
        updated_at=row["updated_at"],
    )
