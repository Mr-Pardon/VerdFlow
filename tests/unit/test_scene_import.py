from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import JobCommandRef, SourceKind  # noqa: E402
from verdflow.core import (  # noqa: E402
    JobRuntime,
    SceneImportError,
    SceneImportService,
)
from verdflow.project_store import ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class SceneImportServiceTests(unittest.TestCase):
    def test_finalizes_inspection_once_and_reuses_revision(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "scene-import.verdflow",
                "Scene import",
            ) as store:
                job_id = self._completed_inspection(store)
                service = SceneImportService(store)

                first = service.finalize(job_id)
                second = service.finalize(job_id)

                self.assertEqual(first.scene_revision_id, second.scene_revision_id)
                self.assertEqual(
                    store.project_info().active_scene_revision_id,
                    first.scene_revision_id,
                )
                self.assertEqual(
                    first.importer_id,
                    "verdflow-blender-worker",
                )

    def test_rejects_shifted_scene_facts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "shifted.verdflow",
                "Shifted",
            ) as store:
                job_id = self._completed_inspection(
                    store,
                    offset_applied=True,
                )

                with self.assertRaisesRegex(
                    SceneImportError,
                    "unshifted source coordinates",
                ):
                    SceneImportService(store).finalize(job_id)

    @staticmethod
    def _completed_inspection(
        store: ProjectStore,
        *,
        offset_applied: bool = False,
    ) -> str:
        source_artifact = store.put_bytes(
            b"fake fbx",
            media_type="model/vnd.autodesk.fbx",
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        inspection_manifest = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.scene-inspection-manifest",
                    "version": "1",
                    "coordinate_frame": {
                        "name": "Blender world",
                        "handedness": "right-handed",
                        "up_axis": "+Z",
                        "linear_unit": "meter",
                        "crs_kind": "unknown",
                        "crs_definition": None,
                        "local_origin": [0.0, 0.0, 0.0],
                        "offset_applied": offset_applied,
                    },
                }
            ).encode("utf-8"),
            media_type="application/json",
            schema_id="verdflow.scene-inspection-manifest",
            schema_version="1",
        )
        facts = store.put_bytes(
            b'{"schema":"fbx-region-tagger.fact-report.v1"}',
            media_type="application/json",
            schema_id="fbx-region-tagger.fact-report",
            schema_version="1",
        )
        command = store.put_bytes(
            b"{}",
            media_type="application/vnd.verdflow.command+json",
        )
        runtime = JobRuntime(store)
        job = runtime.submit(
            JobCommandRef(
                command_type="verdflow.blender.inspect-scene",
                command_version="1",
                artifact_id=command.artifact_id,
            ),
            idempotency_key=f"scene-import-{offset_applied}",
        )
        claimed = runtime.claim_next(
            "test-worker",
            command_types=("verdflow.blender.inspect-scene",),
        )
        assert claimed is not None
        result = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.blender-worker.result",
                    "version": "1",
                    "status": "succeeded",
                    "operation": "inspect_scene",
                    "result_kind": "scene_inspection",
                    "job_id": job.job_id,
                    "source_id": source.source_id,
                    "producer": {
                        "id": "verdflow-blender-worker",
                        "version": "1.0.0+blender-5.1.2",
                        "capability_bundle": {
                            "bundle_id": "verdflow-blender-capabilities",
                            "bundle_version": "1.0.0",
                            "bundle_hash": "test-capability-hash",
                        },
                        "runtime": {
                            "id": "blender",
                            "version": "5.1.2",
                            "build_hash": "test-build",
                        },
                    },
                    "output_artifacts": [
                        {
                            "role": "scene_manifest",
                            "artifact_id": inspection_manifest.artifact_id,
                        },
                        {
                            "role": "material_fact_report",
                            "artifact_id": facts.artifact_id,
                        },
                    ],
                }
            ).encode("utf-8"),
            media_type="application/json",
            schema_id="verdflow.blender-worker.result",
            schema_version="1",
        )
        runtime.succeed(
            claimed.lease,
            result_artifact_id=result.artifact_id,
        )
        return job.job_id


if __name__ == "__main__":
    unittest.main()
