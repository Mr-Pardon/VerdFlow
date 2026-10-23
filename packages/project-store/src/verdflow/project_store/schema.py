"""Versioned SQLite schema initialization and compatibility checks."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from .database import ProjectDatabase
from .errors import ProjectSchemaError


SCHEMA_VERSION = 23

JOB_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        command_type TEXT NOT NULL,
        command_version TEXT NOT NULL,
        command_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        status TEXT NOT NULL,
        progress REAL NOT NULL CHECK (progress >= 0.0 AND progress <= 1.0),
        stage TEXT NOT NULL,
        max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
        attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
        worker_id TEXT,
        lease_token TEXT UNIQUE,
        heartbeat_at TEXT,
        lease_expires_at TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        retention_class TEXT NOT NULL
            CHECK (retention_class IN ('temporary', 'candidate', 'run', 'audit')),
        retention_seconds INTEGER CHECK (retention_seconds > 0),
        purge_after TEXT,
        result_artifact_id TEXT REFERENCES artifacts(artifact_id),
        error_code TEXT,
        error_message TEXT,
        error_retryable INTEGER,
        diagnostic_artifact_id TEXT REFERENCES artifacts(artifact_id),
        CHECK (
            (retention_class IN ('temporary', 'candidate') AND
             retention_seconds IS NOT NULL)
            OR
            (retention_class IN ('run', 'audit') AND
             retention_seconds IS NULL AND purge_after IS NULL)
        ),
        CHECK (
            retention_class IN ('run', 'audit')
            OR
            (status IN ('cancelled', 'succeeded', 'failed', 'interrupted') AND
             purge_after IS NOT NULL)
            OR
            (status NOT IN ('cancelled', 'succeeded', 'failed', 'interrupted') AND
             purge_after IS NULL)
        )
    )
    """,
    "CREATE INDEX jobs_status_created_idx ON jobs(status, created_at)",
    "CREATE INDEX jobs_lease_expiry_idx ON jobs(status, lease_expires_at)",
    "CREATE INDEX jobs_purge_after_idx ON jobs(purge_after, job_id) "
    "WHERE purge_after IS NOT NULL",
    """
    CREATE TABLE job_attempts (
        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
        worker_id TEXT NOT NULL,
        lease_token TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        lease_expires_at TEXT,
        finished_at TEXT,
        error_code TEXT,
        error_message TEXT,
        error_retryable INTEGER,
        diagnostic_artifact_id TEXT REFERENCES artifacts(artifact_id),
        PRIMARY KEY (job_id, attempt_number)
    )
    """,
    """
    CREATE TABLE job_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
        progress REAL NOT NULL CHECK (progress >= 0.0 AND progress <= 1.0),
        stage TEXT NOT NULL,
        message TEXT,
        UNIQUE (job_id, sequence)
    )
    """,
    "CREATE INDEX job_events_job_sequence_idx ON job_events(job_id, sequence)",
)

