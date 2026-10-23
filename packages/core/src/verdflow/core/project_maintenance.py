"""Project-scoped maintenance tasks composed by an application Host."""

from __future__ import annotations

from dataclasses import dataclass

from verdflow.project_store import ProjectStore

from .maintenance import MaintenanceDescriptor, MaintenanceTaskResult
from .retention_sweeper import JobRetentionSweeper


@dataclass(frozen=True, slots=True)
class RetentionMaintenanceTask:
    sweeper: JobRetentionSweeper
    interval_seconds: float = 300.0
    retry_interval_seconds: float = 60.0
    max_retry_interval_seconds: float = 1800.0
    continuation_interval_seconds: float = 1.0

    @property
    def descriptor(self) -> MaintenanceDescriptor:
        return MaintenanceDescriptor(
            task_id="project.retention",
            interval_seconds=self.interval_seconds,
            retry_interval_seconds=self.retry_interval_seconds,
            max_retry_interval_seconds=self.max_retry_interval_seconds,
            continuation_interval_seconds=self.continuation_interval_seconds,
        )

    def run_once(self) -> MaintenanceTaskResult:
        result = self.sweeper.sweep_once(collect_objects=False)
        metrics = (
            ("examined_jobs", result.examined_job_count),
            ("removed_jobs", result.removed_job_count),
            ("protected_jobs", result.protected_job_count),
            ("removed_assignments", result.removed_assignment_count),
            ("removed_snapshots", result.removed_snapshot_count),
            ("removed_artifacts", result.removed_artifact_count),
        )
        return MaintenanceTaskResult(
            did_work=any(value for _, value in metrics[1:]),
            has_more=result.examined_job_count >= self.sweeper.batch_size,
            metrics=metrics,
        )


@dataclass(frozen=True, slots=True)
class ObjectGarbageCollectionTask:
    store: ProjectStore
    interval_seconds: float = 86400.0
    retry_interval_seconds: float = 60.0
    max_retry_interval_seconds: float = 1800.0

    @property
    def descriptor(self) -> MaintenanceDescriptor:
        return MaintenanceDescriptor(
            task_id="project.object-gc",
            interval_seconds=self.interval_seconds,
            retry_interval_seconds=self.retry_interval_seconds,
            max_retry_interval_seconds=self.max_retry_interval_seconds,
        )

    def run_once(self) -> MaintenanceTaskResult:
        result = self.store.collect_garbage()
        metrics = (
            ("removed_objects", result.removed_object_count),
            ("removed_bytes", result.removed_byte_size),
            ("removed_staging_files", result.removed_staging_file_count),
        )
        return MaintenanceTaskResult(
            did_work=any(value for _, value in metrics),
            metrics=metrics,
        )
