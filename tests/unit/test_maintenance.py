from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import JobRetentionSweepResult  # noqa: E402
from verdflow.core import (  # noqa: E402
    MaintenanceDescriptor,
    MaintenanceScheduler,
    MaintenanceTaskResult,
    RetentionMaintenanceTask,
)


@dataclass
class ManualMonotonic:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class ScriptedTask:
    descriptor: MaintenanceDescriptor
    outcomes: list[MaintenanceTaskResult | Exception]
    calls: int = 0

    def run_once(self) -> MaintenanceTaskResult:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class RecordingSweeper:
    batch_size: int = 100
    collect_objects_values: list[bool] = field(default_factory=list)

    def sweep_once(
        self,
        *,
        collect_objects: bool = True,
    ) -> JobRetentionSweepResult:
        self.collect_objects_values.append(collect_objects)
        return JobRetentionSweepResult(0, 0, 0, 0, 0, 0, 0, 0)


class MaintenanceSchedulerTests(unittest.TestCase):
    def test_invalid_task_result_is_contained_as_failure(self) -> None:
        task = ScriptedTask(
            MaintenanceDescriptor("test.invalid-result", 10.0),
            [None],  # type: ignore[list-item]
        )
        scheduler = MaintenanceScheduler()
        scheduler.register(task)
        scheduler.start()

        execution = scheduler.run_due()[0]

        self.assertFalse(execution.succeeded)
        self.assertEqual(execution.error_type, "TypeError")

    def test_retention_task_never_runs_physical_object_gc(self) -> None:
        sweeper = RecordingSweeper()

        result = RetentionMaintenanceTask(sweeper).run_once()  # type: ignore[arg-type]

        self.assertFalse(result.did_work)
        self.assertEqual(sweeper.collect_objects_values, [False])

    def test_start_continuation_and_normal_interval_are_distinct(self) -> None:
        monotonic = ManualMonotonic()
        task = ScriptedTask(
            MaintenanceDescriptor(
                "test.cleanup",
                interval_seconds=10.0,
                continuation_interval_seconds=2.0,
            ),
            [
                MaintenanceTaskResult(True, has_more=True),
                MaintenanceTaskResult(True),
            ],
        )
        scheduler = MaintenanceScheduler(monotonic=monotonic)
        scheduler.register(task)
        scheduler.start()

        first = scheduler.run_due()
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].succeeded)
        self.assertEqual(task.calls, 1)
        monotonic.advance(1.0)
        self.assertEqual(scheduler.run_due(), ())
        monotonic.advance(1.0)
        self.assertEqual(len(scheduler.run_due()), 1)
        self.assertEqual(task.calls, 2)
        monotonic.advance(9.0)
        self.assertEqual(scheduler.run_due(), ())
        monotonic.advance(1.0)
        self.assertEqual(len(scheduler.run_due()), 1)
        self.assertEqual(task.calls, 3)

    def test_failure_is_reported_and_retried_without_busy_loop(self) -> None:
        monotonic = ManualMonotonic()
        task = ScriptedTask(
            MaintenanceDescriptor(
                "test.retry",
                interval_seconds=30.0,
                retry_interval_seconds=5.0,
                max_retry_interval_seconds=20.0,
            ),
            [
                RuntimeError("first failure"),
                RuntimeError("second failure"),
                MaintenanceTaskResult(False),
            ],
        )
        scheduler = MaintenanceScheduler(monotonic=monotonic)
        scheduler.register(task)
        scheduler.start()

        failed = scheduler.run_due()
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0].succeeded)
        self.assertEqual(failed[0].error_message, "first failure")
        self.assertEqual(failed[0].consecutive_failures, 1)
        self.assertEqual(failed[0].next_run_delay_seconds, 5.0)
        monotonic.advance(4.0)
        self.assertEqual(scheduler.run_due(), ())
        monotonic.advance(1.0)
        retried = scheduler.run_due()
        self.assertEqual(len(retried), 1)
        self.assertFalse(retried[0].succeeded)
        self.assertEqual(retried[0].consecutive_failures, 2)
        self.assertEqual(retried[0].next_run_delay_seconds, 10.0)
        self.assertEqual(task.calls, 2)
        monotonic.advance(9.0)
        self.assertEqual(scheduler.run_due(), ())
        monotonic.advance(1.0)
        recovered = scheduler.run_due()
        self.assertEqual(len(recovered), 1)
        self.assertTrue(recovered[0].succeeded)
        self.assertEqual(recovered[0].consecutive_failures, 0)
        self.assertEqual(recovered[0].next_run_delay_seconds, 30.0)
        self.assertEqual(task.calls, 3)
        self.assertEqual(scheduler.last_executions(), recovered)

    def test_deferred_task_does_not_run_on_start(self) -> None:
        monotonic = ManualMonotonic()
        task = ScriptedTask(
            MaintenanceDescriptor(
                "test.deferred",
                interval_seconds=10.0,
                run_on_start=False,
            ),
            [MaintenanceTaskResult(False)],
        )
        scheduler = MaintenanceScheduler(monotonic=monotonic)
        scheduler.register(task)
        scheduler.start()

        self.assertEqual(scheduler.run_due(), ())
        monotonic.advance(10.0)
        self.assertEqual(len(scheduler.run_due()), 1)


if __name__ == "__main__":
    unittest.main()