SCENE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE coordinate_frames (
        coordinate_frame_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        handedness TEXT NOT NULL,
        up_axis TEXT NOT NULL,
        linear_unit TEXT NOT NULL,
        crs_kind TEXT NOT NULL,
        crs_definition TEXT,
        local_origin_x REAL NOT NULL,
        local_origin_y REAL NOT NULL,
        local_origin_z REAL NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sources (
        source_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        original_name TEXT NOT NULL,
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        content_hash TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        UNIQUE (kind, artifact_id)
    )
    """,
    "CREATE INDEX sources_content_hash_idx ON sources(content_hash)",
    """
    CREATE TABLE scene_revisions (
        scene_revision_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        parent_revision_id TEXT REFERENCES scene_revisions(scene_revision_id),
        importer_id TEXT NOT NULL,
        importer_version TEXT NOT NULL,
        coordinate_frame_id TEXT NOT NULL
            REFERENCES coordinate_frames(coordinate_frame_id),
        scene_manifest_artifact_id TEXT NOT NULL
            REFERENCES artifacts(artifact_id),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX scene_revisions_source_created_idx "
    "ON scene_revisions(source_id, created_at)",
)

SOURCE_ASSEMBLY_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE source_assembly_members (
        source_member_id TEXT PRIMARY KEY,
        assembly_source_id TEXT NOT NULL
            REFERENCES sources(source_id) ON DELETE CASCADE,
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        m00 REAL NOT NULL,
        m01 REAL NOT NULL,
        m02 REAL NOT NULL,
        m03 REAL NOT NULL,
        m10 REAL NOT NULL,
        m11 REAL NOT NULL,
        m12 REAL NOT NULL,
        m13 REAL NOT NULL,
        m20 REAL NOT NULL,
        m21 REAL NOT NULL,
        m22 REAL NOT NULL,
        m23 REAL NOT NULL,
        m30 REAL NOT NULL,
        m31 REAL NOT NULL,
        m32 REAL NOT NULL,
        m33 REAL NOT NULL,
        UNIQUE (assembly_source_id, ordinal)
    )
    """,
    "CREATE INDEX source_assembly_members_source_idx "
    "ON source_assembly_members(source_id, assembly_source_id)",
)

WORKER_PROCESS_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE worker_processes (
        job_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL,
        adapter_id TEXT NOT NULL,
        host TEXT NOT NULL,
        pid INTEGER NOT NULL CHECK (pid > 0),
        started_at_token TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        heartbeat_at TEXT,
        finished_at TEXT,
        exit_code INTEGER,
        PRIMARY KEY (job_id, attempt_number),
        FOREIGN KEY (job_id, attempt_number)
            REFERENCES job_attempts(job_id, attempt_number)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX worker_processes_status_idx "
    "ON worker_processes(status, started_at)",
)

REGION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE region_sets (
        region_set_id TEXT PRIMARY KEY,
        scene_revision_id TEXT NOT NULL
            REFERENCES scene_revisions(scene_revision_id),
        producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        extractor_id TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        coordinate_frame_id TEXT NOT NULL
            REFERENCES coordinate_frames(coordinate_frame_id),
        geometry_artifact_id TEXT NOT NULL
            REFERENCES artifacts(artifact_id),
        metrics_artifact_id TEXT NOT NULL
            REFERENCES artifacts(artifact_id),
        region_count INTEGER NOT NULL CHECK (region_count >= 0),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX region_sets_scene_created_idx "
    "ON region_sets(scene_revision_id, created_at)",
    """
    CREATE TABLE regions (
        region_set_id TEXT NOT NULL
            REFERENCES region_sets(region_set_id) ON DELETE CASCADE,
        region_id TEXT NOT NULL,
        source_member_id TEXT
            REFERENCES source_assembly_members(source_member_id),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        label TEXT NOT NULL,
        tags TEXT NOT NULL,
        classification TEXT,
        strategy_hint TEXT,
        footprint_area_xy REAL,
        boundary_perimeter_xy REAL,
        min_x REAL NOT NULL,
        min_y REAL NOT NULL,
        min_z REAL NOT NULL,
        max_x REAL NOT NULL,
        max_y REAL NOT NULL,
        max_z REAL NOT NULL,
        metrics_json TEXT NOT NULL,
        PRIMARY KEY (region_set_id, region_id),
        UNIQUE (region_set_id, ordinal),
        CHECK (min_x <= max_x),
        CHECK (min_y <= max_y),
        CHECK (min_z <= max_z)
    )
    """,
    "CREATE INDEX regions_classification_idx "
    "ON regions(region_set_id, classification)",
    "CREATE INDEX regions_bounds_idx "
    "ON regions(region_set_id, min_x, max_x, min_y, max_y)",
)

POINT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE planting_runs (
        planting_run_id TEXT PRIMARY KEY,
        scene_revision_id TEXT NOT NULL
            REFERENCES scene_revisions(scene_revision_id),
        region_set_id TEXT NOT NULL
            REFERENCES region_sets(region_set_id),
        producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        strategy_package_hash TEXT NOT NULL,
        generator_id TEXT NOT NULL,
        generator_version TEXT NOT NULL,
        random_seed INTEGER NOT NULL,
        input_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX planting_runs_scene_created_idx "
    "ON planting_runs(scene_revision_id, created_at)",
    "CREATE INDEX planting_runs_region_set_idx "
    "ON planting_runs(region_set_id, created_at)",
    """
    CREATE TABLE point_sets (
        point_set_id TEXT PRIMARY KEY,
        planting_run_id TEXT NOT NULL
            REFERENCES planting_runs(planting_run_id),
        parent_point_set_id TEXT
            REFERENCES point_sets(point_set_id),
        producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        kind TEXT NOT NULL
            CHECK (kind IN ('generated', 'elevation_resolved')),
        producer_id TEXT NOT NULL,
        producer_version TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        coordinate_frame_id TEXT NOT NULL
            REFERENCES coordinate_frames(coordinate_frame_id),
        point_artifact_id TEXT NOT NULL
            REFERENCES artifacts(artifact_id),
        resolution_policy TEXT NOT NULL,
        point_count INTEGER NOT NULL CHECK (point_count >= 0),
        sampled_count INTEGER NOT NULL CHECK (
            sampled_count >= 0 AND sampled_count <= point_count
        ),
        hit_count INTEGER NOT NULL CHECK (hit_count >= 0),
        miss_count INTEGER NOT NULL CHECK (miss_count >= 0),
        min_x REAL,
        min_y REAL,
        min_z REAL,
        max_x REAL,
        max_y REAL,
        max_z REAL,
        created_at TEXT NOT NULL,
        CHECK (sampled_count = hit_count + miss_count),
        CHECK (
            (point_count = 0 AND
             min_x IS NULL AND min_y IS NULL AND min_z IS NULL AND
             max_x IS NULL AND max_y IS NULL AND max_z IS NULL)
            OR
            (point_count > 0 AND
             min_x IS NOT NULL AND min_y IS NOT NULL AND
             min_z IS NOT NULL AND max_x IS NOT NULL AND
             max_y IS NOT NULL AND max_z IS NOT NULL AND
             min_x <= max_x AND min_y <= max_y AND min_z <= max_z)
        ),
        CHECK (
            (kind = 'generated' AND parent_point_set_id IS NULL AND
             sampled_count = 0 AND hit_count = 0 AND miss_count = 0)
            OR
            (kind = 'elevation_resolved' AND
             parent_point_set_id IS NOT NULL AND
             sampled_count = point_count)
        )
    )
    """,
    "CREATE INDEX point_sets_run_created_idx "
    "ON point_sets(planting_run_id, created_at)",
    "CREATE INDEX point_sets_parent_idx "
    "ON point_sets(parent_point_set_id, created_at)",
    "CREATE UNIQUE INDEX point_sets_generated_run_idx "
    "ON point_sets(planting_run_id) WHERE kind = 'generated'",
    """
    CREATE TABLE point_set_roles (
        point_set_id TEXT NOT NULL
            REFERENCES point_sets(point_set_id) ON DELETE CASCADE,
        role_key TEXT NOT NULL,
        point_count INTEGER NOT NULL CHECK (point_count > 0),
        PRIMARY KEY (point_set_id, role_key)
    )
    """,
)

SNAPSHOT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE strategy_package_snapshots (
        strategy_package_snapshot_id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        package_hash TEXT NOT NULL UNIQUE,
        package_artifact_id TEXT NOT NULL UNIQUE
            REFERENCES artifacts(artifact_id),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE planting_run_strategy_snapshots (
        planting_run_id TEXT PRIMARY KEY
            REFERENCES planting_runs(planting_run_id),
        strategy_package_snapshot_id TEXT NOT NULL
            REFERENCES strategy_package_snapshots(strategy_package_snapshot_id)
    )
    """,
    """
    CREATE TABLE target_asset_use_snapshots (
        target_asset_use_snapshot_id TEXT PRIMARY KEY,
        target_type TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        source_kind TEXT NOT NULL CHECK (source_kind IN ('catalog', 'profile', 'draft')),
        source_id TEXT NOT NULL,
        source_generation INTEGER,
        source_hash TEXT NOT NULL,
        selected_asset_count INTEGER NOT NULL
            CHECK (selected_asset_count > 0),
        semantic_hash TEXT NOT NULL UNIQUE,
        snapshot_artifact_id TEXT NOT NULL UNIQUE
            REFERENCES artifacts(artifact_id),
        created_at TEXT NOT NULL,
        CHECK (
            (source_kind = 'catalog' AND source_generation > 0)
            OR
            (source_kind IN ('profile', 'draft') AND source_generation IS NULL)
        )
    )
    """,
    "CREATE INDEX target_asset_use_source_idx "
    "ON target_asset_use_snapshots(target_type, source_kind, source_id)",
    """
    CREATE TABLE effective_binding_snapshots (
        effective_binding_snapshot_id TEXT PRIMARY KEY,
        strategy_package_snapshot_id TEXT NOT NULL
            REFERENCES strategy_package_snapshots(strategy_package_snapshot_id),
        target_asset_use_snapshot_id TEXT NOT NULL
            REFERENCES target_asset_use_snapshots(target_asset_use_snapshot_id),
        target_type TEXT NOT NULL,
        source_profile_id TEXT,
        source_profile_hash TEXT,
        override_applied INTEGER NOT NULL
            CHECK (override_applied IN (0, 1)),
        semantic_hash TEXT NOT NULL UNIQUE,
        snapshot_artifact_id TEXT NOT NULL UNIQUE
            REFERENCES artifacts(artifact_id),
        created_at TEXT NOT NULL,
        CHECK (
            (source_profile_id IS NULL AND
             source_profile_hash IS NULL)
            OR
            (source_profile_id IS NOT NULL AND
             source_profile_hash IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX effective_binding_package_idx "
    "ON effective_binding_snapshots(strategy_package_snapshot_id, created_at)",
    """
    CREATE TABLE asset_assignment_sets (
        asset_assignment_set_id TEXT PRIMARY KEY,
        point_set_id TEXT NOT NULL REFERENCES point_sets(point_set_id),
        effective_binding_snapshot_id TEXT NOT NULL
            REFERENCES effective_binding_snapshots(effective_binding_snapshot_id),
        producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        target_type TEXT NOT NULL,
        resolver_id TEXT NOT NULL,
        resolver_version TEXT NOT NULL,
        random_seed INTEGER NOT NULL CHECK (random_seed >= 0),
        semantic_hash TEXT NOT NULL,
        assignment_artifact_id TEXT NOT NULL
            REFERENCES artifacts(artifact_id),
        point_count INTEGER NOT NULL CHECK (point_count >= 0),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX assignment_sets_point_idx "
    "ON asset_assignment_sets(point_set_id, created_at)",
    "CREATE INDEX assignment_sets_binding_idx "
    "ON asset_assignment_sets(effective_binding_snapshot_id, created_at)",
    """
    CREATE TABLE asset_assignment_set_assets (
        asset_assignment_set_id TEXT NOT NULL
            REFERENCES asset_assignment_sets(asset_assignment_set_id)
            ON DELETE CASCADE,
        asset_id TEXT NOT NULL,
        point_count INTEGER NOT NULL CHECK (point_count > 0),
        PRIMARY KEY (asset_assignment_set_id, asset_id)
    )
    """,
)

EXPORT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE exports (
        export_id TEXT PRIMARY KEY,
        producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        adapter_id TEXT NOT NULL,
        adapter_version TEXT NOT NULL,
        target_type TEXT NOT NULL,
        point_set_id TEXT NOT NULL REFERENCES point_sets(point_set_id),
        asset_assignment_set_id TEXT NOT NULL
            REFERENCES asset_assignment_sets(asset_assignment_set_id),
        output_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        offset_x REAL NOT NULL,
        offset_y REAL NOT NULL,
        offset_z REAL NOT NULL,
        record_count INTEGER NOT NULL CHECK (record_count >= 0),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX exports_point_set_idx ON exports(point_set_id, created_at)",
    "CREATE INDEX exports_assignment_idx "
    "ON exports(asset_assignment_set_id, created_at)",
)

PLANT_RUN_EXECUTION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE plant_run_executions (
        execution_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        request_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        request_hash TEXT NOT NULL,
        continuation_artifact_id TEXT REFERENCES artifacts(artifact_id),
        continuation_hash TEXT,
        status TEXT NOT NULL CHECK (status IN (
            'queued', 'running', 'awaiting_confirmation', 'cancel_requested',
            'cancelled', 'failed', 'succeeded'
        )),
        stage TEXT NOT NULL CHECK (stage IN (
            'generation', 'assignment', 'review', 'elevation', 'export', 'completed'
        )),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        generation_job_id TEXT REFERENCES jobs(job_id),
        generated_point_set_id TEXT REFERENCES point_sets(point_set_id),
        assignment_job_id TEXT REFERENCES jobs(job_id),
        asset_assignment_set_id TEXT
            REFERENCES asset_assignment_sets(asset_assignment_set_id),
        elevation_job_id TEXT REFERENCES jobs(job_id),
        elevation_point_set_id TEXT REFERENCES point_sets(point_set_id),
        export_job_id TEXT REFERENCES jobs(job_id),
        export_id TEXT REFERENCES exports(export_id),
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            status != 'succeeded'
            OR (stage = 'completed' AND elevation_point_set_id IS NOT NULL)
        ),
        CHECK (status != 'failed' OR error_code IS NOT NULL),
        CHECK (
            (continuation_artifact_id IS NULL AND continuation_hash IS NULL)
            OR
            (continuation_artifact_id IS NOT NULL AND continuation_hash IS NOT NULL)
        ),
        CHECK (
            status != 'awaiting_confirmation'
            OR (
                stage = 'review'
                AND generated_point_set_id IS NOT NULL
                AND asset_assignment_set_id IS NOT NULL
                AND continuation_artifact_id IS NULL
            )
        )
    )
    """,
    "CREATE INDEX plant_run_executions_status_updated_idx "
    "ON plant_run_executions(status, updated_at, execution_id)",
)

MATERIAL_FLOW_EXECUTION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE material_flow_executions (
        execution_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        request_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        request_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'queued', 'running', 'awaiting_confirmation',
            'cancel_requested', 'cancelled', 'failed', 'succeeded'
        )),
        stage TEXT NOT NULL CHECK (stage IN (
            'analysis', 'confirmation', 'extraction', 'completed'
        )),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        analysis_job_id TEXT REFERENCES jobs(job_id),
        rule_pack_artifact_id TEXT REFERENCES artifacts(artifact_id),
        extraction_job_id TEXT REFERENCES jobs(job_id),
        region_set_id TEXT REFERENCES region_sets(region_set_id),
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            status != 'awaiting_confirmation'
            OR stage = 'confirmation'
        ),
        CHECK (
            status != 'succeeded'
            OR (stage = 'completed' AND region_set_id IS NOT NULL)
        ),
        CHECK (status != 'failed' OR error_code IS NOT NULL)
    )
    """,
    "CREATE INDEX material_flow_executions_status_updated_idx "
    "ON material_flow_executions(status, updated_at, execution_id)",
)

