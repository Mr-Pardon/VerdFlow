"""Blender executable discovery without importing Blender Python modules."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def locate_blender(explicit: Path | str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    configured = os.environ.get("BLENDER_EXE")
    if configured:
        candidates.append(Path(configured).expanduser())
    program_root = Path(r"C:\Program Files\Blender Foundation")
    if program_root.is_dir():
        candidates.extend(
            sorted(
                program_root.glob("Blender */blender.exe"),
                reverse=True,
            )
        )
    found = shutil.which("blender")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None
