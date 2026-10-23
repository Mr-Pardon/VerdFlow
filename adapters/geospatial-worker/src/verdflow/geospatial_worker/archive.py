"""Application-edge helpers for deterministic Shapefile ingestion."""

from __future__ import annotations

import io
from pathlib import Path
import zipfile


REQUIRED_SUFFIXES = (".shp", ".shx", ".dbf")
OPTIONAL_SUFFIXES = (".prj", ".cpg")


def package_shapefile(shp_path: Path) -> bytes:
    """Package one Shapefile dataset as a deterministic ZIP Artifact."""
    source = Path(shp_path)
    if source.suffix.lower() != ".shp" or not source.is_file():
        raise ValueError("shp_path must identify an existing .shp file")
    siblings = {
        candidate.suffix.lower(): candidate
        for candidate in source.parent.iterdir()
        if candidate.is_file() and candidate.stem.casefold() == source.stem.casefold()
    }
    missing = [suffix for suffix in REQUIRED_SUFFIXES if suffix not in siblings]
    if missing:
        raise ValueError(
            "Shapefile companion file is missing: " + ", ".join(missing)
        )
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for suffix in (*REQUIRED_SUFFIXES, *OPTIONAL_SUFFIXES):
            path = siblings.get(suffix)
            if path is None:
                continue
            info = zipfile.ZipInfo(f"dataset{suffix}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return output.getvalue()