SCENE_PREPARATION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE scene_preparation_executions (
        execution_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        request_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        request_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'queued', 'running', 'awaiting_confirmation',
            'cancel_requested', 'cancelled', 'failed', 'succeeded'
        )),
        stage TEXT NOT NULL CHECK (stage IN (
            'inspection', 'material_flow', 'completed'
        )),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        inspection_job_id TEXT REFERENCES jobs(job_id),
        scene_revision_id TEXT REFERENCES scene_revisions(scene_revision_id),
        material_flow_execution_id TEXT
            REFERENCES material_flow_executions(execution_id),
        region_set_id TEXT REFERENCES region_sets(region_set_id),
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            status != 'awaiting_confirmation'
            OR (stage = 'material_flow' AND
                material_flow_execution_id IS NOT NULL)
        ),
        CHECK (
            status != 'succeeded'
            OR (stage = 'completed' AND region_set_id IS NOT NULL)
        ),
        CHECK (status != 'failed' OR error_code IS NOT NULL)
    )
    """,
    "CREATE INDEX scene_preparation_status_updated_idx "
    "ON scene_preparation_executions(status, updated_at, execution_id)",
)

PROJECT_WORKFLOW_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE project_workflow_state (
        project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
        source_id TEXT,
        scene_preparation_execution_id TEXT,
        material_flow_execution_id TEXT,
        region_set_id TEXT,
        plant_run_execution_id TEXT,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        updated_at TEXT NOT NULL
    )
    """,
)

