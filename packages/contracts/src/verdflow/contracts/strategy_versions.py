"""Strategy version syntax and forward-only plan compatibility."""

from __future__ import annotations

import re


_NUMBER = r"(?:0|[1-9][0-9]*)"
_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = re.compile(
    rf"^({_NUMBER})\.({_NUMBER})\.({_NUMBER})"
    rf"(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def require_strategy_version(version: str) -> str:
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise ValueError("Strategy version must use SemVer: major.minor.patch")
    return version


def stable_strategy_version(version: str) -> tuple[int, int, int] | None:
    match = _SEMVER.fullmatch(version)
    if match is None or match[4] is not None or int(match[1]) == 0:
        return None
    return tuple(int(match[index]) for index in (1, 2, 3))


def strategy_version_accepts(saved: str, candidate: str) -> bool:
    """Legacy, development and prerelease versions remain exact-only."""
    if saved == candidate:
        return True
    source = stable_strategy_version(saved)
    target = stable_strategy_version(candidate)
    # Build metadata does not establish an upgrade or authorize a different build.
    return bool(source and target and source[0] == target[0] and target > source)
