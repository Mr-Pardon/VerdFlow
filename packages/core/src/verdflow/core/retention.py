"""Core-owned Job retention policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from verdflow.contracts import JobRetentionClass


@dataclass(frozen=True, slots=True)
class JobRetentionPolicy:
    temporary: timedelta = timedelta(days=1)
    candidate: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        if self.temporary.total_seconds() < 1:
            raise ValueError("temporary retention must be at least one second")
        if self.candidate.total_seconds() < 1:
            raise ValueError("candidate retention must be at least one second")

    def seconds_for(
        self,
        retention_class: JobRetentionClass,
    ) -> int | None:
        if not isinstance(retention_class, JobRetentionClass):
            raise ValueError("retention_class is invalid")
        if retention_class is JobRetentionClass.TEMPORARY:
            return int(self.temporary.total_seconds())
        if retention_class is JobRetentionClass.CANDIDATE:
            return int(self.candidate.total_seconds())
        return None
