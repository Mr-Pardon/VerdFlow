"""Resolve product execution routes from immutable project lineage."""

from __future__ import annotations

from dataclasses import dataclass

from verdflow.blender_worker import BLENDER_WORKER_ADAPTER_ID
from verdflow.dts_adapter import DTS_ADAPTER_ID


@dataclass(frozen=True, slots=True)
class PlantOutputRoute:
    elevation_provider_id: str
    export_provider_id: str


_ELEVATION_BY_IMPORTER = {
    BLENDER_WORKER_ADAPTER_ID: BLENDER_WORKER_ADAPTER_ID,
    "verdflow.geospatial": "verdflow.geospatial",
}
_REGION_EXTRACTION_BY_IMPORTER = {
    BLENDER_WORKER_ADAPTER_ID: BLENDER_WORKER_ADAPTER_ID,
}
_EXPORT_BY_TARGET = {
    "dts": DTS_ADAPTER_ID,
}


def resolve_region_extraction_provider(*, scene_importer_id: str) -> str:
    try:
        return _REGION_EXTRACTION_BY_IMPORTER[scene_importer_id]
    except KeyError as error:
        raise ValueError(
            "No Region extraction route is registered for scene importer: "
            f"{scene_importer_id}"
        ) from error


def resolve_plant_output_route(
    *,
    scene_importer_id: str,
    target_type: str,
) -> PlantOutputRoute:
    try:
        elevation_provider_id = _ELEVATION_BY_IMPORTER[scene_importer_id]
    except KeyError as error:
        raise ValueError(
            "No elevation route is registered for scene importer: "
            f"{scene_importer_id}"
        ) from error
    try:
        export_provider_id = _EXPORT_BY_TARGET[target_type]
    except KeyError as error:
        raise ValueError(
            f"No export route is registered for target: {target_type}"
        ) from error
    return PlantOutputRoute(
        elevation_provider_id=elevation_provider_id,
        export_provider_id=export_provider_id,
    )
