from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    InferenceProtocol,
    InferenceProviderDescriptor,
    InferenceProviderKind,
    JobCommandRef,
    JobStatus,
    SourceKind,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    MaterialAnalysisRequest,
    MaterialAnalysisService,
    ModelAnalysisProviderDefinition,
)
from verdflow.core_host import (  # noqa: E402
    CoreHost,
    CoreHostConfig,
    MaterialFlowRequest,
)
from verdflow.model_api import (  # noqa: E402
    MODEL_API_ANALYZE_MATERIALS_COMMAND,
    ModelApiCapabilities,
    ModelApiProviderConfig,
    ModelApiProviderRegistry,
    ModelApiWorkerConfig,
    ModelApiWorkerSupervisor,
)
from verdflow.project_store import (  # noqa: E402
    ProcessStatus,
    ProjectStore,
    SystemProcessInspector,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class _Secrets:
    def __init__(self, value: str) -> None:
        self.value = value

    def resolve(self, secret_ref: str) -> str:
        if secret_ref != "model-api/custom/test":
            raise ValueError("unexpected secret reference")
        return self.value


class ModelApiWorkerSupervisorTests(unittest.TestCase):
    def test_core_host_runs_manual_material_flow_through_external_worker(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with _model_server(status=200) as server:
                registry = _provider_registry(server.server_port)
                config = CoreHostConfig(
                    root / "model-host.verdflow",
                    enable_blender_worker=False,
                    enable_model_api_worker=True,
                    enable_project_maintenance=False,
                    runtime_root=root / "runtime",
                    poll_interval_seconds=0.02,
                    model_api_ready_timeout_seconds=5.0,
                    model_api_heartbeat_timeout_seconds=5.0,
                    model_api_execution_timeout_seconds=20.0,
                    model_api_request_timeout_seconds=10.0,
                    model_api_heartbeat_interval_seconds=0.2,
                )
                with CoreHost.create(
                    config,
                    project_name="Model Host flow",
                    model_api_provider_registry=registry,
                    model_api_secret_resolver=_Secrets("test-secret-value"),
                ) as host:
                    scene_id = self._scene(host.store)

                    outcome = host.analyze_materials(
                        MaterialFlowRequest(
                            MaterialAnalysisRequest(scene_id, "test")
                        ),
                        analysis_timeout_seconds=20.0,
                    )

                    self.assertEqual(outcome.analysis_job.status, JobStatus.SUCCEEDED)
                    self.assertTrue(
                        outcome.candidate.task_id.startswith("analysis_")
                    )
                    self.assertIsNone(outcome.confirmation)

    def test_reconcile_terminates_only_recorded_model_process(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with _model_server(status=200) as server, ProjectStore.create(
                root / "model-recovery.verdflow",
                "Model recovery",
            ) as store:
                runtime, _, supervisor = self._runtime(
                    store,
                    root / "runtime",
                    server.server_port,
                    secret="test-secret-value",
                )
                command = store.put_bytes(b"{}", media_type="application/json")
                job = runtime.submit(
                    JobCommandRef("verdflow.fixture.model", "1", command.artifact_id),
                    idempotency_key="recovery-fixture",
                )
                claimed = runtime.claim_next(
                    "fixture",
                    command_types=("verdflow.fixture.model",),
                )
                assert claimed is not None
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                inspector = SystemProcessInspector()
                current = inspector.current_identity()
                probe = inspector.probe(current.host, child.pid)
                self.assertEqual(probe.status, ProcessStatus.ALIVE)
                assert probe.identity is not None
                store.register_worker_process(
                    WorkerProcessInfo(
                        job_id=job.job_id,
                        attempt_number=1,
                        adapter_id="verdflow-model-api-worker",
                        host=probe.identity.host,
                        pid=probe.identity.pid,
                        started_at_token=probe.identity.started_at_token,
                        status=WorkerProcessStatus.RUNNING,
                        started_at="2026-08-11T00:00:00+00:00",
                        heartbeat_at=None,
                        finished_at=None,
                        exit_code=None,
                    )
                )
                attempt_dir = root / "runtime" / f"{job.job_id}-1-fixture"
                attempt_dir.mkdir()

                recovered = supervisor.reconcile_recorded_processes()

                self.assertEqual(recovered, 1)
                child.wait(timeout=5.0)
                self.assertIsNotNone(child.returncode)
                self.assertEqual(
                    store.get_worker_process(job.job_id, 1).status,
                    WorkerProcessStatus.TERMINATED,
                )
                self.assertFalse(attempt_dir.exists())

    def test_cancel_terminates_the_owned_process_without_retry(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with _model_server(
                status=200,
                delay_seconds=2.0,
            ) as server, ProjectStore.create(
                root / "model-cancel.verdflow",
                "Model cancellation",
            ) as store:
                runtime, service, supervisor = self._runtime(
                    store,
                    root / "runtime",
                    server.server_port,
                    secret="test-secret-value",
                )
                job = service.submit(
                    MaterialAnalysisRequest(self._scene(store), "test")
                )
                timers: list[threading.Timer] = []

                def cancel_after_start(_record) -> None:
                    timer = threading.Timer(
                        0.1,
                        lambda: supervisor.request_cancel(job.job_id),
                    )
                    timers.append(timer)
                    timer.start()

                supervisor._process_started = cancel_after_start
                completed = supervisor.run_once()
                for timer in timers:
                    timer.join(timeout=2.0)

                self.assertEqual(completed.status, JobStatus.CANCELLED)
                self.assertEqual(len(runtime.attempts(job.job_id)), 1)
                self.assertIsNone(supervisor.run_once())

    def test_external_worker_registers_closed_candidate_without_persisting_key(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with _model_server(status=200) as server, ProjectStore.create(
                root / "model-worker.verdflow",
                "Model worker",
            ) as store:
                runtime, service, supervisor = self._runtime(
                    store,
                    root / "runtime",
                    server.server_port,
                    secret="test-secret-value",
                )
                job = service.submit(
                    MaterialAnalysisRequest(self._scene(store), "test")
                )

                completed = supervisor.run_once()

                self.assertIsNotNone(completed)
                self.assertEqual(
                    completed.status,
                    JobStatus.SUCCEEDED,
                    completed.error,
                )
                candidate = service.load_candidate(job.job_id)
                self.assertEqual(candidate.invocation.request_id, "local_request_1")
                self.assertEqual(
                    [item.material_name for item in candidate.materials],
                    ["Grass_Main", "Road_Main"],
                )
                process = store.get_worker_process(job.job_id, 1)
                self.assertEqual(process.adapter_id, "verdflow-model-api-worker")
                persisted = b"".join(
                    path.read_bytes()
                    for path in store.root.rglob("*")
                    if path.is_file()
                )
                self.assertNotIn(b"test-secret-value", persisted)
                self.assertEqual(tuple((root / "runtime").iterdir()), ())

    def test_dispatched_http_failure_is_visible_and_never_implicitly_retried(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with _model_server(status=500) as server, ProjectStore.create(
                root / "model-failure.verdflow",
                "Model failure",
            ) as store:
                runtime, service, supervisor = self._runtime(
                    store,
                    root / "runtime",
                    server.server_port,
                    secret="test-secret-value",
                )
                job = service.submit(
                    MaterialAnalysisRequest(self._scene(store), "test")
                )

                completed = supervisor.run_once()

                self.assertIsNotNone(completed)
                self.assertEqual(completed.status, JobStatus.FAILED)
                self.assertEqual(completed.error.code, "MODEL_API_ATTEMPT_UNCERTAIN")
                self.assertFalse(completed.error.retryable)
                self.assertEqual(len(runtime.attempts(job.job_id)), 1)
                self.assertIsNone(supervisor.run_once())

    @staticmethod
    def _runtime(
        store: ProjectStore,
        runtime_root: Path,
        port: int,
        *,
        secret: str,
    ):
        registry = _provider_registry(port)
        runtime = JobRuntime(store)
        service = MaterialAnalysisService(
            store,
            runtime,
            (
                ModelAnalysisProviderDefinition(
                    provider_id="test",
                    provider_version="1",
                    protocol=InferenceProtocol.OPENAI_CHAT_COMPLETIONS,
                    default_model="test-model",
                    command_type=MODEL_API_ANALYZE_MATERIALS_COMMAND,
                    command_version="1",
                    max_attempts=1,
                ),
            ),
        )
        supervisor = ModelApiWorkerSupervisor(
            runtime,
            registry,
            _Secrets(secret),
            ModelApiWorkerConfig(
                runtime_root=runtime_root,
                poll_interval_seconds=0.02,
                ready_timeout_seconds=5.0,
                heartbeat_timeout_seconds=5.0,
                execution_timeout_seconds=20.0,
                request_timeout_seconds=10.0,
                heartbeat_interval_seconds=0.2,
            ),
        )
        return runtime, service, supervisor

    @staticmethod
    def _scene(store: ProjectStore) -> str:
        source_artifact = store.put_bytes(b"fbx", media_type="model/vnd.autodesk.fbx")
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
        )
        manifest = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.scene-manifest",
                    "version": "1",
                    "facts": {"material_fact_report_artifact_id": facts.artifact_id},
                }
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.scene-manifest+json",
        )
        frame = store.create_coordinate_frame(
            name="Scene",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        return store.create_scene_revision(
            source_id=source.source_id,
            importer_id="fixture",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest.artifact_id,
        ).scene_revision_id


class _Handler(BaseHTTPRequestHandler):
    status = 200
    delay_seconds = 0.0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.status != 200:
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"fixture failure"}}')
            return
        prompt = request["messages"][1]["content"]
        user = json.loads(prompt.split("Material facts: ", 1)[1])
        rows = [
            {
                "material_name": item["name"],
                "classification": (
                    "general_green" if item["name"] == "Grass_Main" else "not_green"
                ),
                "confidence": 0.95,
                "evidence": ["fixture"],
            }
            for item in user["materials"]
        ]
        response = {
            "id": "local_request_1",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps({"materials": rows, "warnings": []})
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


class _ServerContext:
    def __init__(self, status: int, delay_seconds: float) -> None:
        handler = type(
            "ConfiguredHandler",
            (_Handler,),
            {"status": status, "delay_seconds": delay_seconds},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> ThreadingHTTPServer:
        self.thread.start()
        return self.server

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


def _model_server(*, status: int, delay_seconds: float = 0.0) -> _ServerContext:
    return _ServerContext(status, delay_seconds)


def _provider_registry(port: int) -> ModelApiProviderRegistry:
    provider = ModelApiProviderConfig(
        descriptor=InferenceProviderDescriptor(
            provider_id="test",
            display_name="Local test",
            kind=InferenceProviderKind.API,
            protocol=InferenceProtocol.OPENAI_CHAT_COMPLETIONS,
            provider_version="1",
            default_model="test-model",
            task_types=("material_classification.v1",),
        ),
        endpoint=f"http://127.0.0.1:{port}/chat/completions",
        secret_ref="model-api/custom/test",
        capabilities=ModelApiCapabilities(default_max_output_tokens=4096),
    )
    return ModelApiProviderRegistry((provider,), include_builtin=False)


if __name__ == "__main__":
    unittest.main()
