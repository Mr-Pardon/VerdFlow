from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    JobStatus,
    MaterialConfirmationMode,
)
from verdflow.core import MaterialAnalysisRequest  # noqa: E402
from verdflow.core_host.material_flow import (  # noqa: E402
    MaterialFlowCoordinator,
    MaterialFlowRequest,
)


class _Store:
    def open_artifact(self, artifact_id: str) -> BytesIO:
        return BytesIO(
            json.dumps({"scene_revision_id": "scene_1"}).encode("utf-8")
        )


class _Application:
    def __init__(self) -> None:
        self.store = _Store()
        self.calls: list[tuple[str, object]] = []

    def submit_material_analysis(self, request):
        self.calls.append(("submit_analysis", request))
        return _job("analysis", status=JobStatus.QUEUED)

    def get_job(self, job_id: str):
        return _job(job_id)

    def load_material_candidate(self, job_id: str):
        self.calls.append(("load_candidate", job_id))
        return SimpleNamespace(task_id="candidate")

    def finalize_material_candidate(self, job_id: str, *, user_confirmed: bool):
        self.calls.append(("confirm", user_confirmed))
        return SimpleNamespace(
            rule_pack_artifact_id="artifact_rule_pack",
            rule_pack={
                "schema": "verdflow.region-rule-pack",
                "version": "1",
                "rules": [{"id": "green"}],
            },
        )

    def submit_region_extraction(self, request, *, idempotency_key: str):
        self.calls.append(("submit_regions", request))
        self.calls.append(("region_key", idempotency_key))
        return _job("regions", status=JobStatus.QUEUED)

    def finalize_region_extraction(self, job_id: str):
        self.calls.append(("finalize_regions", job_id))
        return SimpleNamespace(region_set_id="region_set_1")


class _ServiceLoop:
    def run_until_terminal(self, job_id: str, *, timeout_seconds: float):
        return _job(job_id)


class _Host:
    def __init__(self) -> None:
        self.application = _Application()
        self.service_loop = _ServiceLoop()
        self.started = 0

    def start(self) -> None:
        self.started += 1


def _job(job_id: str, *, status: JobStatus = JobStatus.SUCCEEDED):
    return SimpleNamespace(
        job_id=job_id,
        status=status,
        error=None,
        command=SimpleNamespace(artifact_id="analysis_command"),
    )


class MaterialFlowCoordinatorTests(unittest.TestCase):
    def test_confirm_and_submit_does_not_wait_for_region_extraction(self) -> None:
        host = _Host()
        coordinator = MaterialFlowCoordinator(host)

        submission = coordinator.confirm_and_submit(
            "analysis",
            region_provider_id="blender",
        )

        self.assertEqual(submission.extraction_job.status, JobStatus.QUEUED)
        self.assertIn(("confirm", True), host.application.calls)
        self.assertNotIn(
            "finalize_regions",
            [name for name, _ in host.application.calls],
        )

    def test_manual_mode_stops_at_candidate(self) -> None:
        host = _Host()
        coordinator = MaterialFlowCoordinator(host)

        outcome = coordinator.execute(
            MaterialFlowRequest(
                MaterialAnalysisRequest(
                    "scene_1",
                    "provider",
                    confirmation_mode=MaterialConfirmationMode.MANUAL,
                )
            )
        )

        self.assertIsNone(outcome.confirmation)
        self.assertIsNone(outcome.extraction_job)
        self.assertNotIn("confirm", [name for name, _ in host.application.calls])

    def test_automatic_mode_confirms_and_submits_exact_region_extraction(self) -> None:
        host = _Host()
        coordinator = MaterialFlowCoordinator(host)

        outcome = coordinator.execute(
            MaterialFlowRequest(
                MaterialAnalysisRequest(
                    "scene_1",
                    "provider",
                    confirmation_mode=MaterialConfirmationMode.AUTOMATIC,
                ),
                region_provider_id="blender",
            )
        )

        self.assertEqual(outcome.region_set.region_set_id, "region_set_1")
        self.assertIn(("confirm", False), host.application.calls)
        request = next(
            value for name, value in host.application.calls if name == "submit_regions"
        )
        self.assertEqual(request.scene_revision_id, "scene_1")
        self.assertEqual(request.provider_id, "blender")
        self.assertEqual(request.rule_pack["rules"][0]["id"], "green")


if __name__ == "__main__":
    unittest.main()
