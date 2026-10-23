"""Execute one structured Model API request in a supervised subprocess."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from verdflow.contracts import (
    InferenceProtocol,
    InferenceProviderDescriptor,
    InferenceProviderKind,
    MATERIAL_CLASSIFICATION_TASK,
    MODEL_ANALYSIS_COMMAND_SCHEMA,
    MODEL_ANALYSIS_COMMAND_VERSION,
    MODEL_ANALYSIS_RESULT_SCHEMA,
    MODEL_ANALYSIS_RESULT_VERSION,
)

from .client import ModelApiClient
from .errors import ModelApiError
from .models import (
    ModelApiCapabilities,
    ModelApiProviderConfig,
    ModelApiReasoningMode,
)
from .protocol import (
    MODEL_API_KEY_ENVIRONMENT,
    MODEL_API_WORKER_PROTOCOL_VERSION,
)
from .provider_config import ModelApiProviderRegistry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-dir", required=True)
    parser.add_argument("--command", required=True)
    arguments = parser.parse_args()
    control_dir = Path(arguments.control_dir).resolve()
    command_path = Path(arguments.command).resolve()
    if not command_path.is_relative_to(control_dir):
        raise ValueError("Worker command must be inside its control directory")

    _write_json(
        control_dir / "ready.json",
        {
            "schema": "verdflow.model-api-worker.ready",
            "version": MODEL_API_WORKER_PROTOCOL_VERSION,
            "pid": os.getpid(),
        },
    )
    heartbeat = _Heartbeat(
        control_dir,
        interval_seconds=float(
            command_value(command_path, "heartbeat_interval_seconds")
        ),
    )
    heartbeat.start()
    try:
        command = _read_json(command_path)
        analysis = _required_object(command, "analysis")
        _validate_analysis_command(analysis)
        provider = _provider_from_document(_required_object(command, "provider"))
        facts_path = (control_dir / _required_text(command, "facts_file")).resolve()
        if not facts_path.is_relative_to(control_dir) or not facts_path.is_file():
            raise ValueError("Material facts file is invalid")
        fact_report = _read_json(facts_path)
        api_key = os.environ.pop(MODEL_API_KEY_ENVIRONMENT, "").strip()
        if not api_key:
            raise ValueError("Model API secret was not supplied")

        client = ModelApiClient(
            provider_registry=ModelApiProviderRegistry(
                (provider,),
                include_builtin=False,
            )
        )
        plan = client.plan_material_analysis(
            fact_report,
            provider_id=provider.descriptor.provider_id,
            model=_required_text(analysis, "model"),
        )
        _write_json(
            control_dir / "call-plan.json",
            {
                "schema": "verdflow.model-api-worker.call-plan",
                "version": MODEL_API_WORKER_PROTOCOL_VERSION,
                **asdict(plan),
                "warnings": list(plan.warnings),
            },
        )
        heartbeat.set_stage("requesting_model", 0.25)
        _write_json(
            control_dir / "request-dispatched.json",
            {
                "schema": "verdflow.model-api-worker.request-dispatched",
                "version": MODEL_API_WORKER_PROTOCOL_VERSION,
                "provider_id": provider.descriptor.provider_id,
                "model": plan.model,
            },
        )
        candidate = client.analyze_materials(
            fact_report,
            provider_id=provider.descriptor.provider_id,
            api_key=api_key,
            input_ref=_required_text(analysis, "input_artifact_id"),
            model=plan.model,
            task_id=_required_text(analysis, "task_id"),
            timeout_seconds=float(command["request_timeout_seconds"]),
            max_output_tokens=plan.requested_max_output_tokens,
        )
        api_key = ""
        heartbeat.set_stage("validating_candidate", 0.9)
        _write_json(
            control_dir / "result.json",
            {
                "schema": MODEL_ANALYSIS_RESULT_SCHEMA,
                "version": MODEL_ANALYSIS_RESULT_VERSION,
                "result_kind": "material_classification_candidate",
                "candidate": candidate.to_document(),
                "execution": {
                    "estimated_input_tokens": plan.estimated_input_tokens,
                    "estimated_output_tokens": plan.estimated_output_tokens,
                    "requested_max_output_tokens": plan.requested_max_output_tokens,
                    "warnings": list(plan.warnings),
                },
            },
        )
        heartbeat.set_stage("completed", 1.0)
        return 0
    except (ModelApiError, ValueError, KeyError, OSError, TypeError) as error:
        _write_json(
            control_dir / "failure.json",
            {
                "schema": "verdflow.model-api-worker.failure",
                "version": MODEL_API_WORKER_PROTOCOL_VERSION,
                "error_type": type(error).__name__,
                "message": str(error) or type(error).__name__,
            },
        )
        return 1
    finally:
        heartbeat.stop()


class _Heartbeat:
    def __init__(self, control_dir: Path, *, interval_seconds: float) -> None:
        self._path = control_dir / "heartbeat.json"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stage = "preparing_request"
        self._progress = 0.05
        self._sequence = 0
        self._interval_seconds = interval_seconds
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._publish()
        self._thread.start()

    def set_stage(self, stage: str, progress: float) -> None:
        with self._lock:
            self._stage = stage
            self._progress = progress
        self._publish()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._publish()

    def _publish(self) -> None:
        with self._lock:
            self._sequence += 1
            document = {
                "schema": "verdflow.model-api-worker.heartbeat",
                "version": MODEL_API_WORKER_PROTOCOL_VERSION,
                "sequence": self._sequence,
                "stage": self._stage,
                "progress": self._progress,
                "monotonic_seconds": time.monotonic(),
            }
            _write_json(self._path, document)


def _provider_from_document(value: dict[str, Any]) -> ModelApiProviderConfig:
    capabilities = _required_object(value, "capabilities")
    return ModelApiProviderConfig(
        descriptor=InferenceProviderDescriptor(
            provider_id=_required_text(value, "provider_id"),
            display_name=_required_text(value, "display_name"),
            kind=InferenceProviderKind.API,
            protocol=InferenceProtocol(_required_text(value, "protocol")),
            provider_version=_required_text(value, "provider_version"),
            default_model=_optional_text(value.get("default_model"), "default_model"),
            task_types=(MATERIAL_CLASSIFICATION_TASK,),
        ),
        endpoint=_required_text(value, "endpoint"),
        secret_ref=_required_text(value, "secret_ref"),
        capabilities=ModelApiCapabilities(
            context_window_tokens=_optional_int(
                capabilities.get("context_window_tokens")
            ),
            max_output_tokens=_optional_int(capabilities.get("max_output_tokens")),
            default_max_output_tokens=int(
                capabilities["default_max_output_tokens"]
            ),
            reasoning_mode=ModelApiReasoningMode(
                capabilities["reasoning_mode"]
            ),
        ),
    )


def _validate_analysis_command(value: dict[str, Any]) -> None:
    if value.get("schema") != MODEL_ANALYSIS_COMMAND_SCHEMA:
        raise ValueError("Model analysis command schema is unsupported")
    if str(value.get("version")) != MODEL_ANALYSIS_COMMAND_VERSION:
        raise ValueError("Model analysis command version is unsupported")
    if value.get("operation") != "classify_materials":
        raise ValueError("Model analysis operation is unsupported")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Worker JSON document must be an object")
    return value


def command_value(path: Path, field: str) -> Any:
    value = _read_json(path).get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


def _required_object(value: dict[str, Any], field: str) -> dict[str, Any]:
    item = value.get(field)
    if not isinstance(item, dict):
        raise ValueError(f"{field} must be an object")
    return item


def _required_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} is required")
    return item.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be text or null")
    return value.strip()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Provider capability must be an integer or null")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
