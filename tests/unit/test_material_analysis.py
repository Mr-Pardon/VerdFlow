from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    InferenceProtocol,
    JobRetentionClass,
    MaterialConfirmationMode,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    MaterialAnalysisError,
    MaterialConfirmationRequired,
    MaterialAnalysisRequest,
    MaterialAnalysisService,
    ModelAnalysisProviderDefinition,
)
from verdflow.project_store import ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class MaterialAnalysisServiceTests(unittest.TestCase):
    def test_submits_stable_candidate_job_without_secret_or_endpoint(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "material-submit.verdflow",
                "Material analysis",
            ) as store:
                scene_id = self._create_scene(store)
                runtime = JobRuntime(store)
                service = self._service(store, runtime)

                first = service.submit(
                    MaterialAnalysisRequest(scene_id, "deepseek")
                )
                repeated = service.submit(
                    MaterialAnalysisRequest(scene_id, "deepseek")
                )
                with store.open_artifact(first.command.artifact_id) as stream:
                    command = json.load(stream)

                self.assertEqual(first.job_id, repeated.job_id)
                self.assertEqual(first.retention_class, JobRetentionClass.CANDIDATE)
                self.assertEqual(command["provider_id"], "deepseek")
                self.assertEqual(command["model"], "deepseek-v4-flash")
                self.assertEqual(command["task_type"], "material_classification.v1")
                self.assertEqual(command["confirmation_mode"], "manual")
                serialized = json.dumps(command)
                self.assertNotIn("api.deepseek.com", serialized)
                self.assertNotIn("API_KEY", serialized)
                self.assertNotIn("secret", serialized.lower())

    def test_manual_confirmation_freezes_exact_rule_pack_and_audit(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "material-manual.verdflow",
                "Material manual confirmation",
            ) as store:
                scene_id = self._create_scene(store)
                runtime = JobRuntime(store)
                service = self._service(store, runtime)
                job = service.submit(
                    MaterialAnalysisRequest(
                        scene_id,
                        "deepseek",
                        confirmation_mode=MaterialConfirmationMode.MANUAL,
                    )
                )
                self._complete(runtime, store, job.job_id)

                with self.assertRaises(MaterialConfirmationRequired):
                    service.finalize_candidate(job.job_id)

                first = service.finalize_candidate(
                    job.job_id,
                    user_confirmed=True,
                )
                repeated = service.finalize_candidate(
                    job.job_id,
                    user_confirmed=True,
                )

                self.assertEqual(
                    first.rule_pack_artifact_id,
                    repeated.rule_pack_artifact_id,
                )
                self.assertEqual(first.confirmation_mode, MaterialConfirmationMode.MANUAL)
                self.assertEqual(
                    first.rule_pack["rules"][0]["match"]["material_names"],
                    ["Grass_Main"],
                )
                self.assertEqual(
                    store.get_job(job.job_id).retention_class,
                    JobRetentionClass.AUDIT,
                )

    def test_automatic_confirmation_requires_no_user_action(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "material-automatic.verdflow",
                "Material automatic confirmation",
            ) as store:
                scene_id = self._create_scene(store)
                runtime = JobRuntime(store)
                service = self._service(store, runtime)
                job = service.submit(
                    MaterialAnalysisRequest(
                        scene_id,
                        "deepseek",
                        confirmation_mode=MaterialConfirmationMode.AUTOMATIC,
                    )
                )
                self._complete(runtime, store, job.job_id)

                result = service.finalize_candidate(job.job_id)

                self.assertEqual(
                    result.confirmation_mode,
                    MaterialConfirmationMode.AUTOMATIC,
                )
                self.assertEqual(
                    result.rule_pack["origin"]["confirmation_mode"],
                    "automatic",
                )
                self.assertEqual(
                    store.get_job(job.job_id).retention_class,
                    JobRetentionClass.AUDIT,
                )

    def test_loads_candidate_only_when_lineage_and_materials_close(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "material-result.verdflow",
                "Material candidate",
            ) as store:
                scene_id = self._create_scene(store)
                runtime = JobRuntime(store)
                service = self._service(store, runtime)
                job = service.submit(MaterialAnalysisRequest(scene_id, "deepseek"))
                self._complete(runtime, store, job.job_id)

                candidate = service.load_candidate(job.job_id)

                self.assertEqual(
                    [item.material_name for item in candidate.materials],
                    ["Grass_Main", "Road_Main"],
                )
                self.assertEqual(candidate.invocation.model, "deepseek-v4-flash")

    def test_rejects_candidate_that_reorders_material_facts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "material-mismatch.verdflow",
                "Material mismatch",
            ) as store:
                scene_id = self._create_scene(store)
                runtime = JobRuntime(store)
                service = self._service(store, runtime)
                job = service.submit(MaterialAnalysisRequest(scene_id, "deepseek"))
                self._complete(runtime, store, job.job_id, reverse=True)

                with self.assertRaisesRegex(
                    MaterialAnalysisError,
                    "exact input material order",
                ):
                    service.load_candidate(job.job_id)

    @staticmethod
    def _service(
        store: ProjectStore,
        runtime: JobRuntime,
    ) -> MaterialAnalysisService:
        return MaterialAnalysisService(
            store,
            runtime,
            (
                ModelAnalysisProviderDefinition(
                    provider_id="deepseek",
                    provider_version="2026-08-10",
                    protocol=InferenceProtocol.OPENAI_CHAT_COMPLETIONS,
                    default_model="deepseek-v4-flash",
                    command_type="verdflow.model-api.analyze-materials",
                    command_version="1",
                ),
            ),
        )

    @staticmethod
    def _create_scene(store: ProjectStore) -> str:
        source_artifact = store.put_bytes(
            b"fake fbx",
            media_type="model/vnd.autodesk.fbx",
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        facts = store.put_bytes(
            json.dumps(
                {
                    "schema": "fbx-region-tagger.fact-report.v1",
                    "materials": [
                        {"name": "Grass_Main", "face_count": 12},
                        {"name": "Road_Main", "face_count": 20},
                    ],
                }
            ).encode("utf-8"),
            media_type="application/json",
            schema_id="fbx-region-tagger.fact-report.v1",
            schema_version="1",
        )
        manifest = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.scene-manifest",
                    "version": "1",
                    "facts": {
                        "material_fact_report_artifact_id": facts.artifact_id,
                    },
                }
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.scene-manifest+json",
            schema_id="verdflow.scene-manifest",
            schema_version="1",
        )
        frame = store.create_coordinate_frame(
            name="Scene",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        scene = store.create_scene_revision(
            source_id=source.source_id,
            importer_id="test",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest.artifact_id,
        )
        return scene.scene_revision_id

    @staticmethod
    def _complete(
        runtime: JobRuntime,
        store: ProjectStore,
        job_id: str,
        *,
        reverse: bool = False,
    ) -> None:
        claimed = runtime.claim_next(
            "fake-model-worker",
            command_types=("verdflow.model-api.analyze-materials",),
        )
        assert claimed is not None and claimed.job.job_id == job_id
        with store.open_artifact(claimed.job.command.artifact_id) as stream:
            command = json.load(stream)
        material_names = ["Grass_Main", "Road_Main"]
        if reverse:
            material_names.reverse()
        rows = [
            {
                "material_name": name,
                "classification": (
                    "general_green" if name == "Grass_Main" else "not_green"
                ),
                "confidence": 0.95,
                "evidence": ["fixture"],
            }
            for name in material_names
        ]
        candidate = {
            "schema": "verdflow.material-classification-candidate",
            "version": "1",
            "task_id": command["task_id"],
            "task_type": command["task_type"],
            "input_ref": command["input_artifact_id"],
            "invocation": {
                "provider_id": command["provider_id"],
                "provider_version": command["provider_version"],
                "protocol": command["protocol"],
                "model": command["model"],
                "prompt_policy_version": command["prompt_policy_version"],
                "request_id": "deepseek_fixture",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            },
            "materials": rows,
            "warnings": [],
        }
        result = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.model-analysis.result",
                    "version": "1",
                    "result_kind": "material_classification_candidate",
                    "candidate": candidate,
                }
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.model-analysis-result+json",
            schema_id="verdflow.model-analysis.result",
            schema_version="1",
        )
        runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)


if __name__ == "__main__":
    unittest.main()
