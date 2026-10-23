"""Versioned capability declaration for worker handshakes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..protocol import CAPABILITY_BUNDLE_VERSION


CAPABILITY_BUNDLE_ID = "verdflow-blender-capabilities"
def capability_manifest() -> dict[str, object]:
    return {
        "bundle_id": CAPABILITY_BUNDLE_ID,
        "bundle_version": CAPABILITY_BUNDLE_VERSION,
        "bundle_hash": _bundle_hash(),
        "capabilities": [
            {"id": "assets.inspect-library", "version": "1"},
            {"id": "scene.inspect", "version": "1"},
            {"id": "regions.extract", "version": "1"},
            {"id": "points.sample-elevation", "version": "2"},
        ],
    }


def _bundle_hash() -> str:
    package_root = Path(__file__).resolve().parents[1]
    files = [
        package_root / "algorithms.py",
        *sorted((package_root / "capabilities").glob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
