"""Run one real material-classification gate without persisting credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
]

from verdflow.model_api import (  # noqa: E402
    EnvironmentSecretResolver,
    ModelApiClient,
    ModelApiProviderRegistry,
    parse_provider_config_document,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--provider-config", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--task-id", default="material_classification_probe")
    args = parser.parse_args()

    fact_report = _read_object(args.input, "fact report")
    custom = ()
    if args.provider_config is not None:
        custom = parse_provider_config_document(
            _read_object(args.provider_config, "provider config")
        )
    registry = ModelApiProviderRegistry(custom)
    api_key = EnvironmentSecretResolver(
        provider_registry=registry
    ).resolve_provider(args.provider)
    candidate = ModelApiClient(
        provider_registry=registry
    ).analyze_materials(
        fact_report,
        provider_id=args.provider,
        api_key=api_key,
        input_ref=f"file:{args.input.name}",
        model=args.model,
        task_id=args.task_id,
    )
    result = {
        "schema": "verdflow.model-api-material-probe",
        "version": "1",
        "status": "passed",
        "candidate": candidate.to_document(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _read_object(path: Path, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
