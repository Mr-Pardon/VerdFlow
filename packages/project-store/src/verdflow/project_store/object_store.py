"""Immutable content-addressed blob storage."""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from .layout import ProjectLayout


HASH_CHUNK_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StoredObject:
    content_hash: str
    byte_size: int
    relative_path: str


@dataclass(frozen=True, slots=True)
class ObjectCollectionResult:
    removed_object_count: int
    removed_byte_size: int
    removed_staging_file_count: int


class ContentAddressedObjectStore:
    def __init__(self, layout: ProjectLayout) -> None:
        self.layout = layout

    def put_bytes(self, data: bytes) -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()
        staged_path = self._new_staging_path()
        with staged_path.open("wb") as stream:
            stream.write(data)
            self._flush(stream)
        return self._install(staged_path, digest, len(data))

    def put_file(self, source: Path) -> StoredObject:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        staged_path = self._new_staging_path()
        digest = hashlib.sha256()
        byte_size = 0
        with source.open("rb") as input_stream, staged_path.open("wb") as output_stream:
            while chunk := input_stream.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
                output_stream.write(chunk)
                byte_size += len(chunk)
            self._flush(output_stream)
        return self._install(staged_path, digest.hexdigest(), byte_size)

    @contextmanager
    def open(self, content_hash: str) -> Iterator[BinaryIO]:
        with self.path_for(content_hash).open("rb") as stream:
            yield stream

    def path_for(self, content_hash: str) -> Path:
        return (
            self.layout.objects
            / content_hash[:2]
            / f"{content_hash}.blob"
        )

    def collect_unreferenced(
        self,
        referenced_hashes: set[str],
    ) -> ObjectCollectionResult:
        removed_count = 0
        removed_bytes = 0
        for path in self.layout.objects.glob("[0-9a-f][0-9a-f]/*.blob"):
            if path.stem in referenced_hashes:
                continue
            removed_bytes += path.stat().st_size
            path.unlink()
            removed_count += 1

        removed_staging = 0
        for path in self.layout.staging.glob("*.tmp"):
            path.unlink()
            removed_staging += 1
        return ObjectCollectionResult(
            removed_object_count=removed_count,
            removed_byte_size=removed_bytes,
            removed_staging_file_count=removed_staging,
        )

    def _new_staging_path(self) -> Path:
        self.layout.staging.mkdir(parents=True, exist_ok=True)
        return self.layout.staging / f"{uuid.uuid4().hex}.tmp"

    def _install(
        self,
        staged_path: Path,
        content_hash: str,
        byte_size: int,
    ) -> StoredObject:
        object_path = self.path_for(content_hash)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if object_path.exists():
            staged_path.unlink()
        else:
            os.replace(staged_path, object_path)
        return StoredObject(
            content_hash=content_hash,
            byte_size=byte_size,
            relative_path=object_path.relative_to(self.layout.root).as_posix(),
        )

    @staticmethod
    def _flush(stream: BinaryIO) -> None:
        stream.flush()
        os.fsync(stream.fileno())

