"""Small-batch orchestration for resumable project retention cleanup."""

from __future__ import annotations

from datetime import UTC, timedelta
import json

from verdflow.contracts import JobRetentionSweepResult
from verdflow.project_store import ProjectStore

from .clock import Clock, SystemClock


class JobRetentionSweeper:
    def __init__(
        self,
        store: ProjectStore,
        *,
        clock: Clock | None = None,
        orphan_grace: timedelta = timedelta(days=1),
        batch_size: int = 100,
    ) -> None:
        if orphan_grace.total_seconds() <= 0:
            raise ValueError("orphan_grace must be positive")
        if batch_size < 1 or batch_size > 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        self.store = store
        self.clock = clock or SystemClock()
        self.orphan_grace = orphan_grace
        self.batch_size = batch_size

    def sweep_once(
        self,
        *,
        collect_objects: bool = True,
    ) -> JobRetentionSweepResult:
        now = self.clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        protected_artifact_ids = self._scene_fact_artifact_ids()
        return self.store.sweep_expired_retention(
            now=now.isoformat(),
            orphan_before=(now - self.orphan_grace).isoformat(),
            limit=self.batch_size,
            collect_objects=collect_objects,
            protected_artifact_ids=protected_artifact_ids,
        )

    def _scene_fact_artifact_ids(self) -> tuple[str, ...]:
        protected: set[str] = set()
        for revision in self.store.list_scene_revisions():
            with self.store.open_artifact(
                revision.scene_manifest_artifact_id
            ) as stream:
                manifest = json.load(stream)
            if not isinstance(manifest, dict):
                raise ValueError("Scene manifest must be a JSON object")
            facts = manifest.get("facts")
            if not isinstance(facts, dict):
                raise ValueError("Scene manifest facts are required")
            for field in (
                "scene_inspection_manifest_artifact_id",
                "material_fact_report_artifact_id",
            ):
                artifact_id = facts.get(field)
                if not isinstance(artifact_id, str) or not artifact_id.strip():
                    raise ValueError(f"Scene manifest {field} is required")
                protected.add(artifact_id.strip())
        return tuple(sorted(protected))
