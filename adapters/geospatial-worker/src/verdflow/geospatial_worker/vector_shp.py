"""Shared disk-backed SHP input for workbench regions and live selections."""
from __future__ import annotations

import math
from pathlib import Path
import shutil
from typing import Any
import zipfile

from .errors import GeospatialDataError, invalid_polygon_message
from .archive import OPTIONAL_SUFFIXES, REQUIRED_SUFFIXES, shapefile_members


def unpack_shapefile(archive_path: Path, workdir: Path, report) -> Path:
    destination = workdir / "shapefile"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        paths = [Path(member.filename.replace("\\", "/")) for member in members]
        if any(path.anchor or not path.parts or ".." in path.parts or
               any(":" in part for part in path.parts) for path in paths):
            raise GeospatialDataError("geo_invalid_archive", "SHP 压缩包包含不安全路径，未解压数据。请重新打包一套 SHP 及其配套文件。")
        if len(members) > 1000 or len({str(path).casefold() for path in paths}) != len(paths):
            raise GeospatialDataError("geo_invalid_archive", "SHP 压缩包条目过多或包含同名文件。")
        files = [(m, p) for m, p in zip(members, paths, strict=True) if not m.is_dir()]
        shapes = [p for _, p in files if p.suffix.lower() == ".shp"]
        if len(shapes) != 1:
            raise GeospatialDataError("geo_single_dataset_required", "压缩包中应只有一套 SHP 数据，请分别导入多个图层。")
        base = str(shapes[0].with_suffix("")).casefold()
        selected = [(m, p.suffix.lower()) for m, p in files if p.suffix.lower() in REQUIRED_SUFFIXES + OPTIONAL_SUFFIXES
                    and str(p.with_suffix("")).casefold() == base]
        total = sum(member.file_size for member, _ in selected)
        reserve = 256 * 1024**2
        if total + reserve > shutil.disk_usage(destination).free:
            raise GeospatialDataError("geo_disk_budget", "项目磁盘空间不足，无法解压 SHP。请释放空间后重试。")
        completed = 0
        for member, suffix in selected:
            target = destination / ("dataset" + suffix)
            with archive.open(member) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    if completed + len(chunk) > total or shutil.disk_usage(destination).free < reserve + len(chunk):
                        raise GeospatialDataError("geo_disk_budget", "项目磁盘空间不足或 SHP 压缩包解压大小不符。")
                    output.write(chunk)
                    completed += len(chunk)
                    report("unpacking_source", completed, total)
    return destination / "dataset.shp"


def shapefile_records(shp_path: Path, report):
    import shapefile
    from shapely.geometry import shape
    from contextlib import ExitStack

    files = {p.suffix.lower(): p for p in shapefile_members(shp_path)}
    encoding = "utf-8"
    if ".cpg" in files:
        with files[".cpg"].open("rb") as stream:
            raw = stream.read(257)
        if len(raw) > 256:
            raise GeospatialDataError("geo_invalid_encoding", "SHP 的 .cpg 编码声明过长。")
        encoding = raw.decode("utf-8-sig").strip() or encoding
        encoding = {"65001": "utf-8", "936": "gbk"}.get(encoding, encoding)
        if encoding.isdigit():
            encoding = "cp" + encoding
    with ExitStack() as stack:
        handles = {k[1:]: stack.enter_context(files[k].open("rb")) for k in (".shp", ".shx", ".dbf")}
        reader = stack.enter_context(shapefile.Reader(**handles, encoding=encoding))
        for index, record in enumerate(reader.iterShapeRecords(), 1):
            geometry = shape(record.shape.__geo_interface__)
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise GeospatialDataError("geo_polygon_required", f"SHP 第 {index} 条要素不是面，请选择 Polygon/MultiPolygon 图层。")
            yield geometry, record.record.as_dict(), record.shape
            report("preparing_regions", index, max(reader.numRecords, index))


def validate_polygon(polygon: Any, feature_index: int, part_index: int) -> None:
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise GeospatialDataError("geo_invalid_geometry", invalid_polygon_message(polygon, feature_index + 1))
    for ring in (polygon.exterior, *polygon.interiors):
        if not all(
            math.isfinite(float(coordinate[0])) and math.isfinite(float(coordinate[1]))
            for coordinate in ring.coords
        ):
            raise GeospatialDataError("geo_invalid_coordinates", f"第 {feature_index + 1} 条面要素含有无效坐标，请在 GIS 中检查并修复；未自动丢弃顶点。")