COMPOSITION_OUTPUT_EXECUTION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE composition_output_executions (
        execution_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        request_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        request_hash TEXT NOT NULL,
        composition_id TEXT NOT NULL,
        composition_generation INTEGER NOT NULL
            CHECK (composition_generation >= 1),
        status TEXT NOT NULL CHECK (status IN (
            'queued', 'running', 'succeeded', 'failed'
        )),
        stage TEXT NOT NULL CHECK (stage IN (
            'elevation', 'export', 'completed'
        )),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        export_job_id TEXT REFERENCES jobs(job_id),
        export_id TEXT REFERENCES composition_exports(export_id)
            ON DELETE SET NULL,
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX composition_output_executions_status_idx "
    "ON composition_output_executions(status, updated_at, execution_id)",
)


PLANTING_COMPOSITION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE planting_compositions (
        composition_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL UNIQUE
            REFERENCES projects(project_id) ON DELETE CASCADE,
        scene_revision_id TEXT NOT NULL
            REFERENCES scene_revisions(scene_revision_id),
        region_set_id TEXT NOT NULL REFERENCES region_sets(region_set_id),
        generation INTEGER NOT NULL CHECK (generation >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE planting_batches (
        batch_id TEXT PRIMARY KEY,
        composition_id TEXT NOT NULL
            REFERENCES planting_compositions(composition_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        plant_run_execution_id TEXT NOT NULL UNIQUE
            REFERENCES plant_run_executions(execution_id),
        generated_point_set_id TEXT NOT NULL REFERENCES point_sets(point_set_id),
        asset_assignment_set_id TEXT NOT NULL
            REFERENCES asset_assignment_sets(asset_assignment_set_id),
        selected_asset_ids_json TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (composition_id, ordinal)
    )
    """,
    "CREATE INDEX planting_batches_composition_idx "
    "ON planting_batches(composition_id, ordinal)",
    """
    CREATE TABLE planting_batch_regions (
        composition_id TEXT NOT NULL
            REFERENCES planting_compositions(composition_id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL
            REFERENCES planting_batches(batch_id) ON DELETE CASCADE,
        region_set_id TEXT NOT NULL,
        region_id TEXT NOT NULL,
        PRIMARY KEY (composition_id, region_id),
        FOREIGN KEY (region_set_id, region_id)
            REFERENCES regions(region_set_id, region_id)
    )
    """,
    "CREATE INDEX planting_batch_regions_batch_idx "
    "ON planting_batch_regions(batch_id, region_id)",
    """
    CREATE TABLE composition_exports (
        export_id TEXT PRIMARY KEY,
        composition_id TEXT NOT NULL,
        composition_generation INTEGER NOT NULL
            CHECK (composition_generation >= 1),
        producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
        adapter_id TEXT NOT NULL,
        adapter_version TEXT NOT NULL,
        target_type TEXT NOT NULL,
        output_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        offset_x REAL NOT NULL,
        offset_y REAL NOT NULL,
        offset_z REAL NOT NULL,
        record_count INTEGER NOT NULL CHECK (record_count >= 0),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX composition_exports_composition_idx "
    "ON composition_exports(composition_id, created_at)",
    """
    CREATE TABLE composition_export_inputs (
        export_id TEXT NOT NULL
            REFERENCES composition_exports(export_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        batch_id TEXT NOT NULL,
        point_set_id TEXT NOT NULL REFERENCES point_sets(point_set_id),
        asset_assignment_set_id TEXT NOT NULL
            REFERENCES asset_assignment_sets(asset_assignment_set_id),
        selected_asset_ids_json TEXT,
        PRIMARY KEY (export_id, ordinal),
        UNIQUE (export_id, batch_id)
    )
    """,
    *COMPOSITION_OUTPUT_EXECUTION_SCHEMA_STATEMENTS,
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE schema_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE projects (
        project_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        active_scene_revision_id TEXT
            REFERENCES scene_revisions(scene_revision_id)
    )
    """,
    """
    CREATE TABLE objects (
        content_hash TEXT PRIMARY KEY,
        relative_path TEXT NOT NULL UNIQUE,
        byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE artifacts (
        artifact_id TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL REFERENCES objects(content_hash),
        media_type TEXT NOT NULL,
        schema_id TEXT NOT NULL DEFAULT '',
        schema_version TEXT NOT NULL DEFAULT '',
        byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
        created_at TEXT NOT NULL,
        UNIQUE (content_hash, media_type, schema_id, schema_version)
    )
    """,
    "CREATE INDEX artifacts_content_hash_idx ON artifacts(content_hash)",
    *SCENE_SCHEMA_STATEMENTS,
    *SOURCE_ASSEMBLY_SCHEMA_STATEMENTS,
    """
    CREATE TABLE recovery_events (
        event_id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        previous_lock_json TEXT NOT NULL,
        details_json TEXT NOT NULL
    )
    """,
    *JOB_SCHEMA_STATEMENTS,
    *WORKER_PROCESS_SCHEMA_STATEMENTS,
    *REGION_SCHEMA_STATEMENTS,
    *POINT_SCHEMA_STATEMENTS,
    *SNAPSHOT_SCHEMA_STATEMENTS,
    *EXPORT_SCHEMA_STATEMENTS,
    *PLANT_RUN_EXECUTION_SCHEMA_STATEMENTS,
    *MATERIAL_FLOW_EXECUTION_SCHEMA_STATEMENTS,
    *SCENE_PREPARATION_SCHEMA_STATEMENTS,
    *PROJECT_WORKFLOW_SCHEMA_STATEMENTS,
    *PLANTING_COMPOSITION_SCHEMA_STATEMENTS,
)


def initialize_schema(database: ProjectDatabase, name: str) -> None:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Project name is required")
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.execute(
            """
            INSERT INTO projects(
                project_id, name, schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"prj_{uuid.uuid4().hex}",
                normalized_name,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO project_workflow_state(
                project_id, source_id, scene_preparation_execution_id,
                material_flow_execution_id, region_set_id,
                plant_run_execution_id, revision, updated_at
            )
            SELECT project_id, NULL, NULL, NULL, NULL, NULL, 1, ?
            FROM projects
            """,
            (now,),
        )


def validate_schema(database: ProjectDatabase) -> None:
    version = read_schema_version(database)
    if version != SCHEMA_VERSION:
        raise ProjectSchemaError(
            f"Unsupported project schema {version}; expected {SCHEMA_VERSION}"
        )
    columns = {
        row["name"]
        for row in database.connection.execute(
            "PRAGMA table_info(point_set_roles)"
        )
    }
    if columns != {"point_set_id", "role_key", "point_count"}:
        raise ProjectSchemaError(
            "Project schema predates the PointSet v2 role model; "
            "recreate this pre-release project"
        )
    job_columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(jobs)")
    }
    required_job_columns = {
        "retention_class",
        "retention_seconds",
        "purge_after",
    }
    if not required_job_columns.issubset(job_columns):
        raise ProjectSchemaError(
            "Project schema is missing Job retention fields"
        )
    invalid_retention_count = database.connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM jobs
        WHERE retention_class NOT IN ('temporary', 'candidate', 'run', 'audit')
           OR (retention_class IN ('temporary', 'candidate') AND
               (retention_seconds IS NULL OR retention_seconds <= 0))
           OR (retention_class IN ('temporary', 'candidate') AND
               status IN ('cancelled', 'succeeded', 'failed', 'interrupted') AND
               purge_after IS NULL)
           OR (retention_class IN ('temporary', 'candidate') AND
               status NOT IN ('cancelled', 'succeeded', 'failed', 'interrupted') AND
               purge_after IS NOT NULL)
           OR (retention_class IN ('run', 'audit') AND
               (retention_seconds IS NOT NULL OR purge_after IS NOT NULL))
        """
    ).fetchone()["count"]
    if int(invalid_retention_count) != 0:
        raise ProjectSchemaError("Project contains invalid Job retention state")
    export_columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(exports)")
    }
    required_export_columns = {
        "export_id",
        "producer_job_id",
        "adapter_id",
        "adapter_version",
        "target_type",
        "point_set_id",
        "asset_assignment_set_id",
        "output_artifact_id",
        "offset_x",
        "offset_y",
        "offset_z",
        "record_count",
        "created_at",
    }
    if export_columns != required_export_columns:
        raise ProjectSchemaError("Project schema is missing ExportRecord fields")
    execution_columns = {
        row["name"]
        for row in database.connection.execute(
            "PRAGMA table_info(plant_run_executions)"
        )
    }
    required_execution_columns = {
        "execution_id",
        "idempotency_key",
        "request_artifact_id",
        "request_hash",
        "continuation_artifact_id",
        "continuation_hash",
        "status",
        "stage",
        "revision",
        "generation_job_id",
        "generated_point_set_id",
        "assignment_job_id",
        "asset_assignment_set_id",
        "elevation_job_id",
        "elevation_point_set_id",
        "export_job_id",
        "export_id",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    }
    if execution_columns != required_execution_columns:
        raise ProjectSchemaError(
            "Project schema is missing Plant Run execution fields"
        )
    material_flow_columns = {
        row["name"]
        for row in database.connection.execute(
            "PRAGMA table_info(material_flow_executions)"
        )
    }
    required_material_flow_columns = {
        "execution_id",
        "idempotency_key",
        "request_artifact_id",
        "request_hash",
        "status",
        "stage",
        "revision",
        "analysis_job_id",
        "rule_pack_artifact_id",
        "extraction_job_id",
        "region_set_id",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    }
    if material_flow_columns != required_material_flow_columns:
        raise ProjectSchemaError(
            "Project schema is missing Material Flow execution fields"
        )
    scene_preparation_columns = {
        row["name"]
        for row in database.connection.execute(
            "PRAGMA table_info(scene_preparation_executions)"
        )
    }
    required_scene_preparation_columns = {
        "execution_id",
        "idempotency_key",
        "request_artifact_id",
        "request_hash",
        "status",
        "stage",
        "revision",
        "source_id",
        "inspection_job_id",
        "scene_revision_id",
        "material_flow_execution_id",
        "region_set_id",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    }
    if scene_preparation_columns != required_scene_preparation_columns:
        raise ProjectSchemaError(
            "Project schema is missing Scene Preparation execution fields"
        )
    composition_columns = {
        row["name"]
        for row in database.connection.execute(
            "PRAGMA table_info(planting_compositions)"
        )
    }
    if composition_columns != {
        "composition_id",
        "project_id",
        "scene_revision_id",
        "region_set_id",
        "generation",
        "created_at",
        "updated_at",
    }:
        raise ProjectSchemaError(
            "Project schema is missing Planting Composition fields"
        )
    required_composition_tables = {
        "planting_batches": {
            "batch_id",
            "composition_id",
            "ordinal",
            "plant_run_execution_id",
            "generated_point_set_id",
            "asset_assignment_set_id",
            "selected_asset_ids_json",
            "created_at",
        },
        "planting_batch_regions": {
            "composition_id",
            "batch_id",
            "region_set_id",
            "region_id",
        },
        "composition_exports": {
            "export_id",
            "composition_id",
            "composition_generation",
            "producer_job_id",
            "adapter_id",
            "adapter_version",
            "target_type",
            "output_artifact_id",
            "offset_x",
            "offset_y",
            "offset_z",
            "record_count",
            "created_at",
        },
        "composition_export_inputs": {
            "export_id",
            "ordinal",
            "batch_id",
            "point_set_id",
            "asset_assignment_set_id",
            "selected_asset_ids_json",
        },
        "composition_output_executions": {
            "execution_id",
            "idempotency_key",
            "request_artifact_id",
            "request_hash",
            "composition_id",
            "composition_generation",
            "status",
            "stage",
            "revision",
            "export_job_id",
            "export_id",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        },
    }
    for table_name, expected_columns in required_composition_tables.items():
        columns = {
            row["name"]
            for row in database.connection.execute(
                f"PRAGMA table_info({table_name})"
            )
        }
        if columns != expected_columns:
            raise ProjectSchemaError(
                f"Project schema is missing {table_name} fields"
            )


def read_schema_version(database: ProjectDatabase) -> int:
    try:
        row = database.connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError as error:
        raise ProjectSchemaError("Project database is malformed") from error
    if row is None:
        raise ProjectSchemaError("Project schema version is missing")
    try:
        version = int(row["value"])
    except (TypeError, ValueError) as error:
        raise ProjectSchemaError("Project schema version is invalid") from error
    return version
