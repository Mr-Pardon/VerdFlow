"""Filesystem layout for an active VerdFlow project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    root: Path
    database: Path
    objects: Path
    staging: Path
    cache: Path
    exports: Path
    logs: Path
    recovery_logs: Path
    migration_backups: Path
    lock: Path
    recovery_guard: Path
    recovery_blocker: Path

    @classmethod
    def from_root(cls, root: Path) -> ProjectLayout:
        resolved = root.resolve()
        objects = resolved / "objects"
        logs = resolved / "logs"
        cache = resolved / "cache"
        return cls(
            root=resolved,
            database=resolved / "project.db",
            objects=objects,
            staging=objects / ".staging",
            cache=cache,
            exports=resolved / "exports",
            logs=logs,
            recovery_logs=logs / "recovery",
            migration_backups=cache / "migration-backups",
            lock=resolved / "project.lock",
            recovery_guard=logs / "recovery" / ".guard",
            recovery_blocker=logs / "recovery" / ".blocked",
        )

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (
            self.staging,
            self.cache,
            self.exports,
            self.logs,
            self.recovery_logs,
            self.migration_backups,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def cleanup_failed_creation(self) -> None:
        self.recovery_guard.unlink(missing_ok=True)
        self.recovery_blocker.unlink(missing_ok=True)
        for path in (
            self.database.with_name(f"{self.database.name}-wal"),
            self.database.with_name(f"{self.database.name}-shm"),
            self.database,
        ):
            path.unlink(missing_ok=True)
        for path in (
            self.staging,
            self.migration_backups,
            self.recovery_logs,
            self.objects,
            self.cache,
            self.exports,
            self.logs,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
        try:
            self.root.rmdir()
        except OSError:
            pass
