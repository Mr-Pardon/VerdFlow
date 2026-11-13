"""Attempt-private PointSet framing shared by the host and Blender."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import json
from typing import Any, BinaryIO, Callable, TextIO


POINT_ROWS_ENCODING = "verdflow.point-rows.ndjson.v1"


def stage_point_rows(
    stream: TextIO, rows: Iterable[dict[str, Any]], check_progress: Callable[[], None],
) -> None:
    encode = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode
    written = 0
    for ordinal, row in enumerate(rows):
        if ordinal % 256 == 0 or written >= 1024**2:
            check_progress()
            written = 0
        line = encode(row) + "\n"
        stream.write(line)
        written += len(line)
    check_progress()


def read_point_rows(stream: BinaryIO, point_count: int) -> Iterator[dict[str, Any]]:
    for ordinal in range(point_count):
        row = stream.readline()
        if not row.endswith(b"\n"):
            raise ValueError(f"Staged PointSet ended before row {ordinal + 1}")
        document = json.loads(row)
        if not isinstance(document, dict):
            raise ValueError(f"Staged PointSet row {ordinal + 1} must be an object")
        yield document
    if stream.read(1):
        raise ValueError("Staged PointSet contains rows beyond its frozen count")


class SampledPointWriter:
    """Retain the legacy v2 JSON key order, separators and numeric encoding."""

    def __init__(self, stream: TextIO, header: dict[str, Any]) -> None:
        self._stream = stream
        self._encode = json.JSONEncoder(ensure_ascii=False, sort_keys=True).encode
        self._suffix = self._encode({key: item for key, item in header.items() if key > "points"})[1:]
        leading = self._encode({key: item for key, item in header.items() if key < "points"})
        self._stream.write(leading[:-1] + ', "points": [')
        self._separator = ""

    def write_point(self, point: dict[str, Any]) -> None:
        self._stream.write(self._separator + self._encode(point))
        self._separator = ", "

    def finish(self) -> None:
        self._stream.write("], " + self._suffix)
