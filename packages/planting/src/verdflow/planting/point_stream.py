"""Bounded readers for the existing PointSet v2/v3 exchange documents."""

from __future__ import annotations

from codecs import getincrementaldecoder
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, BinaryIO
import unicodedata

import ijson
from ijson.common import JSONError, ObjectBuilder

from ._sqlite import scratch_database
from .errors import PlantingDocumentError
from .canonical import canonical_json_bytes, canonical_row_bytes
from .validation import validate_point_row


_HEADER_KEYS = frozenset({
    "schema", "version", "strategy", "coordinate_space", "offset_applied",
    "kind", "resolution_policy", "point_count",
})
_ASSIGNMENT_HEADER_KEYS = frozenset({
    "schema", "version", "target_type", "resolver", "random_seed", "point_set_hash",
    "effective_binding_snapshot_hash", "point_count", "semantic_hash",
})
_MAX_VALUE_EVENTS = 128 * 1024
_MAX_VALUE_TEXT = 8 * 1024 * 1024
_MAX_VALUE_DEPTH = 64
_CONTAINERS = {"start_map", "start_array"}
_ENDS = {"end_map", "end_array"}
_READ_SIZE = 64 * 1024
_LARGE_VALUE_READ_SIZE = 256 * 1024
_MAX_DECODE_WINDOW = 2 * _READ_SIZE + _LARGE_VALUE_READ_SIZE
_WHITESPACE = re.compile(r"[ \t\r\n]*")
_ID_MEMORY_COUNT = 16 * 1024
_ID_MEMORY_CHARS = 1024 * 1024


class _UniqueMap(dict):
    def __setitem__(self, key: str, value: Any) -> None:
        if key in self:
            raise PlantingDocumentError("PointSet object contains a duplicate field")
        super().__setitem__(key, value)


class _UseEventReader(Exception):
    """A value did not fit the fast reader's fixed decoding window."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise PlantingDocumentError("PointSet object contains a duplicate field")
    return value


def _invalid_constant(value: str) -> None:
    raise PlantingDocumentError(f"PointSet contains an invalid JSON constant: {value}")


class _PointText:
    """Use a small decode window, with one bounded expansion for large values."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.utf8 = getincrementaldecoder("utf-8")()
        self.decoder = json.JSONDecoder(
            object_pairs_hook=_unique_object, parse_constant=_invalid_constant,
        )
        self.text = ""
        self.pos = 0
        self.eof = False
        self.last_value_weight = 0

    def _refill(self, size: int = _READ_SIZE) -> bool:
        size = min(size, _MAX_DECODE_WINDOW - (len(self.text) - self.pos))
        if size <= 0:
            return False
        chunk = self.stream.read(size)
        self.eof = not chunk
        self.text = self.text[self.pos:] + self.utf8.decode(chunk, final=self.eof)
        self.pos = 0
        return True

    def peek(self) -> str:
        while True:
            if self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
                self.pos = _WHITESPACE.match(self.text, self.pos).end()
            if self.pos < len(self.text) or self.eof:
                return self.text[self.pos:self.pos + 1]
            self._refill()

    def next_row_token(self) -> str:
        # Both compact and legacy spaced artifacts usually have the next
        # object in this buffer. Other formatting uses the normal parser.
        if self.text.startswith(",{", self.pos):
            self.pos += 1
            return "{"
        if self.text.startswith(", {", self.pos):
            self.pos += 2
            return "{"
        token = self.peek()
        if token == ",":
            self.pos += 1
            token = self.peek()
            if token == "]":
                raise PlantingDocumentError("PointSet is not valid UTF-8 JSON")
            return token
        if token != "]":
            raise PlantingDocumentError("PointSet is not valid UTF-8 JSON")
        return "]"

    def take(self, token: str) -> bool:
        if self.peek() != token:
            return False
        self.pos += 1
        return True

    def expect(self, token: str) -> None:
        if not self.take(token):
            raise PlantingDocumentError("PointSet is not valid UTF-8 JSON")

    def value(self) -> Any:
        return self.value_at(self.peek())

    def value_at(self, first: str) -> Any:
        for attempt in range(3):
            try:
                value, end = self.decoder.raw_decode(self.text, self.pos)
            except PlantingDocumentError:
                raise
            except (ValueError, RecursionError) as error:
                if not self.eof:
                    if attempt == 0 and self._refill():
                        continue
                    # Avoid forcing the rest of the file through the event reader
                    # when only this value needs a wider window.
                    if attempt == 1 and first in ('{', '[', '"') and self._refill(_LARGE_VALUE_READ_SIZE):
                        continue
                    raise _UseEventReader from error
                raise PlantingDocumentError("PointSet is not valid UTF-8 JSON") from error
            # A top-level number can appear complete at a chunk boundary even
            # when more digits (or an exponent) are in the next chunk.
            if first and first in "-0123456789" and (
                end == len(self.text) or self.text[end] in ".eE+-0123456789"
            ):
                if not self.eof and attempt == 0 and self._refill():
                    continue
                raise _UseEventReader
            raw = self.text[self.pos:end]
            if (
                len(raw) >= _MAX_VALUE_EVENTS
                or raw.count("{") + raw.count("[") > _MAX_VALUE_DEPTH
                or "\\u" in raw
            ):
                # The value already fits in this buffer. Check only this value,
                # instead of reparsing the document and slowing down later rows.
                events = iter(ijson.basic_parse(BytesIO(raw.encode("utf-8"))))
                value, self.last_value_weight = _value(events, _next_event(events))
            else:
                self.last_value_weight = len(raw) * 32
            self.pos = end
            return value
        raise AssertionError("unreachable")


