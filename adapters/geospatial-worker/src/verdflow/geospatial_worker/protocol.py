"""Stable command identities and local runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from struct import Struct
from typing import Literal

from .errors import GeospatialDataError


GEOSPATIAL_ADAPTER_ID = "verdflow.geospatial"
GEOSPATIAL_PREPARE_REGIONS_COMMAND = "verdflow.geospatial.prepare-regions"
IMAGERY_REGION_PROVIDER_ID = "verdflow.geospatial.imagery"
IMAGERY_REGION_RECIPE_ID = "verdflow.imagery-regions"
IMAGERY_PREPARE_REGIONS_COMMAND = "verdflow.geospatial.prepare-imagery-regions"
IMAGERY_MODEL_PROFILE = "satlens-segformer-b0/1"
GEOSPATIAL_SAMPLE_ELEVATION_COMMAND = "verdflow.geospatial.sample-elevation"
GEOSPATIAL_SUPPORTED_COMMANDS = (
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
)
GEOSPATIAL_PROTOCOL_VERSION = "1"
IMAGERY_TILE_INLINE_GEOMETRY_BYTES = 32 * 1024**2
IMAGERY_TILE_RESPONSE_BYTES = 3_000_000
IMAGERY_REVIEW_ORDINAL_LIMIT = 1 << 24
IMAGERY_REVIEW_INDEX_ENCODING = "ordinal-f64-u32-le/1"
IMAGERY_REVIEW_INDEX_ROW = Struct("<dI")
IMAGERY_REVIEW_INDEX_INLINE_ROWS = 32_768


@dataclass(frozen=True, slots=True)
class ImageryRegionOptions:
    """Versioned inputs for the fixed RGB model profile."""

    imagery_member_id: str
    rgb_bands: tuple[int, int, int] = (1, 2, 3)
    backend: Literal["cpu", "directml"] = "cpu"
    device_id: int = 0
    elevation_member_id: str | None = None
    min_region_pixels: int = 0

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "profile": IMAGERY_MODEL_PROFILE,
            "imagery_member_id": self.imagery_member_id,
            "rgb_bands": list(self.rgb_bands),
            "backend": self.backend,
            "device_id": self.device_id,
        }
        if self.elevation_member_id is not None:
            document["elevation_member_id"] = self.elevation_member_id
        if self.min_region_pixels:
            document["min_region_pixels"] = self.min_region_pixels
        return document

    @classmethod
    def from_document(cls, document: object) -> ImageryRegionOptions:
        if isinstance(document, dict) and set(document) - {"elevation_member_id", "min_region_pixels"} == {
            "profile", "imagery_member_id", "rgb_bands", "backend", "device_id",
        }:
            member = document["imagery_member_id"]
            bands = document["rgb_bands"]
            backend = document["backend"]
            device = document["device_id"]
            elevation = document.get("elevation_member_id")
            minimum = document.get("min_region_pixels", 0)
            if (document["profile"] == IMAGERY_MODEL_PROFILE
                    and isinstance(member, str) and member.strip()
                    and isinstance(bands, list) and len(bands) == 3
                    and all(type(band) is int and band > 0 for band in bands)
                    and len(set(bands)) == 3 and backend in ("cpu", "directml")
                    and type(device) is int and 0 <= device <= 15
                    and (backend == "directml" or device == 0)
                    and type(minimum) is int and 0 <= minimum <= (1 << 53) - 1
                    and ("elevation_member_id" not in document or (
                        isinstance(elevation, str) and elevation.strip() and elevation != member
                    ))):
                return cls(member, (bands[0], bands[1], bands[2]), backend, device, elevation, minimum)
        raise GeospatialDataError("imagery_options_invalid", "影像识别配置不受支持，请检查影像来源、RGB 波段、推理设备和最小区域像素数。")


@dataclass(frozen=True, slots=True)
class GeospatialWorkerConfig:
    runtime_root: Path
    cache_root: Path
    python_executable: Path = Path(sys.executable)
    timeout_seconds: float = 300.0
    heartbeat_interval_seconds: float = 2.0
    poll_interval_seconds: float = 0.1
    termination_grace_seconds: float = 3.0
    imagery_model_path: Path | None = None
    imagery_python_executable: Path | None = None
    enable_imagery: bool = False

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.heartbeat_interval_seconds >= self.timeout_seconds:
            raise ValueError("heartbeat interval must be below timeout")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.termination_grace_seconds <= 0:
            raise ValueError("termination_grace_seconds must be positive")
