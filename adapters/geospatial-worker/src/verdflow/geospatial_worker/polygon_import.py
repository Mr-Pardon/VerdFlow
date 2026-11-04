"""Read reusable simple polygon selections without CRS conversion or repair."""

import io
import math
from pathlib import Path, PurePosixPath
import zipfile

from .polygon_export import ShapefilePolygon
from .errors import GeospatialDataError, invalid_polygon_message


MAX_DATASET_BYTES = 128 * 1024 * 1024
MAX_VERTICES = 200_000


class ShapefileImportError(GeospatialDataError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code, message or {
            "shp_polygon_required": "SHP 含有非面要素，请选择仅包含 Polygon/MultiPolygon 的数据；本次未导入任何面。",
            "shp_invalid_geometry": "SHP 包含无效、空或非有限坐标的面，请先在 GIS 软件中修复；本次未导入任何面。",
            "shp_invalid_dataset": "无法解析 SHP。请检查文件完整性、ZIP 路径及 .cpg 字符编码；ZIP 中应只有一套 SHP 数据。",
            "shp_companions_missing": "SHP 缺少同名 .shx 或 .dbf 配套文件，请放在同一目录后重新导入。",
            "shp_single_dataset_required": "ZIP 中应恰好包含一套 SHP 数据，请分别导入多个数据集。",
            "shp_empty": "SHP 中没有可导入的面，请检查是否导出了空图层。",
        }.get(code, "SHP 导入失败，请检查选区数据后重试。"))


def import_polygon_shapefile(source: Path) -> tuple[ShapefilePolygon, ...]:
    """Split multipart records while retaining all interior rings."""
    import shapefile
    from shapely.geometry import shape
    from shapely.errors import GEOSException

    try:
        files = _dataset_files(Path(source))
        encoding = files.get(".cpg", b"UTF-8").decode("utf-8-sig").strip()
        encoding = {"65001": "utf-8", "936": "gbk"}.get(encoding, encoding)
        if encoding.isdigit():
            encoding = f"cp{encoding}"
        polygons: list[ShapefilePolygon] = []
        vertices = 0
        with shapefile.Reader(**{
            suffix[1:]: io.BytesIO(files[suffix]) for suffix in (".shp", ".shx", ".dbf")
        }, encoding=encoding) as reader:
            if reader.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
                raise ShapefileImportError("shp_polygon_required")
            # Check the whole dataset budget before expensive topology validation.
            # The same bounded in-memory snapshot is then decoded with attributes.
            for index, raw in enumerate(reader.iterShapes()):
                vertices += len(raw.points)
                if vertices > MAX_VERTICES:
                    raise ShapefileImportError("shp_vertex_limit", f"读取到第 {index + 1} 条面要素时，累计顶点 {vertices:,} 个，超过实时路径 {MAX_VERTICES:,} 个的上限。请改用工作台的 SHP＋TIF 路径；本次未导入任何面。")
            for index, record in enumerate(reader.iterShapeRecords()):
                raw = record.shape
                if raw.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
                    raise ShapefileImportError("shp_polygon_required")
                if any(not all(math.isfinite(v) for v in point) for point in raw.points):
                    raise ShapefileImportError("shp_invalid_geometry")
                geometry = shape(raw.__geo_interface__)
                if geometry.is_empty or not geometry.is_valid or not math.isfinite(geometry.area) or geometry.area <= 0:
                    raise ShapefileImportError("shp_invalid_geometry", invalid_polygon_message(geometry, index + 1))
                parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
                properties = {key.upper(): value for key, value in record.record.as_dict().items()}
                label = str(properties.get("NAME") or properties.get("LABEL") or f"\u9009\u533a {index + 1}").strip()
                # A 2D export may retain a reference height range for camera positioning.
                # This is not a terrain sample. Plain external 2D data starts at zero.
                z_low, z_high = properties.get("Z_MIN"), properties.get("Z_MAX")
                reference_z = 0.0
                if isinstance(z_low, (int, float)) and isinstance(z_high, (int, float)):
                    if math.isfinite(z_low) and math.isfinite(z_high):
                        reference_z = z_low / 2 + z_high / 2
                z_by_xy = {}
                if raw.shapeType == shapefile.POLYGONZ:
                    if not all(math.isfinite(z) for z in raw.z):
                        raise ShapefileImportError("shp_invalid_geometry")
                    z_by_xy = {tuple(point): z for point, z in zip(raw.points, raw.z)}
                for part_index, polygon in enumerate(parts):
                    if polygon.geom_type != "Polygon":
                        raise ShapefileImportError("shp_polygon_required")
                    name = label if len(parts) == 1 else f"{label} ({part_index + 1})"
                    coordinates = tuple(
                        (float(x), float(y), float(z_by_xy.get((x, y), reference_z)))
                        for x, y in list(polygon.exterior.coords)[:-1]
                    )
                    holes = tuple(tuple(
                        (float(x), float(y), float(z_by_xy.get((x, y), reference_z)))
                        for x, y in list(ring.coords)[:-1]
                    ) for ring in polygon.interiors)
                    polygons.append(ShapefilePolygon(f"{index + 1}_{part_index + 1}", name, coordinates, holes))
        if not polygons:
            raise ShapefileImportError("shp_empty")
        return tuple(polygons)
    except ShapefileImportError:
        raise
    except (ValueError, LookupError, UnicodeError, zipfile.BadZipFile, shapefile.ShapefileException, GEOSException) as error:
        raise ShapefileImportError("shp_invalid_dataset") from error


