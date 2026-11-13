"""Application-edge helpers for deterministic Shapefile ingestion."""

from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO, Callable
import zipfile
from .errors import GeospatialDataError


REQUIRED_SUFFIXES = (".shp", ".shx", ".dbf")
OPTIONAL_SUFFIXES = (".prj", ".cpg")


def package_shapefile(shp_path: Path) -> bytes:
    """In-memory convenience for small direct callers."""
    output = io.BytesIO()
    write_shapefile_package(shp_path, output)
    return output.getvalue()


def shapefile_members(shp_path: Path) -> tuple[Path, ...]:
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
    return tuple(siblings[suffix] for suffix in (*REQUIRED_SUFFIXES, *OPTIONAL_SUFFIXES) if suffix in siblings)


def write_shapefile_package(
    shp_path: Path, output: BinaryIO, *, on_progress: Callable[[int], None] | None = None,
) -> None:
    """Stream one Shapefile dataset into a seekable deterministic ZIP."""
    members = shapefile_members(shp_path)
    copied = 0
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in members:
            suffix = path.suffix.lower()
            info = zipfile.ZipInfo(f"dataset{suffix}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.file_size = path.stat().st_size
            with path.open("rb") as stream, archive.open(info, "w") as member:
                while chunk := stream.read(1024 * 1024):
                    member.write(chunk)
                    copied += len(chunk)
                    if on_progress is not None:
                        on_progress(copied)