class _RowDocumentReader:
    """Yield rows in document order; the verified header is available at EOF."""

    array_key: str
    header_keys: frozenset[str]
    label: str

    def __init__(self, stream: BinaryIO, *, expected_point_count: int | None = None) -> None:
        self._stream = stream
        self._expected_count = expected_point_count
        self._header: dict[str, Any] | None = None
        self._leading_header: dict[str, Any] | None = None
        self._started = False
        self._last_row_weight = 0

    @property
    def header(self) -> dict[str, Any]:
        if self._header is None:
            raise RuntimeError(f"{self.label} header is only available after all rows have been read")
        return dict(self._header)

    @property
    def leading_header(self) -> dict[str, Any] | None:
        """Provisional header when the producer wrote all metadata before points."""
        return dict(self._leading_header) if self._leading_header is not None else None

    def rows(self) -> Iterator[dict[str, Any]]:
        if self._started:
            raise RuntimeError(f"{self.label} rows have already been consumed")
        self._started = True
        try:
            if self._stream.seekable():
                start = self._stream.tell()
                emitted = 0
                try:
                    for row in self._read_fast():
                        emitted += 1
                        yield row
                    return
                except _UseEventReader:
                    self._stream.seek(start)
                    self._leading_header = None
                rows = self._read_events()
                for _ in range(emitted):
                    next(rows)
                yield from rows
            else:
                yield from self._read_events()
        except (JSONError, UnicodeError, OverflowError) as error:
            raise PlantingDocumentError(f"{self.label} is not valid UTF-8 JSON") from error

    def _read_fast(self) -> Iterator[dict[str, Any]]:
        source = _PointText(self._stream)
        source.expect("{")
        header: dict[str, Any] = {}
        seen: set[str] = set()
        count = 0
        if not source.take("}"):
            while True:
                if source.peek() != '"':
                    raise PlantingDocumentError("PointSet is not valid UTF-8 JSON")
                key = source.value()
                self._check_key(key, seen)
                source.expect(":")
                if key == self.array_key:
                    source.expect("[")
                    self._note_leading_header(header)
                    token = source.peek()
                    if token != "]":
                        while True:
                            if token != "{":
                                raise PlantingDocumentError(f"{self.label} row must be an object")
                            count += 1
                            self._check_count(count)
                            row = source.value_at(token)
                            self._last_row_weight = source.last_value_weight
                            yield row
                            token = source.next_row_token()
                            if token == "]":
                                break
                    source.pos += 1
                else:
                    header[key] = source.value()
                if source.take("}"):
                    break
                source.expect(",")
        if source.peek():
            raise PlantingDocumentError(f"{self.label} contains trailing data")
        self._finish(header, seen, count)

    def _read_events(self) -> Iterator[dict[str, Any]]:
        events = iter(ijson.basic_parse(self._stream))
        if _next_event(events)[0] != "start_map":
            raise PlantingDocumentError(f"{self.label} must be a JSON object")
        header: dict[str, Any] = {}
        seen: set[str] = set()
        count = 0
        while True:
            event, key = _next_event(events)
            if event == "end_map":
                break
            if event != "map_key":
                raise PlantingDocumentError(f"{self.label} contains an unsupported field")
            self._check_key(key, seen)
            first = _next_event(events)
            if key != self.array_key:
                header[key], _ = _value(events, first)
                continue
            if first[0] != "start_array":
                raise PlantingDocumentError(f"{self.label} {self.array_key} must be an array")
            self._note_leading_header(header)
            while (item := _next_event(events))[0] != "end_array":
                if item[0] != "start_map":
                    raise PlantingDocumentError(f"{self.label} row must be an object")
                count += 1
                self._check_count(count)
                row, self._last_row_weight = _value(events, item)
                yield row
        # Advance the parser once more so it rejects trailing data.
        if next(events, None) is not None:
            raise PlantingDocumentError(f"{self.label} contains trailing data")
        self._finish(header, seen, count)

    def _check_key(self, key: str, seen: set[str]) -> None:
        if key != self.array_key and key not in self.header_keys:
            raise PlantingDocumentError(f"{self.label} contains an unsupported field")
        if key in seen:
            raise PlantingDocumentError(f"{self.label} contains a duplicate field")
        seen.add(key)

    def _check_count(self, count: int) -> None:
        if self._expected_count is not None and count > self._expected_count:
            raise PlantingDocumentError(f"{self.label} count differs from its Core record")

    def _note_leading_header(self, header: dict[str, Any]) -> None:
        if header.keys() == self.header_keys:
            self._leading_header = dict(header)

    def _finish(self, header: dict[str, Any], seen: set[str], count: int) -> None:
        if header.keys() != self.header_keys:
            raise PlantingDocumentError(f"{self.label} header is incomplete")
        declared = header.get("point_count")
        if self.array_key not in seen or type(declared) is not int or declared != count:
            raise PlantingDocumentError(f"{self.label} point_count does not match {self.array_key} length")
        if self._expected_count is not None and count != self._expected_count:
            raise PlantingDocumentError(f"{self.label} count differs from its Core record")
        self._header = header


