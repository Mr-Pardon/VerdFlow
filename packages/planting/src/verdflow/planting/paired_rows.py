"""Bounded joins for already accepted PointSet and AssignmentSet artifacts."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO

from ._sqlite import scratch_database
from .errors import PlantingDocumentError
from .point_stream import AssignmentSetReader, PointSetReader
from .validation import validate_assignment_header, validate_point_header


def paired_planting_rows(
    points_file: BinaryIO,
    assignments_file: BinaryIO,
    *,
    point_count: int,
    point_kind: str,
    assignment_hash: str,
    target_type: str,
    scratch_root: Path,
    on_rows: Callable[[], None] | None = None,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield frozen assignments in point order, verifying both documents at EOF."""
    point_start = points_file.tell()
    assignment_start = assignments_file.tell()
    points = PointSetReader(points_file, expected_point_count=point_count)
    assignments = AssignmentSetReader(assignments_file, expected_point_count=point_count)
    emitted = 0
    for point, assignment in zip(points.rows(), assignments.rows(), strict=True):
        if _identifier(point, "id") != _identifier(assignment, "point_id"):
            break
        _check_role(point, assignment)
        yield point, assignment
        emitted += 1
        if on_rows is not None and emitted % 2048 == 0:
            on_rows()
    else:
        _check_headers(points, assignments, point_kind, assignment_hash, target_type)
        return

    # Historical assignments need not have been written in point order.
    points_file.seek(point_start)
    assignments_file.seek(assignment_start)
    points = PointSetReader(points_file, expected_point_count=point_count)
    assignments = AssignmentSetReader(assignments_file, expected_point_count=point_count)
    scratch_root.mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix="assignment-join-", dir=scratch_root) as scratch:
            with scratch_database(Path(scratch) / "index.sqlite") as index:
                index.execute("PRAGMA cache_size = -2048")
                index.execute("PRAGMA journal_mode = OFF")
                index.execute("PRAGMA synchronous = OFF")
                index.execute("CREATE TABLE assignments (id TEXT PRIMARY KEY, row TEXT NOT NULL) WITHOUT ROWID")
                for ordinal, assignment in enumerate(assignments.rows(), start=1):
                    index.execute(
                        "INSERT INTO assignments VALUES (?, ?)",
                        (_identifier(assignment, "point_id"), json.dumps(assignment, ensure_ascii=False, allow_nan=False)),
                    )
                    if on_rows is not None and ordinal % 2048 == 0:
                        on_rows()
                for ordinal, point in enumerate(points.rows(), start=1):
                    key = (_identifier(point, "id"),)
                    row = index.execute("SELECT row FROM assignments WHERE id = ?", key).fetchone()
                    if row is None:
                        raise PlantingDocumentError("AssignmentSet point identities differ from PointSet")
                    index.execute("DELETE FROM assignments WHERE id = ?", key)
                    assignment = json.loads(row[0])
                    _check_role(point, assignment)
                    if ordinal > emitted:
                        yield point, assignment
                    if on_rows is not None and ordinal % 2048 == 0:
                        on_rows()
    except sqlite3.IntegrityError as error:
        raise PlantingDocumentError("AssignmentSet contains a duplicate point identifier") from error
    except sqlite3.Error as error:
        raise PlantingDocumentError("Could not build temporary index for unordered assignments") from error
    _check_headers(points, assignments, point_kind, assignment_hash, target_type)


def output_row_selected(
    point: dict[str, Any],
    assignment: dict[str, Any],
    selected: frozenset[str] | None,
    *,
    require_terrain_hit: bool,
) -> bool:
    return (selected is None or assignment["asset_id"] in selected) and (
        not require_terrain_hit
        or (isinstance(sample := point.get("terrain_sample"), dict) and sample.get("hit") is True)
    )


def _identifier(row: dict[str, Any], field: str) -> str:
    identifier = row.get(field)
    if not isinstance(identifier, str) or not identifier:
        raise PlantingDocumentError("Persisted planting row has no point identifier")
    return identifier


def _check_role(point: dict[str, Any], assignment: dict[str, Any]) -> None:
    if point["role_key"] != assignment.get("role_key"):
        raise PlantingDocumentError("AssignmentSet role differs from PointSet")


def _check_headers(
    points: PointSetReader, assignments: AssignmentSetReader, point_kind: str, assignment_hash: str, target_type: str,
) -> None:
    point_header = points.header
    assignment_header = assignments.header
    validate_point_header(point_header)
    validate_assignment_header(assignment_header)
    if point_header["kind"] != point_kind:
        raise PlantingDocumentError("PointSet kind differs from its Core record")
    if assignment_header["semantic_hash"] != assignment_hash or assignment_header["target_type"] != target_type:
        raise PlantingDocumentError("AssignmentSet header differs from its Core record")