def _dataset_files(source: Path) -> dict[str, bytes]:
    suffixes = (".shp", ".shx", ".dbf", ".cpg")
    if source.stat().st_size > MAX_DATASET_BYTES:
        raise _size_error(source.stat().st_size)
    if source.suffix.lower() == ".shp":
        siblings = [p for p in source.parent.iterdir() if p.is_file() and p.stem.casefold() == source.stem.casefold() and p.suffix.lower() in suffixes]
        if sum(p.stat().st_size for p in siblings) > MAX_DATASET_BYTES:
            raise _size_error(sum(p.stat().st_size for p in siblings))
        files = {p.suffix.lower(): p.read_bytes() for p in siblings}
    elif source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > 1000:
                raise ShapefileImportError("shp_archive_limit", "ZIP 中的文件超过 1,000 个，请只保留一套 SHP 及其配套文件后重新导入。")
            if sum(item.file_size for item in members) > MAX_DATASET_BYTES:
                raise _size_error(sum(item.file_size for item in members))
            paths = [PurePosixPath(item.filename.replace("\\", "/")) for item in members]
            if any(path.is_absolute() or ".." in path.parts or ":" in str(path) for path in paths):
                raise ShapefileImportError("shp_invalid_dataset")
            if len({str(path).casefold() for path in paths}) != len(paths):
                raise ShapefileImportError("shp_invalid_dataset")
            shapes = [path for path in paths if path.suffix.lower() == ".shp"]
            if len(shapes) != 1:
                raise ShapefileImportError("shp_single_dataset_required")
            selected = shapes[0]
            files = {
                path.suffix.lower(): archive.read(item)
                for item, path in zip(members, paths)
                if str(path.parent).casefold() == str(selected.parent).casefold()
                and path.stem.casefold() == selected.stem.casefold() and path.suffix.lower() in suffixes
            }
    else:
        raise ShapefileImportError("shp_invalid_dataset")
    if any(suffix not in files for suffix in (".shp", ".shx", ".dbf")):
        raise ShapefileImportError("shp_companions_missing")
    return files


def _size_error(size: int) -> ShapefileImportError:
    return ShapefileImportError("shp_file_size_limit", f"选中文件或配套数据解压后的总量为 {size / 1024**2:.1f} MiB，超过实时路径 {MAX_DATASET_BYTES / 1024**2:g} MiB 上限。请改用工作台的 SHP＋TIF 路径；坐标和无效几何仍需先在 GIS 中检查。")
