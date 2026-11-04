"""Target-neutral immutable paths and typed planting input references."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class PlantingInputRef:
    kind: Literal["polygon", "polyline"]
    set_id: str
    selected_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.kind not in {"polygon", "polyline"}
            or not isinstance(self.set_id, str)
            or not self.set_id.strip()
        ):
            raise ValueError("Invalid planting input reference")
        if (
            not isinstance(self.selected_ids, tuple)
            or not self.selected_ids
            or any(
                not isinstance(key, str) or not key.strip() for key in self.selected_ids
            )
            or len(set(self.selected_ids)) != len(self.selected_ids)
        ):
            raise ValueError("Planting input must select unique geometry identifiers")


@dataclass(frozen=True, slots=True)
class PathSetInfo:
    path_set_id: str
    scene_revision_id: str
    producer_job_id: str
    input_hash: str
    coordinate_frame_id: str
    geometry_artifact_id: str
    path_count: int
    created_at: str
