"""Bounded, cancellable execution of an already-authorized example preview."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Callable

from verdflow.contracts import StrategyPreviewInput, StrategyPreviewResult
from verdflow.worker_runtime import ProcessTreeGuard

from .errors import (
    StrategyExecutionCancelled,
    StrategyExecutionError,
    StrategyPackageError,
)
from .package import StrategyPackage
from .runner import _minimal_environment, _read_tail, _stage_verified_package


@dataclass(frozen=True, slots=True)
class StrategyPreviewWorkerConfig:
    python_executable: Path = Path(sys.executable)
    timeout_seconds: float = 30.0
    max_points: int = 100000
    max_output_bytes: int = 8 * 1024 * 1024


class StrategyPreviewWorker:
    """Not a formal generation worker: output is only a typed display projection."""

    def __init__(
        self,
        package: StrategyPackage,
        workdir_parent: Path,
        config: StrategyPreviewWorkerConfig | None = None,
    ) -> None:
        self._package = package
        self._parent = workdir_parent
        self._config = config or StrategyPreviewWorkerConfig()
        if (
            not math.isfinite(self._config.timeout_seconds)
            or self._config.timeout_seconds <= 0
            or self._config.max_points <= 0
            or self._config.max_output_bytes <= 0
        ):
            raise ValueError("Preview budgets must be finite and positive")

    def generate_example(
        self,
        request: StrategyPreviewInput,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> StrategyPreviewResult:
        package = self._package
        if request.package_hash != package.package_hash:
            raise StrategyPackageError("Preview does not match the authorized package")
        if cancellation_requested():
            raise StrategyExecutionCancelled("Example preview cancelled")
        with tempfile.TemporaryDirectory(
            prefix="example-", dir=self._parent
        ) as temporary:
            workdir = Path(temporary)
            staged = workdir / "package"
            _stage_verified_package(package, staged)
            command_path, output_path, gate = (
                workdir / "command.json",
                workdir / "preview.json",
                workdir / "start.gate",
            )
            command = {
                "package_root": str(staged),
                "entry_file": package.entry_file.relative_to(package.root).as_posix(),
                "entry_function": package.entry_function,
                "role_keys": request.role_keys,
                "max_points": self._config.max_points,
                "context": {
                    "strategy": package.strategy,
                    "selected_regions": [
                        {
                            "id": "strategy_example",
                            "rings": [
                                {
                                    "role": "outer",
                                    "loop": [
                                        [x, y, 0.0]
                                        for x, y in request.sample.boundary_xy
                                    ],
                                }
                            ],
                        }
                    ],
                    "seed": request.sample.fixed_seed,
                    "args": {"strategy_parameters": dict(request.strategy_parameters)},
                    "excluded_role_keys": list(request.excluded_role_keys),
                },
            }
            command_path.write_text(json.dumps(command), encoding="utf-8")
            guard = ProcessTreeGuard()
            process = None
            started = time.monotonic()
            try:
                with (
                    (workdir / "stdout.log").open("wb") as stdout,
                    (workdir / "stderr.log").open("wb") as stderr,
                ):
                    process = subprocess.Popen(
                        [
                            str(self._config.python_executable),
                            "-I",
                            "-S",
                            str(Path(__file__).with_name("preview_entry.py")),
                            str(command_path),
                            str(output_path),
                            str(gate),
                        ],
                        cwd=workdir,
                        env=_minimal_environment(workdir),
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        **guard.popen_options(),
                    )
                    guard.attach(process)
                    gate.write_bytes(b"start")
                    while process.poll() is None:
                        if cancellation_requested():
                            raise StrategyExecutionCancelled(
                                "Example preview cancelled"
                            )
                        if time.monotonic() - started > self._config.timeout_seconds:
                            raise StrategyExecutionError(
                                "Example preview exceeded its execution budget"
                            )
                        if (
                            output_path.exists()
                            and output_path.stat().st_size
                            > self._config.max_output_bytes
                        ):
                            raise StrategyExecutionError(
                                "Example preview output is too large"
                            )
                        time.sleep(0.01)
                if process.returncode:
                    raise StrategyExecutionError(
                        "Example preview failed: "
                        + _read_tail(workdir / "stderr.log", 1200)
                    )
                if cancellation_requested():
                    raise StrategyExecutionCancelled("Example preview cancelled")
                if (
                    not output_path.is_file()
                    or output_path.stat().st_size > self._config.max_output_bytes
                ):
                    raise StrategyExecutionError(
                        "Example preview output is missing or too large"
                    )
                return self._result(
                    request, json.loads(output_path.read_text(encoding="utf-8"))
                )
            finally:
                if process is not None and process.poll() is None:
                    guard.terminate(process, grace_seconds=1.0)
                guard.close()

    def _result(
        self, request: StrategyPreviewInput, value: object
    ) -> StrategyPreviewResult:
        points = value.get("points") if isinstance(value, dict) else None
        point_ids = value.get("point_ids") if isinstance(value, dict) else None
        if not isinstance(points, list) or len(points) > self._config.max_points:
            raise StrategyExecutionError("Invalid preview point projection")
        if (
            not isinstance(point_ids, list)
            or (point_ids and len(point_ids) != len(points))
            or any(
                not isinstance(v, str) or not v.strip() or len(v) > 256
                for v in point_ids
            )
            or len(set(point_ids)) != len(point_ids)
        ):
            raise StrategyExecutionError("Invalid preview point identities")
        excluded = set(request.excluded_role_keys)
        parsed = []
        for point in points:
            if (
                not isinstance(point, list)
                or len(point) != 3
                or any(
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or abs(v) > 1e9
                    for v in point[:2]
                )
                or isinstance(point[2], bool)
                or not isinstance(point[2], int)
                or not 0 <= point[2] < len(request.role_keys)
                or request.role_keys[point[2]] in excluded
            ):
                raise StrategyExecutionError("Invalid preview coordinate or role")
            parsed.append((float(point[0]), float(point[1]), point[2]))
        return StrategyPreviewResult(
            request.package_hash, request.role_keys, tuple(parsed), tuple(point_ids)
        )
