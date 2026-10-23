from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
import warnings
import zipfile
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "worker-runtime" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "planting" / "src"))
sys.path.insert(0, str(ROOT / "adapters" / "strategy-worker" / "src"))

from verdflow.strategy_worker import (  # noqa: E402
    StrategyExecutionCancelled,
    StrategyExecutionError,
    StrategyPackageError,
    StrategyTrustRequiredError,
    StrategyWorker,
    StrategyWorkerConfig,
    inspect_strategy_package,
    open_strategy_package_archive,
    write_strategy_package_archive,
)


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-street-tree"


def rectangle_region(region_id: str, width: float, length: float = 40.0) -> dict:
    half_width = width * 0.5
    return {
        "id": region_id,
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [0.0, -half_width, 2.0],
                    [length, -half_width, 2.0],
                    [length, half_width, 2.0],
                    [0.0, half_width, 2.0],
                ],
            }
        ],
    }


class StrategyWorkerTests(unittest.TestCase):
    def test_inspects_builtin_package_without_importing_generator(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)

        self.assertEqual(package.package_id, "universal_street_tree")
        self.assertEqual(package.package_version, "2.0.2")
        self.assertEqual(package.entry_function, "generate")
        self.assertRegex(package.package_hash, r"^sha256:[0-9a-f]{64}$")

    def test_exact_hash_trust_runs_generator_and_validates_point_set(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)
        worker = StrategyWorker({package.package_hash})

        point_set = worker.run(
            package,
            {
                "strategy": package.strategy,
                "selected_regions": [rectangle_region("double", 2.5)],
                "seed": 2026071301,
            },
        )

        self.assertEqual(point_set["schema"], "verdflow.points")
        self.assertGreater(point_set["point_count"], 0)
        self.assertNotIn("asset_id", point_set["points"][0])

    def test_start_gate_keeps_worker_alive_until_provenance_is_registered(
        self,
    ) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)
        observed_pid: list[int] = []

        def delayed_registration(pid: int) -> None:
            time.sleep(0.2)
            os.kill(pid, 0)
            observed_pid.append(pid)

        point_set = StrategyWorker({package.package_hash}).run(
            package,
            {
                "strategy": package.strategy,
                "selected_regions": [rectangle_region("gated", 2.5)],
                "seed": 2026071301,
            },
            process_started=delayed_registration,
        )

        self.assertEqual(len(observed_pid), 1)
        self.assertGreater(point_set["point_count"], 0)

    def test_untrusted_hash_is_rejected_before_execution(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)

        with self.assertRaises(StrategyTrustRequiredError):
            StrategyWorker(set()).run(package, {})

    def test_cancellation_terminates_direct_worker(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)

        with self.assertRaises(StrategyExecutionCancelled):
            StrategyWorker({package.package_hash}).run(
                package,
                {
                    "strategy": package.strategy,
                    "selected_regions": [rectangle_region("cancel", 2.5)],
                    "seed": 1,
                },
                cancellation_requested=lambda: True,
            )

    def test_default_long_run_keeps_heartbeat_and_produces_points(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)
        heartbeat = Mock()
        clock = Mock()
        clock.monotonic.side_effect = [0.0] + [3600.0] * 100
        clock.sleep = time.sleep
        self.assertIsNone(StrategyWorkerConfig().timeout_seconds)
        with patch("verdflow.strategy_worker.runner.time", clock):
            point_set = StrategyWorker({package.package_hash}).run(
                package,
                {
                    "strategy": package.strategy,
                    "selected_regions": [rectangle_region("long-run", 2.5)],
                    "seed": 1,
                },
                heartbeat=heartbeat,
            )
        self.assertGreater(point_set["point_count"], 0)
        self.assertGreater(heartbeat.call_count, 0)
        clock.monotonic.assert_not_called()

    def test_unbounded_running_worker_can_be_cancelled_after_heartbeats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "strategy"
            shutil.copytree(STRATEGY_ROOT, copied)
            (copied / "generator.py").write_text(
                "import time\n\ndef generate(context):\n    time.sleep(5)\n    return {}\n",
                encoding="utf-8",
            )
            package = inspect_strategy_package(copied)
            heartbeat = Mock()
            finished = Mock()
            with self.assertRaises(StrategyExecutionCancelled):
                StrategyWorker({package.package_hash}).run(
                    package, {}, heartbeat=heartbeat,
                    cancellation_requested=lambda: heartbeat.call_count >= 2,
                    process_finished=finished,
                )
            self.assertGreaterEqual(heartbeat.call_count, 2)
            self.assertIsNotNone(finished.call_args.args[0])
            self.assertTrue(finished.call_args.args[1])

    def test_explicit_deadline_must_be_finite_and_positive(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)
        for invalid in (0.0, -1.0, float("inf"), float("nan")):
            with self.subTest(timeout=invalid):
                with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                    StrategyWorker(
                        {package.package_hash}, StrategyWorkerConfig(timeout_seconds=invalid),
                    ).run(package, {})

    def test_changed_package_invalidates_previously_inspected_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "strategy"
            shutil.copytree(STRATEGY_ROOT, copied)
            package = inspect_strategy_package(copied)
            with (copied / "generator.py").open("a", encoding="utf-8") as stream:
                stream.write("\n# changed after trust\n")

            with self.assertRaisesRegex(StrategyPackageError, "changed after inspection"):
                StrategyWorker({package.package_hash}).run(package, {})

    def test_unlisted_file_and_path_traversal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "strategy"
            shutil.copytree(STRATEGY_ROOT, copied)
            (copied / "undeclared.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(StrategyPackageError, "file set mismatch"):
                inspect_strategy_package(copied)

    def test_vfs_archive_is_deterministic_and_round_trips_exact_package(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.vfs"
            second = Path(temporary) / "second.vfs"

            first_info = write_strategy_package_archive(package, first)
            second_info = write_strategy_package_archive(package, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_info.archive_hash, second_info.archive_hash)
            self.assertEqual(first_info.package_hash, package.package_hash)
            with open_strategy_package_archive(first) as opened:
                self.assertEqual(opened.package.package_hash, package.package_hash)
                self.assertEqual(opened.archive_hash, first_info.archive_hash)

    def test_vfs_archive_rejects_unsafe_and_duplicate_entries(self) -> None:
        package = inspect_strategy_package(STRATEGY_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            valid = Path(temporary) / "valid.vfs"
            write_strategy_package_archive(package, valid)

            unsafe = Path(temporary) / "unsafe.vfs"
            with zipfile.ZipFile(unsafe, mode="w") as archive:
                archive.writestr("../outside.txt", b"unexpected")
            with self.assertRaisesRegex(StrategyPackageError, "Unsafe"):
                with open_strategy_package_archive(unsafe):
                    pass

            duplicate = Path(temporary) / "duplicate.vfs"
            duplicate.write_bytes(valid.read_bytes())
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, mode="a") as archive:
                    archive.writestr("generator.py", b"duplicate")
            with self.assertRaisesRegex(StrategyPackageError, "Duplicate"):
                with open_strategy_package_archive(duplicate):
                    pass

        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "strategy"
            shutil.copytree(STRATEGY_ROOT, copied)
            manifest_path = copied / "strategy-package.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["strategy_file"] = "../strategy.yaml"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(StrategyPackageError, "Invalid strategy package path"):
                inspect_strategy_package(copied)

    def test_invalid_output_and_timeout_are_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "strategy"
            shutil.copytree(STRATEGY_ROOT, copied)
            (copied / "generator.py").write_text(
                "def generate(context):\n    return {'invalid': True}\n",
                encoding="utf-8",
            )
            package = inspect_strategy_package(copied)
            with self.assertRaisesRegex(StrategyExecutionError, "invalid PointSet"):
                StrategyWorker({package.package_hash}).run(package, {})

        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "strategy"
            shutil.copytree(STRATEGY_ROOT, copied)
            (copied / "generator.py").write_text(
                "import time\n\ndef generate(context):\n    time.sleep(5)\n    return {}\n",
                encoding="utf-8",
            )
            package = inspect_strategy_package(copied)
            worker = StrategyWorker(
                {package.package_hash},
                StrategyWorkerConfig(timeout_seconds=0.1),
            )
            with self.assertRaisesRegex(StrategyExecutionError, "exceeded"):
                worker.run(package, {})


if __name__ == "__main__":
    unittest.main()
