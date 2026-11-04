"""Application-edge helpers for deterministic Shapefile ingestion."""

from __future__ import annotations

import io
from pathlib import Path
import zipfile
from .errors import GeospatialDataError


REQUIRED_SUFFIXES = (".shp", ".shx", ".dbf")
OPTIONAL_SUFFIXES = (".prj", ".cpg")


def package_shapefile(shp_path: Path) -> bytes:
    """Package one Shapefile dataset as a deterministic ZIP Artifact."""
    source = Path(shp_path)
    if source.suffix.lower() != ".shp" or not source.is_file():
        raise GeospatialDataError("geo_source_missing", "无法读取所选 SHP 文件，请确认文件仍存在、扩展名为 .shp，且当前用户有读取权限。")
    siblings = {
        candidate.suffix.lower(): candidate
        for candidate in source.parent.iterdir()
        if candidate.is_file() and candidate.stem.casefold() == source.stem.casefold()
    }
    missing = [suffix for suffix in REQUIRED_SUFFIXES if suffix not in siblings]
    if missing:
        raise GeospatialDataError("geo_companions_missing", "SHP 缺少同名配套文件 " + "、".join(missing) + "。请将 .shp、.shx、.dbf 放在同一目录后重新选择。")
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
