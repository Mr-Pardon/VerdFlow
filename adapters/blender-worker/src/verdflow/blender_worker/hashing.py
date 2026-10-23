"""Content hashing helpers for Blender provider observations."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


CONTENT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def file_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
