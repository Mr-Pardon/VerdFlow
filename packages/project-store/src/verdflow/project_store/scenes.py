"""Immutable source, coordinate frame and scene revision repositories."""

from __future__ import annotations

import hashlib
import math
import uuid

from verdflow.contracts import (
    CoordinateFrameInfo,
    CoordinateReferenceKind,
    SceneRevisionInfo,
    SourceAssemblyMemberInfo,
    SourceAssemblyMemberSpec,
    SourceInfo,
    SourceKind,
)

from .database import ProjectDatabase
from .errors import (
    ArtifactNotFoundError,
    CoordinateFrameNotFoundError,
    SceneRevisionConflictError,
    SceneRevisionNotFoundError,
    SourceAssemblyError,
    SourceNotFoundError,
)


MATRIX_LENGTH = 16
AFFINE_LAST_ROW = (0.0, 0.0, 0.0, 1.0)


class SceneRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create_coordinate_frame(
        self,
        *,
        name: str,
        handedness: str,
        up_axis: str,
        linear_unit: str,
        crs_kind: CoordinateReferenceKind,
        crs_definition: str | None,
        local_origin_x: float,
        local_origin_y: float,
        local_origin_z: float,
        created_at: str,
    ) -> CoordinateFrameInfo:
        values = {
            "name": name.strip(),
            "handedness": handedness.strip(),
            "up_axis": up_axis.strip(),
            "linear_unit": linear_unit.strip(),
        }
        missing = [field for field, value in values.items() if not value]
        if missing:
            raise ValueError(f"{missing[0]} is required")
        frame = CoordinateFrameInfo(
            coordinate_frame_id=f"frame_{uuid.uuid4().hex}",
            name=values["name"],
            handedness=values["handedness"],
            up_axis=values["up_axis"],
            linear_unit=values["linear_unit"],
            crs_kind=CoordinateReferenceKind(crs_kind),
            crs_definition=(
                crs_definition.strip() if crs_definition else None
            ),
            local_origin_x=float(local_origin_x),
            local_origin_y=float(local_origin_y),
            local_origin_z=float(local_origin_z),
            created_at=created_at,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO coordinate_frames(
                    coordinate_frame_id, name, handedness, up_axis,
                    linear_unit, crs_kind, crs_definition,
                    local_origin_x, local_origin_y, local_origin_z, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frame.coordinate_frame_id,
                    frame.name,
                    frame.handedness,
                    frame.up_axis,
                    frame.linear_unit,
                    frame.crs_kind.value,
                    frame.crs_definition,
                    frame.local_origin_x,
                    frame.local_origin_y,
                    frame.local_origin_z,
                    frame.created_at,
                ),
            )
        return frame

    def get_coordinate_frame(
        self,
        coordinate_frame_id: str,
    ) -> CoordinateFrameInfo:
        row = self.database.connection.execute(
            """
            SELECT coordinate_frame_id, name, handedness, up_axis,
                   linear_unit, crs_kind, crs_definition,
                   local_origin_x, local_origin_y, local_origin_z, created_at
            FROM coordinate_frames
            WHERE coordinate_frame_id = ?
            """,
            (coordinate_frame_id,),
        ).fetchone()
        if row is None:
            raise CoordinateFrameNotFoundError(coordinate_frame_id)
        return _coordinate_frame_from_row(row)

    def create_source(
        self,
        *,
        kind: SourceKind,
        original_name: str,
        artifact_id: str,
        imported_at: str,
    ) -> SourceInfo:
        normalized_name = original_name.strip()
        if not normalized_name:
            raise ValueError("original_name is required")
        normalized_kind = SourceKind(kind)
        if normalized_kind is SourceKind.ASSEMBLY:
            raise SourceAssemblyError(
                "Assembly Sources require structured member registration"
            )
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT s.source_id, s.kind, s.original_name, s.artifact_id,
                       s.content_hash, s.imported_at
                FROM sources AS s
                WHERE s.kind = ? AND s.artifact_id = ?
                """,
                (normalized_kind.value, artifact_id),
            ).fetchone()
            if existing is not None:
                return _source_from_row(existing)
            artifact = connection.execute(
                "SELECT content_hash FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if artifact is None:
                raise ArtifactNotFoundError(
                    f"Source artifact does not exist: {artifact_id}"
                )
            source = SourceInfo(
                source_id=f"src_{uuid.uuid4().hex}",
                kind=normalized_kind,
                original_name=normalized_name,
                artifact_id=artifact_id,
                content_hash=str(artifact["content_hash"]),
                imported_at=imported_at,
            )
            connection.execute(
                """
                INSERT INTO sources(
                    source_id, kind, original_name, artifact_id,
                    content_hash, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.kind.value,
                    source.original_name,
                    source.artifact_id,
                    source.content_hash,
                    source.imported_at,
                ),
            )
        return source

    def create_source_assembly(
        self,
        *,
        original_name: str,
        artifact_id: str,
        members: tuple[SourceAssemblyMemberSpec, ...],
        imported_at: str,
    ) -> SourceInfo:
        normalized_name = original_name.strip()
        if not normalized_name:
            raise ValueError("original_name is required")
        if not members:
            raise SourceAssemblyError(
                "Source Assembly requires at least one leaf member"
            )
        normalized_members = tuple(
            SourceAssemblyMemberSpec(
                source_id=_required_text(member.source_id, "source_id"),
                source_to_scene_matrix_row_major=normalize_affine_matrix(
                    member.source_to_scene_matrix_row_major
                ),
            )
            for member in members
        )
        with self.database.transaction() as connection:
            artifact = connection.execute(
                "SELECT content_hash FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if artifact is None:
                raise ArtifactNotFoundError(
                    f"Source artifact does not exist: {artifact_id}"
                )
            source_rows = {}
            for member in normalized_members:
                row = connection.execute(
                    """
                    SELECT source_id, kind, content_hash
                    FROM sources
                    WHERE source_id = ?
                    """,
                    (member.source_id,),
                ).fetchone()
                if row is None:
                    raise SourceNotFoundError(member.source_id)
                if SourceKind(row["kind"]) is SourceKind.ASSEMBLY:
                    raise SourceAssemblyError(
                        "Nested Source Assemblies are not supported"
                    )
                source_rows[member.source_id] = row

            existing = connection.execute(
                """
                SELECT source_id, kind, original_name, artifact_id,
                       content_hash, imported_at
                FROM sources
                WHERE kind = ? AND artifact_id = ?
                """,
                (SourceKind.ASSEMBLY.value, artifact_id),
            ).fetchone()
            if existing is not None:
                source = _source_from_row(existing)
                persisted = self._list_source_assembly_members(
                    connection,
                    source.source_id,
                )
                requested = tuple(
                    (
                        member.source_id,
                        member.source_to_scene_matrix_row_major,
                    )
                    for member in normalized_members
                )
                actual = tuple(
                    (
                        member.source_id,
                        member.source_to_scene_matrix_row_major,
                    )
                    for member in persisted
                )
                if actual != requested:
                    raise SourceAssemblyError(
                        "Assembly Artifact is already bound to other members"
                    )
                return source

            source = SourceInfo(
                source_id=f"src_{uuid.uuid4().hex}",
                kind=SourceKind.ASSEMBLY,
                original_name=normalized_name,
                artifact_id=artifact_id,
                content_hash=str(artifact["content_hash"]),
                imported_at=imported_at,
            )
            connection.execute(
                """
                INSERT INTO sources(
                    source_id, kind, original_name, artifact_id,
                    content_hash, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.kind.value,
                    source.original_name,
                    source.artifact_id,
                    source.content_hash,
                    source.imported_at,
                ),
            )
            for ordinal, member in enumerate(normalized_members):
                source_member_id = _source_member_id(
                    source.content_hash,
                    ordinal,
                )
                connection.execute(
                    """
                    INSERT INTO source_assembly_members(
                        source_member_id, assembly_source_id, source_id,
                        ordinal,
                        m00, m01, m02, m03,
                        m10, m11, m12, m13,
                        m20, m21, m22, m23,
                        m30, m31, m32, m33
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        source_member_id,
                        source.source_id,
                        member.source_id,
                        ordinal,
                        *member.source_to_scene_matrix_row_major,
                    ),
                )
        return source

    def get_source(self, source_id: str) -> SourceInfo:
        row = self.database.connection.execute(
            """
            SELECT source_id, kind, original_name, artifact_id,
                   content_hash, imported_at
            FROM sources
            WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            raise SourceNotFoundError(source_id)
        return _source_from_row(row)

    def list_source_assembly_members(
        self,
        source_id: str,
    ) -> tuple[SourceAssemblyMemberInfo, ...]:
        source = self.get_source(source_id)
        if source.kind is not SourceKind.ASSEMBLY:
            return ()
        return self._list_source_assembly_members(
            self.database.connection,
            source_id,
        )

    @staticmethod
    def _list_source_assembly_members(
        connection: object,
        source_id: str,
    ) -> tuple[SourceAssemblyMemberInfo, ...]:
        rows = connection.execute(
            """
            SELECT m.source_member_id, m.assembly_source_id, m.source_id,
                   m.ordinal,
                   m.m00, m.m01, m.m02, m.m03,
                   m.m10, m.m11, m.m12, m.m13,
                   m.m20, m.m21, m.m22, m.m23,
                   m.m30, m.m31, m.m32, m.m33,
                   s.content_hash AS source_content_hash
            FROM source_assembly_members AS m
            JOIN sources AS s ON s.source_id = m.source_id
            WHERE m.assembly_source_id = ?
            ORDER BY m.ordinal
            """,
            (source_id,),
        ).fetchall()
        return tuple(_source_assembly_member_from_row(row) for row in rows)

    def create_scene_revision(
        self,
        *,
        source_id: str,
        parent_revision_id: str | None,
        importer_id: str,
        importer_version: str,
        coordinate_frame_id: str,
        scene_manifest_artifact_id: str,
        created_at: str,
        activate: bool,
    ) -> SceneRevisionInfo:
        normalized_importer = importer_id.strip()
        normalized_version = importer_version.strip()
        if not normalized_importer:
            raise ValueError("importer_id is required")
        if not normalized_version:
            raise ValueError("importer_version is required")
        revision = SceneRevisionInfo(
            scene_revision_id=f"scene_{uuid.uuid4().hex}",
            source_id=source_id,
            parent_revision_id=parent_revision_id,
            importer_id=normalized_importer,
            importer_version=normalized_version,
            coordinate_frame_id=coordinate_frame_id,
            scene_manifest_artifact_id=scene_manifest_artifact_id,
            created_at=created_at,
        )
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone() is None:
                raise SourceNotFoundError(source_id)
            if connection.execute(
                """
                SELECT 1 FROM coordinate_frames
                WHERE coordinate_frame_id = ?
                """,
                (coordinate_frame_id,),
            ).fetchone() is None:
                raise CoordinateFrameNotFoundError(coordinate_frame_id)
            if connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                (scene_manifest_artifact_id,),
            ).fetchone() is None:
                raise ArtifactNotFoundError(
                    "Scene manifest artifact does not exist: "
                    f"{scene_manifest_artifact_id}"
                )
            if parent_revision_id is not None:
                parent = connection.execute(
                    """
                    SELECT source_id FROM scene_revisions
                    WHERE scene_revision_id = ?
                    """,
                    (parent_revision_id,),
                ).fetchone()
                if parent is None:
                    raise SceneRevisionNotFoundError(parent_revision_id)
                if parent["source_id"] != source_id:
                    raise SceneRevisionConflictError(
                        "Parent revision belongs to another source"
                    )
            connection.execute(
                """
                INSERT INTO scene_revisions(
                    scene_revision_id, source_id, parent_revision_id,
                    importer_id, importer_version, coordinate_frame_id,
                    scene_manifest_artifact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision.scene_revision_id,
                    revision.source_id,
                    revision.parent_revision_id,
                    revision.importer_id,
                    revision.importer_version,
                    revision.coordinate_frame_id,
                    revision.scene_manifest_artifact_id,
                    revision.created_at,
                ),
            )
            if activate:
                connection.execute(
                    """
                    UPDATE projects
                    SET active_scene_revision_id = ?, updated_at = ?
                    """,
                    (revision.scene_revision_id, created_at),
                )
        return revision

    def get_scene_revision(
        self,
        scene_revision_id: str,
    ) -> SceneRevisionInfo:
        row = self.database.connection.execute(
            """
            SELECT scene_revision_id, source_id, parent_revision_id,
                   importer_id, importer_version, coordinate_frame_id,
                   scene_manifest_artifact_id, created_at
            FROM scene_revisions
            WHERE scene_revision_id = ?
            """,
            (scene_revision_id,),
        ).fetchone()
        if row is None:
            raise SceneRevisionNotFoundError(scene_revision_id)
        return _scene_revision_from_row(row)

    def list_scene_revisions(self) -> tuple[SceneRevisionInfo, ...]:
        rows = self.database.connection.execute(
            """
            SELECT scene_revision_id, source_id, parent_revision_id,
                   importer_id, importer_version, coordinate_frame_id,
                   scene_manifest_artifact_id, created_at
            FROM scene_revisions
            ORDER BY created_at, scene_revision_id
            """
        ).fetchall()
        return tuple(_scene_revision_from_row(row) for row in rows)

    def find_scene_revision_by_manifest(
        self,
        scene_manifest_artifact_id: str,
    ) -> SceneRevisionInfo | None:
        row = self.database.connection.execute(
            """
            SELECT scene_revision_id, source_id, parent_revision_id,
                   importer_id, importer_version, coordinate_frame_id,
                   scene_manifest_artifact_id, created_at
            FROM scene_revisions
            WHERE scene_manifest_artifact_id = ?
            ORDER BY created_at
            LIMIT 1
            """,
            (scene_manifest_artifact_id,),
        ).fetchone()
        return _scene_revision_from_row(row) if row is not None else None


def _coordinate_frame_from_row(row: object) -> CoordinateFrameInfo:
    return CoordinateFrameInfo(
        coordinate_frame_id=row["coordinate_frame_id"],
        name=row["name"],
        handedness=row["handedness"],
        up_axis=row["up_axis"],
        linear_unit=row["linear_unit"],
        crs_kind=CoordinateReferenceKind(row["crs_kind"]),
        crs_definition=row["crs_definition"],
        local_origin_x=float(row["local_origin_x"]),
        local_origin_y=float(row["local_origin_y"]),
        local_origin_z=float(row["local_origin_z"]),
        created_at=row["created_at"],
    )


def _source_from_row(row: object) -> SourceInfo:
    return SourceInfo(
        source_id=row["source_id"],
        kind=SourceKind(row["kind"]),
        original_name=row["original_name"],
        artifact_id=row["artifact_id"],
        content_hash=row["content_hash"],
        imported_at=row["imported_at"],
    )


def _scene_revision_from_row(row: object) -> SceneRevisionInfo:
    return SceneRevisionInfo(
        scene_revision_id=row["scene_revision_id"],
        source_id=row["source_id"],
        parent_revision_id=row["parent_revision_id"],
        importer_id=row["importer_id"],
        importer_version=row["importer_version"],
        coordinate_frame_id=row["coordinate_frame_id"],
        scene_manifest_artifact_id=row["scene_manifest_artifact_id"],
        created_at=row["created_at"],
    )


def normalize_affine_matrix(value: object) -> tuple[float, ...]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != MATRIX_LENGTH
        or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    ):
        raise SourceAssemblyError(
            "source_to_scene_matrix_row_major must contain 16 finite numbers"
        )
    matrix = tuple(float(item) for item in value)
    if not all(
        math.isclose(matrix[12 + index], expected, abs_tol=1e-12)
        for index, expected in enumerate(AFFINE_LAST_ROW)
    ):
        raise SourceAssemblyError(
            "source_to_scene_matrix_row_major must be affine"
        )
    determinant = (
        matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9])
        - matrix[1] * (matrix[4] * matrix[10] - matrix[6] * matrix[8])
        + matrix[2] * (matrix[4] * matrix[9] - matrix[5] * matrix[8])
    )
    if abs(determinant) <= 1e-12:
        raise SourceAssemblyError(
            "source_to_scene_matrix_row_major must be invertible"
        )
    return matrix


def _source_assembly_member_from_row(
    row: object,
) -> SourceAssemblyMemberInfo:
    matrix = tuple(
        float(row[f"m{row_index}{column_index}"])
        for row_index in range(4)
        for column_index in range(4)
    )
    return SourceAssemblyMemberInfo(
        source_member_id=row["source_member_id"],
        assembly_source_id=row["assembly_source_id"],
        source_id=row["source_id"],
        ordinal=int(row["ordinal"]),
        source_content_hash=row["source_content_hash"],
        source_to_scene_matrix_row_major=matrix,
    )


def _source_member_id(assembly_content_hash: str, ordinal: int) -> str:
    digest = hashlib.sha256(
        f"{assembly_content_hash}:{ordinal}".encode("ascii")
    ).hexdigest()
    return f"member_{digest[:32]}"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceAssemblyError(f"{field} is required")
    return value.strip()
