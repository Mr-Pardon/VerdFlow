"""Physical durability profiles for project metadata storage."""

from __future__ import annotations

from enum import StrEnum


class ProjectStorageProfile(StrEnum):
    """Select SQLite durability without changing project domain semantics."""

    DURABLE = "durable"
    EPHEMERAL = "ephemeral"
