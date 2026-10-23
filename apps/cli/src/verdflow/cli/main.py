"""Minimal VerdFlow Phase 1 command line."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from verdflow.contracts import SourceKind
from verdflow.core import OutputExportRequest, RegionSourceRequest
from verdflow.core_host import (
    CoreHost,
    CoreHostConfig,
    PlantRunRequest,
    PlantRunTimeouts,
)
from verdflow.library_store import LibraryStore


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as error:
        _print_json(
            {
                "ok": False,
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
        )
        return 1
    _print_json({"ok": True, "result": result})
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verdflow")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    doctor.set_defaults(handler=_doctor)

    project = commands.add_parser("project")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    create = project_commands.add_parser("create")
    create.add_argument("project", type=Path)
    create.add_argument("--name", required=True)
    create.set_defaults(handler=_project_create)
    info = project_commands.add_parser("info")
    info.add_argument("project", type=Path)
    info.set_defaults(handler=_project_info)

    source = commands.add_parser("source")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    add = source_commands.add_parser("add")
    add.add_argument("project", type=Path)
    add.add_argument("file", type=Path)
    add.add_argument(
        "--kind",
        choices=[kind.value for kind in SourceKind if kind is not SourceKind.ASSEMBLY],
        default=SourceKind.FBX.value,
    )
    add.add_argument("--media-type")
    add.set_defaults(handler=_source_add)

    assembly = commands.add_parser("assembly")
    assembly_commands = assembly.add_subparsers(
        dest="assembly_command",
        required=True,
    )
    assembly_create = assembly_commands.add_parser("create")
    assembly_create.add_argument("project", type=Path)
    assembly_create.add_argument("--name", required=True)
    assembly_create.add_argument("--member", action="append", required=True)
    assembly_create.set_defaults(handler=_assembly_create)

    scene = commands.add_parser("scene")
    scene_commands = scene.add_subparsers(dest="scene_command", required=True)
    inspect = scene_commands.add_parser("inspect")
    inspect.add_argument("project", type=Path)
    inspect.add_argument("source_id")
    inspect.add_argument("--idempotency-key")
    inspect.add_argument("--timeout", type=float, default=1800.0)
    inspect.set_defaults(handler=_scene_inspect)

    geospatial = commands.add_parser("geospatial")
    geospatial_commands = geospatial.add_subparsers(
        dest="geospatial_command",
        required=True,
    )
    prepare = geospatial_commands.add_parser("prepare")
    prepare.add_argument("project", type=Path)
    prepare.add_argument("vector", type=Path)
    prepare.add_argument("elevation", type=Path)
    prepare.add_argument("--name")
    prepare.add_argument("--timeout", type=float, default=300.0)
    prepare.set_defaults(handler=_geospatial_prepare)

    export = commands.add_parser("export")
    export_commands = export.add_subparsers(
        dest="export_command",
        required=True,
    )
    dts = export_commands.add_parser("dts")
    dts.add_argument("project", type=Path)
    dts.add_argument("point_set_id")
    dts.add_argument("asset_assignment_set_id")
    dts.add_argument("output", type=Path)
    dts.add_argument("--offset-x", type=float, required=True)
    dts.add_argument("--offset-y", type=float, required=True)
    dts.add_argument("--offset-z", type=float, required=True)
    dts.add_argument("--idempotency-key")
    dts.add_argument("--timeout", type=float, default=300.0)
    dts.set_defaults(handler=_export_dts)

    plant = commands.add_parser("plant")
    plant_commands = plant.add_subparsers(
        dest="plant_command",
        required=True,
    )
    run = plant_commands.add_parser("run")
    run.add_argument("project", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--library", type=Path, required=True)
    run.add_argument("--scene-revision", required=True)
    run.add_argument("--region-set", required=True)
    run.add_argument("--region", action="append", required=True)
    run.add_argument("--strategy-package-hash", required=True)
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--assignment-seed", type=int)
    run.add_argument(
        "--elevation-provider",
        default="verdflow-blender-worker",
    )
    run.add_argument("--target", required=True)
    run.add_argument("--catalog-id", required=True)
    run.add_argument("--profile-id")
    run.add_argument("--export-provider", default="verdflow.dts.xml")
    run.add_argument("--offset-x", type=float, default=0.0)
    run.add_argument("--offset-y", type=float, default=0.0)
    run.add_argument("--offset-z", type=float, default=0.0)
    run.add_argument("--generation-args", default="{}")
    run.add_argument("--elevation-options", default="{}")
    run.add_argument("--binding-overrides", default="{}")
    run.add_argument("--blender", type=Path)
    run.add_argument(
        "--generation-timeout", type=float, default=None,
        help="Optional generation wait limit in seconds; unlimited by default",
    )
    run.add_argument("--elevation-timeout", type=float, default=1800.0)
    run.add_argument("--assignment-timeout", type=float, default=300.0)
    run.add_argument("--export-timeout", type=float, default=300.0)
    run.set_defaults(handler=_plant_run)

    job = commands.add_parser("job")
    job_commands = job.add_subparsers(dest="job_command", required=True)
    status = job_commands.add_parser("status")
    status.add_argument("project", type=Path)
    status.add_argument("job_id")
    status.set_defaults(handler=_job_status)
    events = job_commands.add_parser("events")
    events.add_argument("project", type=Path)
    events.add_argument("job_id")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=100)
    events.set_defaults(handler=_job_events)
    cancel = job_commands.add_parser("cancel")
    cancel.add_argument("project", type=Path)
    cancel.add_argument("job_id")
    cancel.set_defaults(handler=_job_cancel)
    return parser


def _doctor(_: argparse.Namespace) -> dict[str, Any]:
    diagnostics = CoreHost.diagnose()
    return {"blender": diagnostics}


def _project_create(args: argparse.Namespace) -> Any:
    with CoreHost.create(
        _project_config(args.project),
        project_name=args.name,
    ) as host:
        return host.application.project_info()


def _project_info(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        return host.application.project_info()


def _source_add(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        return host.add_source_file(
            args.file,
            kind=SourceKind(args.kind),
            media_type=args.media_type,
        )


def _assembly_create(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        return host.create_identity_assembly(
            original_name=args.name,
            source_ids=tuple(args.member),
        )


def _scene_inspect(args: argparse.Namespace) -> Any:
    with CoreHost.open(CoreHostConfig(args.project)) as host:
        return host.inspect_scene(
            args.source_id,
            idempotency_key=args.idempotency_key,
            timeout_seconds=args.timeout,
        )


def _geospatial_prepare(args: argparse.Namespace) -> Any:
    from verdflow.core_host import GeospatialExtensionFactory
    from verdflow.geospatial_worker import package_shapefile

    vector_path = args.vector.resolve()
    elevation_path = args.elevation.resolve()
    vector_suffix = vector_path.suffix.lower()
    if vector_suffix not in {".shp", ".geojson", ".json"}:
        raise ValueError("vector must be a .shp, .geojson or .json file")
    if elevation_path.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("elevation must be a .tif or .tiff file")
    project_path = args.project.resolve()
    extension = GeospatialExtensionFactory(
        runtime_root=project_path / "runtime" / "geospatial-worker",
        cache_root=project_path / "cache" / "geospatial-inputs",
        timeout_seconds=args.timeout,
    )
    with CoreHost.open(
        _project_config(project_path),
        extension_factories=(extension,),
    ) as host:
        if vector_suffix == ".shp":
            vector = host.add_source_bytes(
                package_shapefile(vector_path),
                original_name=f"{vector_path.stem}.shapefile.zip",
                kind=SourceKind.SHAPEFILE,
            )
        else:
            vector = host.add_source_file(
                vector_path,
                kind=SourceKind.GEOJSON,
            )
        elevation = host.add_source_file(
            elevation_path,
            kind=SourceKind.GEOTIFF,
        )
        assembly = host.create_identity_assembly(
            original_name=(
                args.name
                or f"{vector_path.stem} + {elevation_path.stem}"
            ),
            source_ids=(vector.source_id, elevation.source_id),
        )
        outcome = host.prepare_region_source(
            RegionSourceRequest(
                source_id=assembly.source_id,
                provider_id="verdflow.geospatial",
            ),
            timeout_seconds=args.timeout,
        )
        return {
            "source": assembly,
            "job": outcome.job,
            "scene_revision": outcome.scene_revision,
            "region_set": outcome.region_set,
        }


def _job_status(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        return host.application.get_job(args.job_id)


def _export_dts(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        outcome = host.export_output(
            OutputExportRequest(
                point_set_id=args.point_set_id,
                asset_assignment_set_id=args.asset_assignment_set_id,
                provider_id="verdflow.dts.xml",
                offset=(args.offset_x, args.offset_y, args.offset_z),
            ),
            idempotency_key=args.idempotency_key,
            timeout_seconds=args.timeout,
        )
        destination = host.store.materialize_artifact(
            outcome.export.output_artifact_id,
            args.output,
        )
        return {
            "job": outcome.job,
            "export": outcome.export,
            "output": str(destination),
        }


def _plant_run(args: argparse.Namespace) -> Any:
    request = PlantRunRequest(
        scene_revision_id=args.scene_revision,
        region_set_id=args.region_set,
        selected_region_ids=tuple(args.region),
        strategy_package_hash=args.strategy_package_hash,
        random_seed=args.seed,
        assignment_random_seed=args.assignment_seed,
        generation_args=_json_object(args.generation_args, "generation-args"),
        elevation_provider_id=args.elevation_provider,
        elevation_options=_json_object(
            args.elevation_options,
            "elevation-options",
        ),
        target_type=args.target,
        catalog_id=args.catalog_id,
        profile_id=args.profile_id,
        binding_overrides=_json_object(
            args.binding_overrides,
            "binding-overrides",
        ),
        export_provider_id=args.export_provider,
        offset=(args.offset_x, args.offset_y, args.offset_z),
    )
    timeouts = PlantRunTimeouts(
        generation_seconds=args.generation_timeout,
        elevation_seconds=args.elevation_timeout,
        assignment_seconds=args.assignment_timeout,
        export_seconds=args.export_timeout,
    )
    with LibraryStore.open(args.library) as library:
        extension_factories = ()
        use_geospatial = args.elevation_provider == "verdflow.geospatial"
        if use_geospatial:
            from verdflow.core_host import GeospatialExtensionFactory

            project_path = args.project.resolve()
            extension_factories = (
                GeospatialExtensionFactory(
                    runtime_root=(
                        project_path / "runtime" / "geospatial-worker"
                    ),
                    cache_root=(
                        project_path / "cache" / "geospatial-inputs"
                    ),
                    timeout_seconds=args.elevation_timeout,
                ),
            )
        with CoreHost.open(
            CoreHostConfig(
                args.project,
                enable_blender_worker=not use_geospatial,
                blender_executable=args.blender,
            ),
            strategy_library=library,
            extension_factories=extension_factories,
        ) as host:
            outcome = host.run_planting(request, timeouts=timeouts)
            destination = host.store.materialize_artifact(
                outcome.output.export.output_artifact_id,
                args.output,
            )
            return {
                "planting_run_id": outcome.planting_run_id,
                "generation": outcome.generation,
                "elevation": outcome.elevation,
                "assignment": outcome.assignment,
                "output": outcome.output,
                "file": str(destination),
            }


def _job_events(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        return host.application.job_events(
            args.job_id,
            after_sequence=args.after,
            limit=args.limit,
        )


def _job_cancel(args: argparse.Namespace) -> Any:
    with CoreHost.open(_project_config(args.project)) as host:
        return host.application.cancel_job(args.job_id)


def _print_json(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2))


def _json_object(value: str, option: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{option} must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{option} must be a JSON object")
    return parsed


def _project_config(project: Path) -> CoreHostConfig:
    return CoreHostConfig(project, enable_blender_worker=False)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
