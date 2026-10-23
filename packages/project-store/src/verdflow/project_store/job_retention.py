"""Job retention invariants shared by terminal transitions and recovery."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from verdflow.contracts import JobRetentionClass


def validate_retention(
    retention_class: JobRetentionClass,
    retention_seconds: int | None,
) -> None:
    if retention_class in {
        JobRetentionClass.TEMPORARY,
        JobRetentionClass.CANDIDATE,
    }:
        if (
            not isinstance(retention_seconds, int)
            or isinstance(retention_seconds, bool)
            or retention_seconds <= 0
        ):
            raise ValueError(
                f"{retention_class.value} retention requires positive seconds"
            )
        return
    if retention_seconds is not None:
        raise ValueError(
            f"{retention_class.value} retention cannot define seconds"
        )


def purge_after_for(row: Mapping[str, object], finished_at: str) -> str | None:
    retention_class = JobRetentionClass(str(row["retention_class"]))
    seconds = row["retention_seconds"]
    validate_retention(
        retention_class,
        int(seconds) if seconds is not None else None,
    )
    if seconds is None:
        return None
    return (
        datetime.fromisoformat(finished_at) + timedelta(seconds=int(seconds))
    ).isoformat()