class PointSetReader(_RowDocumentReader):
    """Read an existing v2/v3 PointSet in bounded space."""

    array_key = "points"
    header_keys = _HEADER_KEYS
    label = "PointSet"

    def batches(self, *, max_rows: int, max_weight: int) -> Iterator[list[dict[str, Any]]]:
        """Bound both row count and the estimated decoded size of a batch."""
        if max_rows <= 0 or max_weight <= 0:
            raise ValueError("PointSet batch limits must be positive")
        batch: list[dict[str, Any]] = []
        weight = 0
        for row in self.rows():
            row_weight = self._last_row_weight
            if batch and weight + row_weight > max_weight:
                yield batch
                batch, weight = [], 0
            batch.append(row)
            weight += row_weight
            if len(batch) >= max_rows or weight >= max_weight:
                yield batch
                batch, weight = [], 0
        if batch:
            yield batch


class AssignmentSetReader(_RowDocumentReader):
    """Read a v1 AssignmentSet in bounded space."""

    array_key = "assignments"
    header_keys = _ASSIGNMENT_HEADER_KEYS
    label = "AssignmentSet"


def _next_event(events: Iterator[tuple[str, Any]]) -> tuple[str, Any]:
    event = next(events, None)
    if event is None:
        raise PlantingDocumentError("PointSet ended before its JSON document was complete")
    return event


