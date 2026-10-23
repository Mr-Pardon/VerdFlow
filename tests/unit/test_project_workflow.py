from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.contracts import SourceKind  # noqa: E402
from verdflow.project_store import (  # noqa: E402
    ProjectSchemaError,
    ProjectStore,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class ProjectWorkflowTests(unittest.TestCase):
    def test_first_preparation_fixes_the_project_source(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "fixed-source.verdflow"
            with ProjectStore.create(project_path, "Fixed Source") as store:
                initial = store.project_workflow_state()
                self.assertIsNone(initial.source_id)
                self.assertEqual(initial.revision, 1)

                source_one = self._source(store, "one.fbx")
                source_two = self._source(store, "two.fbx")
                preparation_one = self._preparation(store, source_one.source_id, "one")
                preparation_two = self._preparation(store, source_two.source_id, "two")

                current = store.start_scene_preparation_workflow(preparation_one)
                self.assertEqual(current.source_id, source_one.source_id)
                self.assertEqual(
                    current.scene_preparation_execution_id,
                    preparation_one.execution_id,
                )

                with self.assertRaisesRegex(
                    ProjectSchemaError,
                    "Project source is fixed",
                ):
                    store.start_scene_preparation_workflow(preparation_two)

                self.assertEqual(store.project_workflow_state(), current)

    def test_stale_preparation_cannot_replace_a_revised_material_flow(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project_path = Path(temporary) / "revised-materials.verdflow"
            with ProjectStore.create(project_path, "Revised Materials") as store:
                source = self._source(store, "scene.fbx")
                preparation = self._preparation(store, source.source_id, "scene")
                store.start_scene_preparation_workflow(preparation)
                revised_flow = SimpleNamespace(
                    execution_id="mfe_revised",
                    region_set_id="regions_revised",
                )
                current = store.start_material_flow_workflow(revised_flow)

                stale_preparation = SimpleNamespace(
                    execution_id=preparation.execution_id,
                    source_id=source.source_id,
                    material_flow_execution_id="mfe_original",
                    region_set_id=None,
                )
                synchronized = store.synchronize_scene_preparation_workflow(
                    stale_preparation
                )

                self.assertEqual(synchronized, current)
                self.assertEqual(
                    synchronized.material_flow_execution_id,
                    "mfe_revised",
                )
                self.assertEqual(synchronized.region_set_id, "regions_revised")

    @staticmethod
    def _source(store: ProjectStore, name: str):
        artifact = store.put_bytes(
            name.encode("utf-8"),
            media_type="application/octet-stream",
        )
        return store.register_source(
            kind=SourceKind.FBX,
            original_name=name,
            artifact_id=artifact.artifact_id,
        )

    @staticmethod
    def _preparation(store: ProjectStore, source_id: str, suffix: str):
        request = store.put_bytes(
            b"{}",
            media_type="application/vnd.verdflow.request+json",
        )
        return store.create_scene_preparation(
            idempotency_key=f"prep:{suffix}",
            request_artifact_id=request.artifact_id,
            request_hash=f"sha256:{suffix}",
            source_id=source_id,
        )


if __name__ == "__main__":
    unittest.main()
