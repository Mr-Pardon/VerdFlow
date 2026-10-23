from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
]

from verdflow.blender_worker import (  # noqa: E402
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
)
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402


def main() -> None:
    project_path = Path(sys.argv[1])
    runtime_path = Path(sys.argv[2])
    blender = Path(sys.argv[3])
    with ProjectStore.open(project_path) as store:
        runtime = JobRuntime(
            store,
            lease_duration=timedelta(seconds=1),
        )
        supervisor = BlenderWorkerSupervisor(
            runtime,
            BlenderWorkerConfig(
                blender_executable=blender,
                runtime_root=runtime_path,
                ready_timeout_seconds=30.0,
                heartbeat_timeout_seconds=5.0,
                execution_timeout_seconds=30.0,
            ),
            worker_id="crash-host-worker",
        )
        supervisor.run_once()


if __name__ == "__main__":
    main()