def _value(events: Iterator[tuple[str, Any]], first: tuple[str, Any]) -> tuple[Any, int]:
    builder = ObjectBuilder(map_type=_UniqueMap)
    depth = 0
    text = 0
    event, value = first
    for index in range(_MAX_VALUE_EVENTS):
        if event in _CONTAINERS:
            depth += 1
        elif event in _ENDS:
            depth -= 1
        if isinstance(value, str):
            text += len(value)
        elif isinstance(value, Decimal):
            # Match json.load's float representation for existing coordinates and hashes.
            value = float(value)
        if depth > _MAX_VALUE_DEPTH or text > _MAX_VALUE_TEXT:
            break
        builder.event(event, value)
        if not depth:
            return builder.value, text * 4 + (index + 1) * 96
        event, value = _next_event(events)
    raise PlantingDocumentError("PointSet row or header field exceeds the per-value decoding budget")


def validated_point_rows(
    rows: Iterable[dict[str, Any]], *, version: str, kind: str, scratch: Path,
    on_batch: Callable[[], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Validate rows and IDs; spill only when the bounded ID set outgrows memory."""
    ids: set[str] = set()
    memory_chars = 0
    index: sqlite3.Connection | None = None
    pending: list[tuple[str]] = []
    pending_chars = 0
    try:
        with ExitStack() as resources:
            for ordinal, point in enumerate(rows):
                validate_point_row(point, version=version, kind=kind, index=ordinal)
                key = unicodedata.normalize("NFC", point["id"])
                if index is None and (len(ids) >= _ID_MEMORY_COUNT or memory_chars + len(key) > _ID_MEMORY_CHARS):
                    index = resources.enter_context(scratch_database(scratch))
                    index.execute("PRAGMA cache_size = -2048")
                    # The caller owns this disposable index, not the project database.
                    index.execute("PRAGMA journal_mode = OFF")
                    index.execute("PRAGMA synchronous = OFF")
                    index.execute("CREATE TABLE point_ids (id TEXT PRIMARY KEY) WITHOUT ROWID")
                    index.executemany("INSERT INTO point_ids VALUES (?)", ((item,) for item in ids))
                    ids.clear()
                if index is None:
                    if key in ids:
                        raise PlantingDocumentError("PointSet points contain a duplicate id")
                    ids.add(key)
                    memory_chars += len(key)
                else:
                    pending.append((key,))
                    pending_chars += len(key)
                    if len(pending) >= 512 or pending_chars >= 256 * 1024:
                        index.executemany("INSERT INTO point_ids VALUES (?)", pending)
                        pending.clear()
                        pending_chars = 0
                if on_batch is not None and ordinal % 512 == 0:
                    on_batch()
                yield point
            if index is not None and pending:
                index.executemany("INSERT INTO point_ids VALUES (?)", pending)
            if on_batch is not None:
                on_batch()
    except sqlite3.IntegrityError as error:
        raise PlantingDocumentError("PointSet points contain a duplicate id") from error
    except sqlite3.Error as error:
        raise PlantingDocumentError(f"PointSet ID verification could not write its temporary index: {error}") from error


def encoded_point_rows(rows: Iterable[dict[str, Any]]) -> Iterator[bytes]:
    """Produce the canonical array body, without enclosing brackets."""
    for ordinal, point in enumerate(rows):
        yield (b"," if ordinal else b"") + canonical_row_bytes(point)


def canonical_point_document(
    header: dict[str, Any], rows: Iterable[bytes],
) -> Iterator[bytes]:
    """Combine verified header and canonical array body into the existing format."""
    yield b"{"
    for ordinal, key in enumerate(sorted((*header, "points"))):
        if ordinal:
            yield b","
        yield canonical_json_bytes(key) + b":"
        if key == "points":
            yield b"["
            yield from rows
            yield b"]"
        else:
            yield canonical_json_bytes(header[key])
    yield b"}"
