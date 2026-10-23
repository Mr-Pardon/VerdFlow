"""Run an explicitly trusted strategy package outside the Core process."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Collection

from verdflow.planting import canonical_json_bytes, validate_point_set_v2
from verdflow.worker_runtime import ProcessTreeGuard

from .errors import (
    StrategyExecutionCancelled,
    StrategyExecutionError,
    StrategyPackageError,
    StrategyTrustRequiredError,
)
from .package import StrategyPackage, inspect_strategy_package


@dataclass(frozen=True)
class StrategyWorkerConfig:
    python_executable: Path = Path(sys.executable)
    timeout_seconds: float | None = None
    max_output_bytes: int = 256 * 1024 * 1024
    poll_interval_seconds: float = 0.1
    termination_grace_seconds: float = 3.0


class StrategyWorker:
    """Execute exact trusted package hashes using the trusted-local model."""

    def __init__(
        self,
        trusted_hashes: Collection[str],
        config: StrategyWorkerConfig | None = None,
    ) -> None:
        self._trusted_hashes = frozenset(trusted_hashes)
        self._config = config or StrategyWorkerConfig()

    def run(
        self,
        package: StrategyPackage,
        context: dict[str, Any],
        *,
        heartbeat: Callable[[], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        process_started: Callable[[int], None] | None = None,
        process_finished: Callable[[int | None, bool], None] | None = None,
        workdir_parent: Path | None = None,
        workdir_prefix: str = "verdflow-strategy-",
    ) -> dict[str, Any]:
        current = inspect_strategy_package(package.root)
        if current.package_hash != package.package_hash:
            raise StrategyPackageError(
                "Strategy package changed after inspection; inspect and trust the new hash"
            )
        if current.package_hash not in self._trusted_hashes:
            raise StrategyTrustRequiredError(
                f"Strategy package hash is not trusted: {current.package_hash}"
            )
        timeout_seconds = self._config.timeout_seconds
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive, or None")
        if self._config.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if self._config.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self._config.termination_grace_seconds <= 0:
            raise ValueError("termination_grace_seconds must be positive")
        if workdir_parent is not None and not workdir_parent.is_dir():
            raise ValueError("workdir_parent must be an existing directory")

        with tempfile.TemporaryDirectory(
            prefix=workdir_prefix,
            dir=workdir_parent,
        ) as temporary:
            workdir = Path(temporary)
            staged_root = workdir / "package"
            _stage_verified_package(current, staged_root)
            command_path = workdir / "command.json"
            output_path = workdir / "point-set.json"
            start_gate_path = workdir / "start.gate"
            stdout_path = workdir / "stdout.log"
            stderr_path = workdir / "stderr.log"
            command_path.write_bytes(canonical_json_bytes(context))
            entry_script = Path(__file__).with_name("worker_entry.py")
            staged_entry = staged_root / current.entry_file.relative_to(current.root)
            command = [
                str(self._config.python_executable),
                "-I",
                "-S",
                str(entry_script),
                "--package-root",
                str(staged_root),
                "--entry-file",
                str(staged_entry),
                "--entry-function",
                current.entry_function,
                "--command",
                str(command_path),
                "--output",
                str(output_path),
                "--start-gate",
                str(start_gate_path),
            ]
            process: subprocess.Popen[bytes] | None = None
            guard = ProcessTreeGuard()
            terminated = False
            with stdout_path.open("wb") as stdout_stream, stderr_path.open(
                "wb"
            ) as stderr_stream:
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=workdir,
                        env=_minimal_environment(workdir),
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                        **guard.popen_options(),
                    )
                    try:
                        guard.attach(process)
                    except Exception:
                        process.kill()
                        process.wait(timeout=self._config.termination_grace_seconds)
                        raise
                except OSError as error:
                    guard.close()
                    raise StrategyExecutionError(
                        f"Cannot start strategy worker: {error}"
                    ) from error
                deadline = (
                    time.monotonic() + timeout_seconds
                    if timeout_seconds is not None
                    else None
                )
                try:
                    if process_started is not None:
                        process_started(process.pid)
                    start_gate_path.write_bytes(b"start")
                    while process.poll() is None:
                        if (
                            cancellation_requested is not None
                            and cancellation_requested()
                        ):
                            terminated = True
                            guard.terminate(
                                process,
                                grace_seconds=(
                                    self._config.termination_grace_seconds
                                ),
                            )
                            raise StrategyExecutionCancelled(
                                "Strategy worker was cancelled"
                            )
                        if deadline is not None and time.monotonic() >= deadline:
                            terminated = True
                            guard.terminate(
                                process,
                                grace_seconds=(
                                    self._config.termination_grace_seconds
                                ),
                            )
                            raise StrategyExecutionError(
                                "Strategy worker exceeded "
                                f"{self._config.timeout_seconds} seconds"
                            )
                        if heartbeat is not None:
                            heartbeat()
                        time.sleep(self._config.poll_interval_seconds)
                except BaseException:
                    if process.poll() is None:
                        terminated = True
                        guard.terminate(
                            process,
                            grace_seconds=(
                                self._config.termination_grace_seconds
                            ),
                        )
                    raise
                finally:
                    return_code = (
                        process.returncode if process is not None else None
                    )
                    try:
                        if process_finished is not None and process is not None:
                            process_finished(return_code, terminated)
                    finally:
                        guard.close()

            if return_code != 0:
                stderr = _read_tail(stderr_path, 4000)
                raise StrategyExecutionError(
                    f"Strategy worker exited with code {return_code}: {stderr}"
                )
            if not output_path.is_file():
                raise StrategyExecutionError("Strategy worker did not produce a PointSet")
            if output_path.stat().st_size > self._config.max_output_bytes:
                raise StrategyExecutionError("Strategy worker output exceeds configured limit")
            try:
                value = json.loads(output_path.read_text(encoding="utf-8"))
                return validate_point_set_v2(value)
            except Exception as error:
                raise StrategyExecutionError(
                    f"Strategy worker returned an invalid PointSet: {error}"
                ) from error

def _stage_verified_package(package: StrategyPackage, destination: Path) -> None:
    destination.mkdir()
    for relative, expected_hash in package.file_hashes:
        source = package.root / Path(relative)
        content = source.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise StrategyPackageError(
                f"Strategy package changed while staging: {relative}"
            )
        target = destination / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _read_tail(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - limit))
        return stream.read().decode("utf-8", errors="replace")


def _minimal_environment(workdir: Path) -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT")
    environment = {
        key: os.environ[key]
        for key in allowed
        if key in os.environ
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(workdir),
            "TMP": str(workdir),
        }
    )
    return environment
