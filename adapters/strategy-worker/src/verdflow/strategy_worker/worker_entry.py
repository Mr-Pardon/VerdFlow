"""Private JSON-file protocol entry for trusted-local strategy execution."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--entry-file", type=Path, required=True)
    parser.add_argument("--entry-function", required=True)
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-gate", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _wait_for_start_gate(args.start_gate, timeout_seconds=30.0)
    package_root = args.package_root.resolve(strict=True)
    entry_file = args.entry_file.resolve(strict=True)
    entry_file.relative_to(package_root)
    sys.path.insert(0, str(package_root))

    spec = importlib.util.spec_from_file_location("verdflow_strategy_entry", entry_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load strategy entry: {entry_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generator = getattr(module, args.entry_function, None)
    if not callable(generator):
        raise RuntimeError(f"Strategy entry is not callable: {args.entry_function}")

    context = json.loads(args.command.read_text(encoding="utf-8"))
    result = generator(context)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0


def _wait_for_start_gate(path: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("Strategy Worker start gate timed out")
        time.sleep(0.01)


if __name__ == "__main__":
    raise SystemExit(main())
