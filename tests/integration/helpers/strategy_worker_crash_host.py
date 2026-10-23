from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]

from verdflow.core import JobRuntime, StrategyExecutionAuthorizer  # noqa: E402
from verdflow.contracts import WorkerProcessInfo  # noqa: E402
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.strategy_worker import (  # noqa: E402
    StrategyWorkerConfig,
    StrategyWorkerSupervisor,
    StrategyWorkerSupervisorConfig,
)


def main() -> None:
    project_path = Path(sys.argv[1])
    library_path = Path(sys.argv[2])
    runtime_path = Path(sys.argv[3])
    launch_path = Path(sys.argv[4])
    with (
        LibraryStore.open(library_path) as library,
        ProjectStore.open(project_path) as store,
    ):
        runtime = JobRuntime(store, lease_duration=timedelta(seconds=1))

        def publish_launch(record: WorkerProcessInfo) -> None:
            launch_path.write_text(
                json.dumps(
                    {
                        "host": record.host,
                        "pid": record.pid,
                        "started_at_token": record.started_at_token,
                    }
                ),
                encoding="utf-8",
            )

        supervisor = StrategyWorkerSupervisor(
            runtime,
            StrategyExecutionAuthorizer(library, library),
            library,
            StrategyWorkerSupervisorConfig(
                worker=StrategyWorkerConfig(
                    timeout_seconds=30.0,
                    poll_interval_seconds=0.05,
                ),
                heartbeat_interval_seconds=0.1,
                runtime_root=runtime_path,
            ),
            worker_id="strategy-crash-host",
            process_started=publish_launch,
        )
        supervisor.run_once()


if __name__ == "__main__":
    main()
