"""Filesystem layout for the user-scoped VerdFlow Library."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LibraryLayout:
    root: Path
    database: Path
    strategy_packages: Path
    resources: Path
    resource_staging: Path
    staging: Path
    cache: Path
    migration_backups: Path
    initialization_guard: Path
    package_mutation_guard: Path
    resource_mutation_guard: Path

    @classmethod
    def from_root(cls, root: Path) -> LibraryLayout:
        resolved = root.resolve()
        cache = resolved / "cache"
        packages = resolved / "strategy-packages"
        resources = resolved / "resources"
        return cls(
            root=resolved,
            database=resolved / "library.db",
            strategy_packages=packages,
            resources=resources,
            resource_staging=resources / ".staging",
            staging=packages / ".staging",
            cache=cache,
            migration_backups=cache / "migration-backups",
            initialization_guard=cache / ".initialize.guard",
            package_mutation_guard=cache / ".packages.guard",
            resource_mutation_guard=cache / ".resources.guard",
        )

    def prepare(self) -> None:
        for path in (
            self.root,
            self.strategy_packages,
            self.resources,
            self.resource_staging,
            self.staging,
            self.cache,
            self.migration_backups,
        ):
            path.mkdir(parents=True, exist_ok=True)
