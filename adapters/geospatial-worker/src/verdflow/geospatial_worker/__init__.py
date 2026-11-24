"""Public geospatial Worker adapter API."""

from typing import Any

from .actor_footprints import (
    ACTOR_CAPTURE_FRAMES, ACTOR_CAPTURE_SIZE, ACTOR_CAPTURE_WIDTH, ActorFootprintTiles, actor_capture_ready,
)

from .archive import package_shapefile, shapefile_members, write_shapefile_package
from .errors import GeospatialDataError
from .polygon_export import ShapefilePolygon, export_polygon_shapefile, write_polygon_shapefile
from .polygon_import import ShapefileImportError, import_polygon_shapefile, iter_live_shapefile
from .protocol import (
    GEOSPATIAL_ADAPTER_ID,
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    GEOSPATIAL_SUPPORTED_COMMANDS,
    IMAGERY_PREPARE_REGIONS_COMMAND,
    IMAGERY_REVIEW_INDEX_ENCODING,
    IMAGERY_REVIEW_INDEX_ROW,
    IMAGERY_REVIEW_ORDINAL_LIMIT,
    IMAGERY_REGION_PROVIDER_ID,
    IMAGERY_REGION_RECIPE_ID,
    GeospatialWorkerConfig,
    ImageryRegionOptions,
)

__all__ = [
    "ACTOR_CAPTURE_FRAMES",
    "ACTOR_CAPTURE_SIZE",
    "actor_capture_ready",
    "ACTOR_CAPTURE_WIDTH",
    "ActorFootprintTiles",
    "GeospatialDataError",
    "GEOSPATIAL_ADAPTER_ID",
    "GEOSPATIAL_PREPARE_REGIONS_COMMAND",
    "GEOSPATIAL_SAMPLE_ELEVATION_COMMAND",
    "GEOSPATIAL_SUPPORTED_COMMANDS",
    "GeospatialWorkerConfig",
    "GeospatialWorkerSupervisor",
    "IMAGERY_PREPARE_REGIONS_COMMAND",
    "IMAGERY_REVIEW_INDEX_ENCODING",
    "IMAGERY_REVIEW_INDEX_ROW",
    "IMAGERY_REVIEW_ORDINAL_LIMIT",
    "IMAGERY_REGION_PROVIDER_ID",
    "IMAGERY_REGION_RECIPE_ID",
    "ImageryRegionOptions",
    "ImageryTileReader",
    "package_shapefile",
    "shapefile_members",
    "write_shapefile_package",
    "ShapefilePolygon",
    "export_polygon_shapefile",
    "write_polygon_shapefile",
    "ShapefileImportError",
    "import_polygon_shapefile",
    "iter_live_shapefile",
]


def __getattr__(name: str) -> Any:
    if name == "ImageryTileReader":
        from .tile_reader import ImageryTileReader

        return ImageryTileReader
    if name == "GeospatialWorkerSupervisor":
        from .supervisor import GeospatialWorkerSupervisor

        return GeospatialWorkerSupervisor
    raise AttributeError(name)
