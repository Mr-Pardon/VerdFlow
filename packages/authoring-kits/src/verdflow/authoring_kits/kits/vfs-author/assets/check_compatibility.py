"""Offline authoring checks. Never imports or executes a strategy generator."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from strategy_versions import require_strategy_version, stable_strategy_version
except ImportError:
    from verdflow.contracts import require_strategy_version, stable_strategy_version


def _read(root: Path) -> dict:
    try:
        import yaml
    except ImportError as error:
        raise ValueError("Compatibility checks require PyYAML: python -m pip install PyYAML") from error
    root = root.resolve(strict=True)
    manifest = json.loads((root / "strategy-package.json").read_text(encoding="utf-8"))
    path = (root / manifest["strategy_file"]).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("Strategy document must stay inside its package")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Strategy document exceeds the size limit")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    require_strategy_version(document["strategy_version"])
    if (document["strategy_id"], document["strategy_version"]) != (
        manifest["package_id"], manifest["package_version"],
    ):
        raise ValueError("Strategy and package identities must match")
    return document


def check_update(previous: Path, current: Path) -> None:
    before, after = _read(previous), _read(current)
    if before["strategy_id"] != after["strategy_id"]:
        raise ValueError("Comparison requires the same strategy_id")
    old = stable_strategy_version(before["strategy_version"])
    new = stable_strategy_version(after["strategy_version"])
    if old and new and new <= old:
        raise ValueError("A new release must increase the strategy version")
    if not old or not new or old[0] != new[0]:
        return
    issues = compatible_update_issues(before, after)
    if issues:
        raise ValueError("Breaking changes require a major version: " + "; ".join(issues))


def compatible_update_issues(before: dict, after: dict) -> list[str]:
    issues = []
    old_roles = {item["role_key"]: item for item in before["roles"]}
    new_roles = {item["role_key"]: item for item in after["roles"]}
    for key, role in old_roles.items():
        if key not in new_roles:
            issues.append(f"removed role {key}")
            continue
        for field in ("assignment_policy", "relationship_rules"):
            if role.get(field) != new_roles[key].get(field):
                issues.append(f"changed role {key}.{field}")
        if not role["required"] and new_roles[key]["required"]:
            issues.append(f"role {key} became required")
    for key in new_roles.keys() - old_roles.keys():
        if new_roles[key]["required"]:
            issues.append(f"new required role {key}")
    old_parameters = {item["key"]: item for item in before.get("generation_parameters", [])}
    new_parameters = {item["key"]: item for item in after.get("generation_parameters", [])}
    for key, parameter in old_parameters.items():
        newer = new_parameters.get(key)
        if newer is None:
            issues.append(f"removed parameter {key}")
            continue
        if newer["value_type"] != parameter["value_type"]:
            issues.append(f"changed parameter type {key}")
        if newer["min"] > parameter["min"] or newer["max"] < parameter["max"]:
            issues.append(f"narrowed parameter range {key}")
        if newer["default"] != parameter["default"]:
            issues.append(f"changed default for partial plans: {key}")
    return issues
