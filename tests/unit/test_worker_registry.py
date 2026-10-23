from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import JobInfo  # noqa: E402
from verdflow.core import WorkerDescriptor, WorkerRegistry  # noqa: E402


@dataclass
class FakeDriver:
    descriptor: WorkerDescriptor
    reconciled: int = 0

    def __post_init__(self) -> None:
        self.cancelled: list[str] = []
        self.completed: list[JobInfo] = []

    def run_once(self) -> JobInfo | None:
        return self.completed.pop(0) if self.completed else None

    def reconcile(self) -> int:
        return self.reconciled

    def request_cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)


class WorkerRegistryTests(unittest.TestCase):
    def test_rejects_ambiguous_command_ownership_and_seals(self) -> None:
        registry = WorkerRegistry()
        registry.register(
            FakeDriver(WorkerDescriptor("worker-a", ("command.a",)))
        )

        with self.assertRaisesRegex(ValueError, "already has an owner"):
            registry.register(
                FakeDriver(WorkerDescriptor("worker-b", ("command.a",)))
            )

        registry.seal()
        with self.assertRaisesRegex(RuntimeError, "sealed"):
            registry.register(
                FakeDriver(WorkerDescriptor("worker-c", ("command.c",)))
            )

    def test_reconciles_registered_drivers_in_registration_order(self) -> None:
        registry = WorkerRegistry()
        registry.register(
            FakeDriver(WorkerDescriptor("worker-a", ("command.a",)), 2)
        )
        registry.register(
            FakeDriver(WorkerDescriptor("worker-b", ("command.b",)), 3)
        )

        reports = registry.reconcile_all()

        self.assertEqual(
            [(row.worker_id, row.recovered_process_count) for row in reports],
            [("worker-a", 2), ("worker-b", 3)],
        )


if __name__ == "__main__":
    unittest.main()
