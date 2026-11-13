"""Keep the coordinates of one oversized Region out of Python object trees."""

from __future__ import annotations

from itertools import chain
from typing import Any, Callable, Iterator, TextIO

from ijson.common import ObjectBuilder

from .region_import import RegionEvent, RegionSetImportError, _read_value


_BOUNDARY_PREFIX_EVENTS = 32 * 1024
_WRITE_TOKENS = 4096
_COPY_CHARACTERS = 256 * 1024


class RegionBoundarySpool:
    """The fragment remains valid only until the next boundary is read."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def read(self, events: Iterator[RegionEvent], first: RegionEvent) -> Any:
        if first[0] != "start_array":
            return _read_value(events, first, keep=True)
        builder = ObjectBuilder()
        builder.event(*first)
        depth = 1
        for _ in range(_BOUNDARY_PREFIX_EVENTS - 1):
            event, value = next(events)
            builder.event(event, value)
            if event in ("start_array", "start_map"):
                depth += 1
            elif event in ("end_array", "end_map"):
                depth -= 1
                if depth == 0:
                    return builder.value
        if depth > 3:
            raise RegionSetImportError("候选区域坐标数组层级无效，未确认区域。")
        self._write_array(chain(_array_prefix(builder.value, depth), events))
        return self

    def _write_array(self, events: Iterator[RegionEvent]) -> None:
        output = self._stream
        output.seek(0)
        output.truncate()
        # The native JSON parser verifies syntax; only numbers and arrays are
        # valid in the three levels of an exact Region boundary.
        separators: list[bool] = []
        tokens: list[str] = []
        for event, value in events:
            if event == "end_array":
                tokens.append("]")
                separators.pop()
                if not separators:
                    output.write("".join(tokens))
                    return
            elif event == "start_array" or (event == "number" and separators):
                if separators:
                    if separators[-1]:
                        tokens.append(", ")
                    separators[-1] = True
                if event == "start_array":
                    if len(separators) == 3:
                        raise RegionSetImportError("候选区域坐标数组层级无效，未确认区域。")
                    separators.append(False)
                    tokens.append("[")
                else:
                    tokens.append(str(value))
            else:
                raise RegionSetImportError("候选区域边界包含非数字坐标，未确认区域。")
            if len(tokens) >= _WRITE_TOKENS:
                output.write("".join(tokens))
                tokens.clear()
        raise StopIteration

    def copy_to(self, output: TextIO, check_progress: Callable[[], None] | None) -> None:
        self._stream.seek(0)
        while chunk := self._stream.read(_COPY_CHARACTERS):
            output.write(chunk)
            if check_progress is not None:
                check_progress()


def _array_prefix(value: Any, open_arrays: int) -> Iterator[RegionEvent]:
    """Replay the bounded decoded prefix, leaving its last open branch open."""
    if isinstance(value, list):
        yield "start_array", None
        last = len(value) - 1
        for index, item in enumerate(value):
            yield from _array_prefix(item, max(0, open_arrays - 1) if index == last else 0)
        if open_arrays == 0:
            yield "end_array", None
    elif type(value) in (int, float):
        yield "number", value
    else:
        raise RegionSetImportError("候选区域边界包含非数字坐标，未确认区域。")
