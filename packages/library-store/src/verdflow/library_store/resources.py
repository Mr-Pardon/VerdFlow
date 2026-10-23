"""Content-addressed managed resources for immutable library documents."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from verdflow.contracts import (
    LibraryResourceInfo,
    require_library_resource_id,
)

from .database import LibraryDatabase
from .errors import LibraryIntegrityError, LibraryResourceNotFoundError
from .layout import LibraryLayout


MAX_RESOURCE_BYTES = 16 * 1024 * 1024


class ManagedLibraryResources:
    def __init__(self, layout: LibraryLayout, database: LibraryDatabase) -> None:
        self._layout = layout
        self._database = database

    def import_file(self, source: Path, *, media_type: str) -> LibraryResourceInfo:
        source_path = source.resolve(strict=True)
        if not source_path.is_file():
            raise ValueError(f"Library resource is not a file: {source_path}")
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type is required")
        size = source_path.stat().st_size
        if size <= 0:
            raise ValueError("Library resource must not be empty")
        if size > MAX_RESOURCE_BYTES:
            raise ValueError("Library resource exceeds the 16 MiB limit")

        staged = self._layout.resource_staging / uuid.uuid4().hex
        digest = hashlib.sha256()
        written = 0
        try:
            with source_path.open("rb") as input_stream, staged.open("xb") as output:
                while chunk := input_stream.read(1024 * 1024):
                    digest.update(chunk)
                    written += len(chunk)
                    if written > MAX_RESOURCE_BYTES:
                        raise ValueError("Library resource exceeds the 16 MiB limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            return self._commit_staged(
                staged,
                digest_text=digest.hexdigest(),
                byte_size=written,
                media_type=media_type.strip(),
            )
        finally:
            staged.unlink(missing_ok=True)

    def import_bytes(
        self,
        payload: bytes,
        *,
        media_type: str,
    ) -> LibraryResourceInfo:
        if not isinstance(payload, bytes):
            raise TypeError("Library resource payload must be bytes")
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type is required")
        if not payload:
            raise ValueError("Library resource must not be empty")
        if len(payload) > MAX_RESOURCE_BYTES:
            raise ValueError("Library resource exceeds the 16 MiB limit")

        staged = self._layout.resource_staging / uuid.uuid4().hex
        try:
            with staged.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            return self._commit_staged(
                staged,
                digest_text=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
                media_type=media_type.strip(),
            )
        finally:
            staged.unlink(missing_ok=True)

    def get(self, resource_id: str) -> LibraryResourceInfo:
        require_library_resource_id(resource_id)
        row = self._database.connection.execute(
            "SELECT * FROM library_resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        if row is None:
            raise LibraryResourceNotFoundError(resource_id)
        info = _to_info(row)
        self._verify_file(
            self._path_for_hash(info.content_hash),
            info.content_hash,
            info.byte_size,
        )
        return info

    def read_bytes(self, resource_id: str) -> tuple[LibraryResourceInfo, bytes]:
        info = self.get(resource_id)
        payload = self._path_for_hash(info.content_hash).read_bytes()
        actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if actual != info.content_hash:
            raise LibraryIntegrityError(
                f"Managed resource content changed: {resource_id}"
            )
        return info, payload

    def cleanup_staging(self) -> int:
        removed = 0
        for path in self._layout.resource_staging.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        return removed

    def _commit_staged(
        self,
        staged: Path,
        *,
        digest_text: str,
        byte_size: int,
        media_type: str,
    ) -> LibraryResourceInfo:
        content_hash = f"sha256:{digest_text}"
        resource_id = f"res_{digest_text}"
        destination = self._path_for_hash(content_hash)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_file(destination, content_hash, byte_size)
            staged.unlink()
        else:
            os.replace(staged, destination)

        created_at = datetime.now(UTC).isoformat()
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM library_resources WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO library_resources(
                        resource_id, content_hash, media_type,
                        byte_size, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        resource_id,
                        content_hash,
                        media_type,
                        byte_size,
                        created_at,
                    ),
                )
            elif (
                str(existing["content_hash"]) != content_hash
                or int(existing["byte_size"]) != byte_size
                or str(existing["media_type"]) != media_type
            ):
                raise LibraryIntegrityError(
                    "Managed resource metadata conflicts with existing content"
                )
        return self.get(resource_id)

    def _path_for_hash(self, content_hash: str) -> Path:
        digest = content_hash.removeprefix("sha256:")
        return self._layout.resources / "sha256" / digest[:2] / digest

    @staticmethod
    def _verify_file(path: Path, content_hash: str, byte_size: int) -> None:
        if not path.is_file() or path.stat().st_size != byte_size:
            raise LibraryIntegrityError(
                f"Managed resource is missing or has the wrong size: {content_hash}"
            )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if f"sha256:{digest.hexdigest()}" != content_hash:
            raise LibraryIntegrityError(
                f"Managed resource content changed: {content_hash}"
            )


def _to_info(row: sqlite3.Row) -> LibraryResourceInfo:
    return LibraryResourceInfo(
        resource_id=str(row["resource_id"]),
        content_hash=str(row["content_hash"]),
        media_type=str(row["media_type"]),
        byte_size=int(row["byte_size"]),
        created_at=str(row["created_at"]),
    )
