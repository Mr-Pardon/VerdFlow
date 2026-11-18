"""Session-owned exact SHP drafts. One sequential file and a small offset index per import."""

from __future__ import annotations

from errno import ENOSPC
from itertools import groupby
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Iterable, Iterator
from uuid import uuid4

from .live import DtsLiveGeometryInputError, DtsLiveRegion, DtsLiveRegionRef


@dataclass(frozen=True, slots=True)
class _Row:
    ref: DtsLiveRegionRef
    start: int
    length: int


class LiveRegionDraftStore:
    def __init__(self, root: Path):
        self.root = root
        self._datasets: dict[str, list[_Row]] = {}

    def register(self, regions: Iterable[DtsLiveRegion]) -> tuple[str, int]:
        self.root.mkdir(parents=True, exist_ok=True)
        dataset = uuid4().hex
        path = self.root / dataset
        rows: list[_Row] = []
        try:
            with path.open("xb") as output:
                next_check = 0
                for i, region in enumerate(regions):
                    ref = DtsLiveRegionRef(region.region_id, region.name, (), region.bbox, (),
                                           dataset, i, len(region.coordinates) + sum(map(len, region.holes)))
                    payload = json.dumps(dict(region_id=ref.region_id, name=ref.name, bbox=ref.bbox,
                                              coordinates=region.coordinates, holes=region.holes,
                                              dataset_id=dataset, ordinal=i, vertex_count=ref.vertex_count),
                                         ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                    start = output.tell()
                    if start + len(payload) >= next_check:
                        if shutil.disk_usage(self.root).free < len(payload) + 32 * 1024**2:
                            raise OSError(ENOSPC, "Insufficient space for SHP preview")
                        next_check = start + len(payload) + 16 * 1024**2
                    output.write(payload)
                    rows.append(_Row(ref, start, len(payload)))
            if not rows:
                raise ValueError("SHP 中没有面要素。")
            self._datasets[dataset] = rows
            return dataset, len(rows)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _row(self, dataset: object, ordinal: object) -> _Row:
        rows = self._datasets.get(dataset) if isinstance(dataset, str) else None
        if not rows or type(ordinal) is not int or not 0 <= ordinal < len(rows):
            raise DtsLiveGeometryInputError("SHP 选区不属于当前会话或已失效，请重新导入。")
        return rows[ordinal]

    def resolve(self, value: object) -> object:
        if not isinstance(value, list):
            return value
        resolved = []
        seen = set()
        for item in value:
            if isinstance(item, dict) and "dataset_id" in item:
                dataset = item["dataset_id"]
                start, count = item.get("start"), item.get("count")
                self._row(dataset, start)
                if type(count) is not int or count < 1:
                    raise DtsLiveGeometryInputError("SHP 选区范围无效。")
                self._row(dataset, start + count - 1)
                for ordinal in range(start, start + count):
                    ref = self._datasets[dataset][ordinal].ref
                    if ref.region_id in seen:
                        raise DtsLiveGeometryInputError("SHP 选区不能重复。")
                    seen.add(ref.region_id)
                    resolved.append(ref)
            else:
                resolved.append(item)
        return resolved

    def page(self, dataset: str, start: int, *, single: bool = False) -> bytes:
        first = self._row(dataset, start)
        rows = self._datasets[dataset]
        end = start + 1
        while not single and end < min(start + 1024, len(rows)):
            last = rows[end]
            if last.start + last.length - first.start > 8 * 1024**2:
                break
            end += 1
        with (self.root / dataset).open("rb") as source:
            source.seek(first.start)
            return b'{"regions":[' + b",".join(source.read(row.length) for row in rows[start:end]) + b"]}"

    def geometries(self, regions: Iterable[DtsLiveRegion]) -> Iterator[DtsLiveRegion]:
        for dataset, items in groupby(regions, lambda r: r.dataset_id if isinstance(r, DtsLiveRegionRef) else None):
            if dataset is None:
                yield from items
                continue
            if dataset not in self._datasets:
                raise DtsLiveGeometryInputError("SHP 选区不属于当前会话。")
            with (self.root / dataset).open("rb") as stream:
                for region in items:
                    row = self._row(dataset, region.ordinal)
                    stream.seek(row.start)
                    doc = json.loads(stream.read(row.length))
                    yield DtsLiveRegion(region.region_id, region.name, tuple(map(tuple, doc["coordinates"])), region.bbox,
                                        tuple(tuple(map(tuple, ring)) for ring in doc["holes"]))
