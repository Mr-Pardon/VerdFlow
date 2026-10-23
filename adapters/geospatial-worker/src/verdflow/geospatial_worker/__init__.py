"""Public geospatial Worker adapter API."""

from typing import Any

from .archive import package_shapefile
from .polygon_export import ShapefilePolygon, export_polygon_shapefile
from .polygon_import import ShapefileImportError, import_polygon_shapefile
from .protocol import (
    GEOSPATIAL_ADAPTER_ID,
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    GEOSPATIAL_SUPPORTED_COMMANDS,
    GeospatialWorkerConfig,
)

__all__ = [
    "GEOSPATIAL_ADAPTER_ID",
    "GEOSPATIAL_PREPARE_REGIONS_COMMAND",
    "GEOSPATIAL_SAMPLE_ELEVATION_COMMAND",
    "GEOSPATIAL_SUPPORTED_COMMANDS",
    "GeospatialWorkerConfig",
    "GeospatialWorkerSupervisor",
    "package_shapefile",
    "ShapefilePolygon",
    "export_polygon_shapefile",
    "ShapefileImportError",
    "import_polygon_shapefile",
]


def __getattr__(name: str) -> Any:
    if name == "GeospatialWorkerSupervisor":
        from .supervisor import GeospatialWorkerSupervisor

        return GeospatialWorkerSupervisor
    raise AttributeError(name)
