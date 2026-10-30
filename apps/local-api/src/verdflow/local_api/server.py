"""Small loopback HTTP transport around SystemHost."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from . import request_documents, request_values, resource_paths
from verdflow.contracts import parse_assignment_preview_request

from verdflow.dts_live_connector import DtsLiveLifecycleService
from verdflow.core import StrategyAccessError
from verdflow.contracts import MaterialConfirmationMode, SourceKind
from verdflow.system_host import (
    parse_asset_search_request,
    LibraryResourceNotFoundError,
    MaterialAnalysisRequest,
    MaterialExtractionSubmission,
    PlantingBatchCommitRequest,
    AssetCatalogSaveRequest,
    ProjectResourceNotFoundError,
    ProjectSessionNotFoundError,
    RegionExtractionRequest,
    RegionSourceRequest,
    SYSTEM_API_PROTOCOL_VERSION,
    SystemHost,
    SystemHostError,
    ProjectWorkspace,
)
from verdflow.studio_service import StudioQueryService, StudioResourceNotFoundError

@dataclass(frozen=True, slots=True)
class LocalApiConfig:
    host: str = "127.0.0.1"
    port: int = 0
    request_body_limit_bytes: int = 1024 * 1024
    studio_query_timeout_seconds: float = 30.0
    management_token: str | None = None

    def validate(self) -> None:
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Local API must bind to a loopback host")
        if self.port < 0 or self.port > 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.request_body_limit_bytes <= 0:
            raise ValueError("request_body_limit_bytes must be positive")
        if self.studio_query_timeout_seconds <= 0:
            raise ValueError("studio_query_timeout_seconds must be positive")
        if self.management_token is not None:
            if (
                len(self.management_token) < 32
                or self.management_token != self.management_token.strip()
            ):
                raise ValueError(
                    "management_token must contain at least 32 characters"
                )

class LocalApiServer:
    """Own an HTTP listener while SystemHost remains caller-owned."""

    def __init__(
        self,
        system_host: SystemHost,
        config: LocalApiConfig | None = None,
        *,
        studio_queries: StudioQueryService | None = None,
        dts_live_lifecycle: DtsLiveLifecycleService | None = None,
    ) -> None:
        self.system_host = system_host
        self.config = config or LocalApiConfig()
        self.config.validate()
        self._studio_queries = studio_queries or StudioQueryService(
            timeout_seconds=self.config.studio_query_timeout_seconds
        )
        # Constructing the lifecycle controller is side-effect free. The
        # Connector and temporary CoreHost are created only by POST /start.
        self._dts_live_lifecycle = (
            dts_live_lifecycle or DtsLiveLifecycleService(system_host)
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._endpoint_id = str(uuid4())

    def __enter__(self) -> LocalApiServer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Local API is not running")
        host, port = self._server.server_address[:2]
        display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        return f"http://{display_host}:{port}"

    def start(self) -> str:
        if self._server is not None:
            return self.base_url
        if self.system_host.state.value != "running":
            raise SystemHostError("SystemHost must be running before Local API starts")
        handler = self._handler_type()
        server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        server.daemon_threads = True
        self._server = server
        self._thread = Thread(
            target=server.serve_forever,
            name="verdflow-local-api",
            daemon=True,
        )
        self._thread.start()
        try:
            self.system_host.publish_endpoint(
                endpoint_id=self._endpoint_id,
                base_url=self.base_url,
                protocol_version=SYSTEM_API_PROTOCOL_VERSION,
            )
        except BaseException:
            server.shutdown()
            server.server_close()
            self._thread.join(timeout=5.0)
            self._server = None
            self._thread = None
            self._studio_queries.close()
            raise
        return self.base_url

    def close(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            self._dts_live_lifecycle.close()
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
        self._server = None
        self._thread = None
        self._studio_queries.close()
        self._dts_live_lifecycle.close()
        self.system_host.clear_endpoint(self._endpoint_id)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        system_host = self.system_host
        studio_queries = self._studio_queries
        dts_live_lifecycle = self._dts_live_lifecycle
        body_limit = self.config.request_body_limit_bytes
        management_token = self.config.management_token

        class Handler(BaseHTTPRequestHandler):
            server_version = "VerdFlowLocalApi/0.1"

            def do_GET(self) -> None:
                target = urlsplit(self.path)
                path = target.path.rstrip("/") or "/"
                try:
                    if path == "/api/v1/health":
                        self._send_json(HTTPStatus.OK, asdict(system_host.status()))
                        return
                    if path == "/api/v1/starter-resources":
                        self._require_management()
                        self._send_json(HTTPStatus.OK, asdict(system_host.starter_resource_manifest()))
                        return
                    if path == "/api/v1/starter-resources/import":
                        self._require_management()
                        query = parse_qs(target.query, keep_blank_values=True)
                        operation_id = request_values.query_required_text(query, "operation_id")
                        self._send_json(HTTPStatus.OK, asdict(system_host.get_starter_import(operation_id)))
                        return
                    preview_id = resource_paths.strategy_preview_resource(path)
                    if preview_id is not None:
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(system_host.get_strategy_preview(preview_id)),
                        )
                        return
                    if path == "/api/v1/projects":
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_projects()
                                ]
                            },
                        )
                        return
                    if path == "/api/v1/recent-projects":
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_recent_projects()
                                ]
                            },
                        )
                        return
                    if path == "/api/v1/model-providers":
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_model_providers()
                                ]
                            },
                        )
                        return
                    if path == "/api/v1/external-runtimes":
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_external_runtimes()
                                ]
                            },
                        )
                        return
                    if path == "/api/v1/dts-live/status":
                        self._require_management()
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(dts_live_lifecycle.status()),
                        )
                        return
                    if path == "/api/v1/strategies":
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_strategies()
                                ]
                            },
                        )
                        return
                    strategy_hash = resource_paths.strategy_resource(path)
                    if strategy_hash is not None:
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(system_host.get_strategy(strategy_hash)),
                        )
                        return
                    if path == "/api/v1/asset-catalogs":
                        query = parse_qs(target.query, keep_blank_values=True)
                        target_type = request_values.query_required_text(
                            query,
                            "target_type",
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "target_type": target_type,
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_asset_catalogs(
                                        target_type
                                    )
                                ],
                            },
                        )
                        return
                    catalog_detail = resource_paths.catalog_resource(path)
                    if catalog_detail is not None:
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(
                                system_host.get_asset_catalog(*catalog_detail)
                            ),
                        )
                        return
                    library_resource_id = resource_paths.library_resource(path)
                    if library_resource_id is not None:
                        resource, content = system_host.read_library_resource(
                            library_resource_id
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                **asdict(resource),
                                "data_base64": base64.b64encode(content).decode(
                                    "ascii"
                                ),
                            },
                        )
                        return
                    catalog_resource = resource_paths.catalog_asset_resource(path)
                    if catalog_resource is not None:
                        query = parse_qs(target.query, keep_blank_values=True)
                        result = system_host.get_asset_catalog_assets(
                            *catalog_resource,
                            after_ordinal=request_values.query_integer(
                                query,
                                "after_ordinal",
                                default=-1,
                                minimum=-1,
                            ),
                            limit=request_values.query_integer(
                                query,
                                "limit",
                                default=200,
                                minimum=1,
                                maximum=1000,
                            ),
                            collection_id=request_values.query_optional_text(
                                query,
                                "collection_id",
                            ),
                            query=request_values.query_optional_text(query, "query"),
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            result if isinstance(result, dict) else asdict(result),
                        )
                        return
                    if path == "/api/v1/role-binding-profiles":
                        query = parse_qs(target.query, keep_blank_values=True)
                        target_type = request_values.query_required_text(
                            query,
                            "target_type",
                        )
                        result = system_host.list_role_binding_profiles(
                            target_type,
                            strategy_id=request_values.query_optional_text(
                                query,
                                "strategy_id",
                            ),
                            strategy_version=request_values.query_optional_text(
                                query,
                                "strategy_version",
                            ),
                            catalog_id=request_values.query_optional_text(
                                query,
                                "catalog_id",
                            ),
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "target_type": target_type,
                                "items": [asdict(item) for item in result],
                            },
                        )
                        return
                    profile_resource = resource_paths.profile_resource(path)
                    if profile_resource is not None:
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(
                                system_host.get_role_binding_profile(profile_resource)
                            ),
                        )
                        return
                    material_candidate = resource_paths.project_action_resource(
                        path,
                        "material-analyses",
                        "candidate",
                    )
                    if material_candidate is not None:
                        session_id, job_id = material_candidate
                        candidate = system_host.get_material_candidate(
                            session_id,
                            job_id,
                        )
                        self._send_json(HTTPStatus.OK, candidate.to_document())
                        return
                    material_facts = resource_paths.project_action_resource(
                        path,
                        "scene-revisions",
                        "material-facts",
                    )
                    if material_facts is not None:
                        result = system_host.get_material_facts(
                            material_facts[0],
                            material_facts[1],
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    material_selection_session = resource_paths.project_collection(
                        path,
                        "current-material-selection",
                    )
                    if material_selection_session is not None:
                        result = system_host.get_current_material_selection(
                            material_selection_session
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {"materials": []}
                            if result is None
                            else asdict(result),
                        )
                        return
                    viewer_resource = resource_paths.project_viewer_resource(path)
                    if viewer_resource is not None:
                        session_id, kind, resource_id, action = viewer_resource
                        query = parse_qs(target.query, keep_blank_values=True)
                        project = system_host.get_project(session_id)
                        project_path = Path(project.project_path)
                        if kind == "region-sets" and action == "viewer":
                            result = studio_queries.get_region_manifest(
                                session_id,
                                project_path,
                                resource_id,
                            )
                        elif kind == "region-sets" and action == "geometry":
                            bounds = request_values.viewport_query(query)
                            result = studio_queries.get_region_chunk(
                                session_id,
                                project_path,
                                resource_id,
                                **bounds,
                                classifications=request_values.query_values(
                                    query,
                                    "classification",
                                ),
                                after_ordinal=request_values.query_integer(
                                    query,
                                    "after_ordinal",
                                    default=-1,
                                    minimum=-1,
                                ),
                                limit=request_values.query_integer(
                                    query,
                                    "limit",
                                    default=1000,
                                    minimum=1,
                                    maximum=5000,
                                ),
                                simplification_tolerance=(
                                    request_values.query_nonnegative_float(
                                        query,
                                        "simplification_tolerance",
                                        default=0.0,
                                    )
                                ),
                            )
                        elif kind == "point-sets" and action == "review":
                            result = studio_queries.get_point_manifest(
                                session_id,
                                project_path,
                                resource_id,
                                request_values.query_required_text(
                                    query,
                                    "assignment_set_id",
                                ),
                            )
                        elif kind == "point-sets" and action == "points":
                            bounds = request_values.viewport_query(query)
                            result = studio_queries.get_point_chunk(
                                session_id,
                                project_path,
                                resource_id,
                                request_values.query_required_text(
                                    query,
                                    "assignment_set_id",
                                ),
                                **bounds,
                                roles=request_values.query_values(query, "role"),
                                assets=request_values.query_values(query, "asset"),
                                after_ordinal=request_values.query_integer(
                                    query,
                                    "after_ordinal",
                                    default=-1,
                                    minimum=-1,
                                ),
                                limit=request_values.query_integer(
                                    query,
                                    "limit",
                                    default=5000,
                                    minimum=1,
                                    maximum=20000,
                                ),
                            )
                        else:
                            self._send_error(
                                HTTPStatus.NOT_FOUND,
                                "route_not_found",
                                path,
                            )
                            return
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    resource = resource_paths.project_resource(path)
                    if resource is not None:
                        session_id, kind, resource_id, action = resource
                        if kind == "jobs" and action is None:
                            result: object = system_host.get_job(
                                session_id,
                                resource_id,
                            )
                        elif kind == "jobs" and action == "events":
                            query = parse_qs(target.query, keep_blank_values=True)
                            result = {
                                "items": [
                                    asdict(item)
                                    for item in system_host.get_job_events(
                                        session_id,
                                        resource_id,
                                        after_sequence=request_values.query_integer(
                                            query,
                                            "after_sequence",
                                            default=0,
                                            minimum=0,
                                        ),
                                        limit=request_values.query_integer(
                                            query,
                                            "limit",
                                            default=100,
                                            minimum=1,
                                            maximum=1000,
                                        ),
                                    )
                                ]
                            }
                        elif kind == "jobs" and action == "attempts":
                            result = {
                                "items": [
                                    asdict(item)
                                    for item in system_host.get_job_attempts(
                                        session_id,
                                        resource_id,
                                    )
                                ]
                            }
                        elif kind == "artifacts" and action is None:
                            result = system_host.get_artifact(
                                session_id,
                                resource_id,
                            )
                        elif kind == "exports" and action is None:
                            result = system_host.get_export_download(
                                session_id,
                                resource_id,
                            )
                        elif kind == "exports" and action == "content":
                            self._send_export_content(
                                session_id,
                                resource_id,
                            )
                            return
                        elif kind == "plant-runs" and action is None:
                            result = system_host.get_plant_run_execution(
                                session_id,
                                resource_id,
                            )
                        elif kind == "plant-previews" and action is None:
                            result = system_host.get_plant_preview(
                                session_id,
                                resource_id,
                            )
                        elif kind == "plant-previews" and action == "input":
                            result = system_host.get_plant_preview_input(
                                session_id,
                                resource_id,
                            )
                        elif kind == "plant-previews" and action == "export":
                            result = system_host.get_plant_preview_export(
                                session_id,
                                resource_id,
                            )
                        elif kind == "plant-previews" and action == "re-export":
                            query = parse_qs(
                                target.query,
                                keep_blank_values=True,
                            )
                            result = system_host.get_plant_preview_reexport(
                                session_id,
                                resource_id,
                                request_values.query_required_text(
                                    query,
                                    "export_job_id",
                                ),
                            )
                        elif kind == "plant-exports" and action is None:
                            result = system_host.get_plant_export(
                                session_id,
                                resource_id,
                            )
                        elif (
                            kind == "planting-composition-exports"
                            and action is None
                        ):
                            if resource_id == "active":
                                operation = system_host.get_active_planting_composition_export(
                                    session_id
                                )
                                self._send_json(
                                    HTTPStatus.OK,
                                    {
                                        "operation": (
                                            asdict(operation)
                                            if operation is not None
                                            else None
                                        )
                                    },
                                )
                                return
                            result = system_host.get_planting_composition_export(
                                session_id,
                                resource_id,
                            )
                        elif kind == "material-flows" and action is None:
                            result = system_host.get_material_flow_execution(
                                session_id,
                                resource_id,
                            )
                        elif kind == "scene-preparations" and action is None:
                            result = system_host.get_scene_preparation(
                                session_id,
                                resource_id,
                            )
                        else:
                            self._send_error(
                                HTTPStatus.NOT_FOUND,
                                "route_not_found",
                                path,
                            )
                            return
                        payload = (
                            result
                            if isinstance(result, dict)
                            else asdict(result)
                        )
                        self._send_json(HTTPStatus.OK, payload)
                        return
                    project_collection = resource_paths.project_named_collection(path)
                    if project_collection is not None:
                        session_id, kind = project_collection
                        query = parse_qs(target.query, keep_blank_values=True)
                        if kind == "tasks":
                            statuses = request_values.query_statuses(query)
                            result = system_host.list_project_tasks(
                                session_id,
                                statuses=statuses,
                                before=request_values.query_optional_text(query, "before"),
                                limit=request_values.query_integer(
                                    query,
                                    "limit",
                                    default=50,
                                    minimum=1,
                                    maximum=200,
                                ),
                            )
                        elif kind == "summary":
                            result = system_host.get_project_business_summary(
                                session_id
                            )
                        elif kind == "planting-composition":
                            composition = (
                                system_host.get_current_planting_composition(
                                    session_id
                                )
                            )
                            result = {
                                "composition": (
                                    asdict(composition)
                                    if composition is not None
                                    else None
                                )
                            }
                        elif kind == "xml-outputs":
                            result = {
                                "items": [
                                    asdict(item)
                                    for item in system_host.list_project_xml_outputs(
                                        session_id,
                                        limit=request_values.query_integer(
                                            query,
                                            "limit",
                                            default=200,
                                            minimum=1,
                                            maximum=1000,
                                        ),
                                    )
                                ]
                            }
                        else:
                            self._send_error(
                                HTTPStatus.NOT_FOUND,
                                "route_not_found",
                                path,
                            )
                            return
                        self._send_json(
                            HTTPStatus.OK,
                            result if isinstance(result, dict) else asdict(result),
                        )
                        return
                    self._send_error(HTTPStatus.NOT_FOUND, "route_not_found", path)
                except Exception as error:
                    self._handle_error(error)

            def do_POST(self) -> None:
                path = urlsplit(self.path).path.rstrip("/") or "/"
                try:
                    if path == "/api/v1/resource-imports/prepare":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.prepare_resource_import(
                            request_values.bounded_text_list(document, "paths", limit=32),
                            recommended=request_values.optional_boolean(document, "recommended", default=False),
                            strategy_paths=request_values.bounded_text_list(document, "strategy_paths", limit=8),
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if path == "/api/v1/resource-imports/confirm":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.confirm_resource_import(
                            request_values.required_text(document, "operation_id"),
                            optional_ids=request_values.bounded_text_list(document, "optional_ids"),
                            overwrite_ids=request_values.bounded_text_list(document, "overwrite_ids"),
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if path == "/api/v1/asset-search":
                        self._require_management()
                        result = system_host.search_asset_catalogs(parse_asset_search_request(self._read_json()))
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    if path == "/api/v1/starter-resources/import":
                        self._require_management()
                        revision = request_values.required_text(self._read_json(), "revision")
                        self._send_json(HTTPStatus.ACCEPTED, asdict(system_host.start_starter_import(revision)))
                        return
                    if path == "/api/v1/asset-assignment-previews":
                        self._require_management()
                        result = system_host.preview_asset_assignments(
                            parse_assignment_preview_request(self._read_json(limit_multiplier=8))
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    if path == "/api/v1/strategy-previews":
                        self._require_management()
                        request = request_documents.strategy_preview_request(
                            self._read_json()
                        )
                        self._send_json(
                            HTTPStatus.ACCEPTED,
                            asdict(system_host.submit_strategy_preview(request)),
                        )
                        return
                    if path == "/api/v1/dts-live/start":
                        self._require_management()
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(dts_live_lifecycle.start()),
                        )
                        return
                    if path == "/api/v1/dts-live/relaunch":
                        self._require_management()
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(dts_live_lifecycle.relaunch()),
                        )
                        return
                    if path == "/api/v1/dts-live/stop":
                        self._require_management()
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(dts_live_lifecycle.stop()),
                        )
                        return
                    if path == "/api/v1/strategies/import":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.install_strategy(
                            Path(
                                request_values.required_text(
                                    document,
                                    "source_path",
                                )
                            )
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    if path == "/api/v1/strategy-packages/import":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.import_strategy_package(
                            Path(request_values.required_text(document, "source_path"))
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    if path == "/api/v1/asset-catalog-packages/import":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.import_asset_catalog_package(
                            Path(request_values.required_text(document, "source_path")),
                            replace_existing=request_values.optional_boolean(
                                document,
                                "replace_existing",
                                default=False,
                            ),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    if path == "/api/v1/role-binding-profile-packages/import":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.import_role_binding_profile_package(
                            Path(request_values.required_text(document, "source_path"))
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    if path == "/api/v1/library-resources/import":
                        self._require_management()
                        document = self._read_json()
                        result = system_host.import_library_thumbnail(
                            Path(request_values.required_text(document, "source_path"))
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    catalog_export = resource_paths.catalog_package_export_resource(path)
                    if catalog_export is not None:
                        self._require_management()
                        document = self._read_json()
                        target_type, catalog_id = catalog_export
                        result = system_host.export_asset_catalog_package(
                            target_type,
                            catalog_id,
                            Path(
                                request_values.required_text(
                                    document,
                                    "destination_path",
                                )
                            ),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    profile_export = resource_paths.profile_package_export_resource(path)
                    if profile_export is not None:
                        self._require_management()
                        document = self._read_json()
                        result = system_host.export_role_binding_profile_package(
                            profile_export,
                            Path(
                                request_values.required_text(
                                    document,
                                    "destination_path",
                                )
                            ),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    strategy_export = resource_paths.strategy_package_export_resource(path)
                    if strategy_export is not None:
                        self._require_management()
                        document = self._read_json()
                        result = system_host.export_strategy_package(
                            strategy_export,
                            Path(
                                request_values.required_text(
                                    document,
                                    "destination_path",
                                )
                            ),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    authoring_kit = resource_paths.authoring_kit_export_resource(path)
                    if authoring_kit is not None:
                        self._require_management()
                        document = self._read_json()
                        result = system_host.export_authoring_kit(
                            authoring_kit,
                            Path(
                                request_values.required_text(
                                    document,
                                    "destination_path",
                                )
                            ),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    strategy_action = resource_paths.strategy_action_resource(path)
                    if strategy_action is not None:
                        self._require_management()
                        document = self._read_json()
                        package_hash, action = strategy_action
                        reason = request_values.optional_text(document, "reason")
                        if action == "trust":
                            result = system_host.trust_strategy(
                                package_hash,
                                reason=reason,
                            )
                        else:
                            result = system_host.revoke_strategy_trust(
                                package_hash,
                                reason=reason,
                            )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    if path == "/api/v1/role-binding-profiles":
                        self._require_management()
                        result = system_host.save_role_binding_profile(
                            request_documents.role_binding_profile_save_request(
                                self._read_json()
                            )
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    profile_sync = resource_paths.profile_action_resource(
                        path,
                        "sync-catalog-metadata",
                    )
                    if profile_sync is not None:
                        self._require_management()
                        document = self._read_json()
                        result = system_host.sync_role_binding_profile_assets(
                            profile_sync,
                            expected_generation=request_values.required_integer(
                                document,
                                "expected_generation",
                                minimum=0,
                            ),
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    provider_models = resource_paths.provider_action_resource(
                        path,
                        "models",
                    )
                    if provider_models is not None:
                        self._require_management()
                        self._read_json()
                        result = system_host.list_model_provider_models(
                            provider_models
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    provider_test = resource_paths.provider_action_resource(
                        path,
                        "test",
                    )
                    if provider_test is not None:
                        self._require_management()
                        document = self._read_json()
                        result = system_host.test_model_provider(
                            provider_test,
                            model=request_values.optional_text(document, "model"),
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    source_session = resource_paths.project_collection(path, "sources")
                    if source_session is not None:
                        self._require_management()
                        document = self._read_json()
                        kind_value = document.get("kind", SourceKind.FBX.value)
                        if not isinstance(kind_value, str):
                            raise ValueError("kind must be a SourceKind value")
                        result = system_host.add_source_file(
                            source_session,
                            Path(request_values.required_text(document, "source_path")),
                            kind=SourceKind(kind_value),
                            media_type=request_values.optional_text(
                                document,
                                "media_type",
                            ),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    assembly_session = resource_paths.project_collection(
                        path,
                        "source-assemblies",
                    )
                    if assembly_session is not None:
                        self._require_management()
                        document = self._read_json()
                        source_ids = document.get("source_ids")
                        if (
                            not isinstance(source_ids, list)
                            or not source_ids
                            or not all(
                                isinstance(item, str) and item.strip()
                                for item in source_ids
                            )
                        ):
                            raise ValueError(
                                "source_ids must be a non-empty string array"
                            )
                        result = system_host.create_identity_source_assembly(
                            assembly_session,
                            original_name=request_values.required_text(
                                document,
                                "original_name",
                            ),
                            source_ids=tuple(item.strip() for item in source_ids),
                        )
                        self._send_json(HTTPStatus.CREATED, asdict(result))
                        return
                    preparation_session = resource_paths.project_collection(
                        path,
                        "scene-preparations",
                    )
                    if preparation_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        result = system_host.submit_scene_preparation(
                            preparation_session,
                            request_documents.scene_preparation_request(document),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    material_session = resource_paths.project_collection(
                        path,
                        "material-analyses",
                    )
                    if material_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        mode_value = document.get(
                            "confirmation_mode",
                            MaterialConfirmationMode.MANUAL.value,
                        )
                        if not isinstance(mode_value, str):
                            raise ValueError(
                                "confirmation_mode must be manual or automatic"
                            )
                        result = system_host.submit_material_analysis(
                            material_session,
                            MaterialAnalysisRequest(
                                scene_revision_id=request_values.required_text(
                                    document,
                                    "scene_revision_id",
                                ),
                                provider_id=request_values.required_text(
                                    document,
                                    "provider_id",
                                ),
                                model=request_values.optional_text(document, "model"),
                                confirmation_mode=MaterialConfirmationMode(
                                    mode_value
                                ),
                            ),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    material_confirm = resource_paths.project_action_resource(
                        path,
                        "material-analyses",
                        "confirm",
                    )
                    if material_confirm is not None:
                        document = self._read_json()
                        import_settings = document.get("import_settings", {})
                        allow_contains = document.get("allow_contains", False)
                        if not isinstance(import_settings, dict):
                            raise ValueError(
                                "import_settings must be a JSON object"
                            )
                        if not isinstance(allow_contains, bool):
                            raise ValueError("allow_contains must be a boolean")
                        result = (
                            system_host.confirm_materials_and_submit_extraction(
                                material_confirm[0],
                                material_confirm[1],
                                region_provider_id=request_values.required_text(
                                    document,
                                    "region_provider_id",
                                ),
                                allow_contains=allow_contains,
                                import_settings=import_settings,
                            )
                        )
                        self._send_json(
                            HTTPStatus.ACCEPTED,
                            self._material_submission_document(result),
                        )
                        return
                    region_session = resource_paths.project_collection(
                        path,
                        "region-extractions",
                    )
                    if region_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        rule_pack = document.get("rule_pack")
                        import_settings = document.get("import_settings", {})
                        allow_contains = document.get("allow_contains", False)
                        if not isinstance(rule_pack, dict):
                            raise ValueError("rule_pack must be a JSON object")
                        if not isinstance(import_settings, dict):
                            raise ValueError(
                                "import_settings must be a JSON object"
                            )
                        if not isinstance(allow_contains, bool):
                            raise ValueError("allow_contains must be a boolean")
                        result = system_host.submit_region_extraction(
                            region_session,
                            RegionExtractionRequest(
                                scene_revision_id=request_values.required_text(
                                    document,
                                    "scene_revision_id",
                                ),
                                provider_id=request_values.required_text(
                                    document,
                                    "provider_id",
                                ),
                                rule_pack=rule_pack,
                                allow_contains=allow_contains,
                                import_settings=import_settings,
                            ),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    region_finalize = resource_paths.project_action_resource(
                        path,
                        "region-extractions",
                        "finalize",
                    )
                    if region_finalize is not None:
                        result = system_host.finalize_region_extraction(
                            region_finalize[0],
                            region_finalize[1],
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    region_source_session = resource_paths.project_collection(
                        path,
                        "region-sources",
                    )
                    if region_source_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        options = document.get("options", {})
                        if not isinstance(options, dict):
                            raise ValueError("options must be a JSON object")
                        result = system_host.submit_region_source(
                            region_source_session,
                            RegionSourceRequest(
                                source_id=request_values.required_text(
                                    document,
                                    "source_id",
                                ),
                                provider_id=request_values.required_text(
                                    document,
                                    "provider_id",
                                ),
                                options=options,
                            ),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    region_source_finalize = resource_paths.project_action_resource(
                        path,
                        "region-sources",
                        "finalize",
                    )
                    if region_source_finalize is not None:
                        scene, regions = system_host.finalize_region_source(
                            region_source_finalize[0],
                            region_source_finalize[1],
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "scene_revision": asdict(scene),
                                "region_set": asdict(regions),
                            },
                        )
                        return
                    plant_run_session = resource_paths.plant_run_collection(path)
                    if plant_run_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        result = system_host.submit_plant_run_execution(
                            plant_run_session,
                            request_documents.plant_run_request(document),
                            idempotency_key=(
                                idempotency_key
                            ),
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    preview_session = resource_paths.project_collection(
                        path,
                        "plant-previews",
                    )
                    if preview_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        result = system_host.submit_plant_preview(
                            preview_session,
                            request_documents.plant_preview_request(document),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    export_session = resource_paths.project_collection(
                        path,
                        "plant-exports",
                    )
                    if export_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        result = system_host.submit_plant_export(
                            export_session,
                            request_documents.plant_export_request(document),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    composition_export_session = resource_paths.project_collection(
                        path,
                        "planting-composition-exports",
                    )
                    if composition_export_session is not None:
                        document = self._read_json()
                        result = system_host.submit_planting_composition_export(
                            composition_export_session,
                            request_documents.planting_composition_export_request(document),
                        )
                        self._send_json(
                            HTTPStatus.ACCEPTED,
                            asdict(result),
                        )
                        return
                    resource = resource_paths.project_resource(path)
                    if (
                        resource is not None
                        and resource[1] == "plant-runs"
                        and resource[3] == "confirm"
                    ):
                        document = self._read_json()
                        result = system_host.confirm_plant_run_execution(
                            resource[0],
                            resource[2],
                            request_documents.plant_run_continuation(document),
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if (
                        resource is not None
                        and resource[1] == "plant-previews"
                        and resource[3] == "retry-assignment"
                    ):
                        document = self._read_json()
                        request_values.reject_unknown(document)
                        result = system_host.retry_plant_run_assignment(
                            resource[0],
                            resource[2],
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if (
                        resource is not None
                        and resource[1] == "plant-previews"
                        and resource[3] == "commit"
                    ):
                        document = self._read_json()
                        selected = document.pop("selected_asset_ids", None)
                        if selected is not None and not isinstance(selected, list):
                            raise ValueError(
                                "selected_asset_ids must be an array or null"
                            )
                        continue_planting = document.pop(
                            "continue_planting", False
                        )
                        if not isinstance(continue_planting, bool):
                            raise ValueError(
                                "continue_planting must be a boolean"
                            )
                        replacing_batch_id = document.pop(
                            "replacing_batch_id",
                            None,
                        )
                        if replacing_batch_id is not None and (
                            not isinstance(replacing_batch_id, str)
                            or not replacing_batch_id.strip()
                        ):
                            raise ValueError(
                                "replacing_batch_id must be a non-empty string"
                            )
                        if replacing_batch_id is not None:
                            replacing_batch_id = replacing_batch_id.strip()
                        request_values.reject_unknown(document)
                        result = system_host.commit_plant_preview_batch(
                            resource[0],
                            resource[2],
                            PlantingBatchCommitRequest(
                                selected_asset_ids=(
                                    tuple(selected)
                                    if selected is not None
                                    else None
                                ),
                                continue_planting=continue_planting,
                                replacing_batch_id=replacing_batch_id,
                            ),
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    if (
                        resource is not None
                        and resource[1] == "plant-previews"
                        and resource[3] == "export"
                    ):
                        document = self._read_json()
                        result = system_host.export_plant_preview(
                            resource[0],
                            resource[2],
                            request_documents.plant_preview_output_request(document),
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if (
                        resource is not None
                        and resource[1] == "plant-previews"
                        and resource[3] == "re-export"
                    ):
                        document = self._read_json()
                        result = system_host.submit_plant_preview_reexport(
                            resource[0],
                            resource[2],
                            request_documents.plant_preview_output_request(document),
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    material_flow_session = resource_paths.project_collection(
                        path,
                        "material-flows",
                    )
                    if material_flow_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        result = system_host.submit_material_flow_execution(
                            material_flow_session,
                            request_documents.material_flow_request(document),
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    material_revision_session = resource_paths.project_collection(
                        path,
                        "material-revisions",
                    )
                    if material_revision_session is not None:
                        document = self._read_json()
                        idempotency_key = request_values.pop_idempotency_key(document)
                        selection = request_documents.material_selection(document)
                        if selection is None:
                            raise ValueError("materials are required")
                        result = system_host.revise_current_materials(
                            material_revision_session,
                            selection,
                            idempotency_key=idempotency_key,
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    resource = resource_paths.project_resource(path)
                    if (
                        resource is not None
                        and resource[1] == "scene-preparations"
                        and resource[3] == "confirm"
                    ):
                        document = self._read_json()
                        selection = request_documents.material_selection(document)
                        result = (
                            system_host.confirm_scene_preparation(
                                resource[0],
                                resource[2],
                                selection,
                            )
                            if selection is not None
                            else system_host.confirm_scene_preparation(
                                resource[0],
                                resource[2],
                            )
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if (
                        resource is not None
                        and resource[1] == "scene-preparations"
                        and resource[3] == "cancel"
                    ):
                        self._require_management()
                        result = system_host.request_scene_preparation_cancel(
                            resource[0],
                            resource[2],
                        )
                        self._send_json(
                            HTTPStatus.ACCEPTED if result.accepted else HTTPStatus.OK,
                            asdict(result),
                        )
                        return
                    if (
                        resource is not None
                        and resource[1] == "material-flows"
                        and resource[3] == "confirm"
                    ):
                        document = self._read_json()
                        selection = request_documents.material_selection(document)
                        result = (
                            system_host.confirm_material_flow_execution(
                                resource[0],
                                resource[2],
                                selection,
                            )
                            if selection is not None
                            else system_host.confirm_material_flow_execution(
                                resource[0],
                                resource[2],
                            )
                        )
                        self._send_json(HTTPStatus.ACCEPTED, asdict(result))
                        return
                    if (
                        resource is not None
                        and resource[1] == "material-flows"
                        and resource[3] == "cancel"
                    ):
                        self._require_management()
                        result = system_host.request_material_flow_cancel(
                            resource[0],
                            resource[2],
                        )
                        self._send_json(
                            (
                                HTTPStatus.ACCEPTED
                                if result.accepted
                                else HTTPStatus.OK
                            ),
                            asdict(result),
                        )
                        return
                    if (
                        resource is not None
                        and resource[1] == "plant-runs"
                        and resource[3] == "cancel"
                    ):
                        self._require_management()
                        result = system_host.request_plant_run_cancel(
                            resource[0],
                            resource[2],
                        )
                        self._send_json(
                            (
                                HTTPStatus.ACCEPTED
                                if result.accepted
                                else HTTPStatus.OK
                            ),
                            asdict(result),
                        )
                        return
                    if (
                        resource is not None
                        and resource[1] == "jobs"
                        and resource[3] == "cancel"
                    ):
                        self._require_management()
                        result = system_host.request_job_cancel(
                            resource[0],
                            resource[2],
                        )
                        self._send_json(
                            (
                                HTTPStatus.ACCEPTED
                                if result.accepted
                                else HTTPStatus.OK
                            ),
                            asdict(result),
                        )
                        return
                    if path != "/api/v1/projects":
                        self._send_error(HTTPStatus.NOT_FOUND, "route_not_found", path)
                        return
                    document = self._read_json()
                    action = request_values.required_text(document, "action")
                    project_path = Path(request_values.required_text(document, "project_path"))
                    enable_blender = document.get("enable_blender_worker", True)
                    if not isinstance(enable_blender, bool):
                        raise ValueError("enable_blender_worker must be a boolean")
                    enable_geospatial = document.get(
                        "enable_geospatial_worker",
                        False,
                    )
                    if not isinstance(enable_geospatial, bool):
                        raise ValueError(
                            "enable_geospatial_worker must be a boolean"
                        )
                    blender_value = document.get("blender_executable")
                    blender = (
                        Path(blender_value)
                        if isinstance(blender_value, str)
                        else None
                    )
                    enable_model_api = document.get(
                        "enable_model_api_worker",
                        False,
                    )
                    if not isinstance(enable_model_api, bool):
                        raise ValueError(
                            "enable_model_api_worker must be a boolean"
                        )
                    workspace_value = document.get("workspace")
                    workspace = (
                        ProjectWorkspace(workspace_value)
                        if workspace_value is not None
                        else None
                    )
                    if action == "create":
                        result = system_host.create_project(
                            project_path,
                            project_name=request_values.required_text(document, "project_name"),
                            enable_blender_worker=enable_blender,
                            enable_geospatial_worker=enable_geospatial,
                            blender_executable=blender,
                            enable_model_api_worker=enable_model_api,
                            workspace=workspace,
                        )
                        status = HTTPStatus.CREATED
                    elif action == "open":
                        result = system_host.open_project(
                            project_path,
                            enable_blender_worker=enable_blender,
                            enable_geospatial_worker=enable_geospatial,
                            blender_executable=blender,
                            enable_model_api_worker=enable_model_api,
                            workspace=workspace,
                        )
                        status = HTTPStatus.OK
                    else:
                        raise ValueError("action must be 'create' or 'open'")
                    self._send_json(status, asdict(result))
                except Exception as error:
                    self._handle_error(error)

            def do_PUT(self) -> None:
                path = urlsplit(self.path).path.rstrip("/")
                prefix = "/api/v1/model-providers/"
                try:
                    catalog = resource_paths.catalog_resource(path)
                    if catalog is not None:
                        self._require_management()
                        document = self._read_json()
                        catalog_document = document.get("document")
                        if not isinstance(catalog_document, dict):
                            raise ValueError("document must be an object")
                        if (
                            catalog_document.get("target_type") != catalog[0]
                            or catalog_document.get("catalog_id") != catalog[1]
                        ):
                            raise ValueError(
                                "Catalog identity differs from request path"
                            )
                        result = system_host.save_asset_catalog(
                            AssetCatalogSaveRequest(
                                document=dict(catalog_document),
                                expected_generation=request_values.required_integer(
                                    document,
                                    "expected_generation",
                                    minimum=0,
                                ),
                            )
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    runtime_prefix = "/api/v1/external-runtimes/"
                    if path.startswith(runtime_prefix):
                        self._require_management()
                        runtime_id = unquote(path[len(runtime_prefix) :])
                        if not runtime_id:
                            raise ValueError("runtime_id is required")
                        document = self._read_json()
                        result = system_host.configure_external_runtime(
                            runtime_id,
                            request_values.required_text(document, "path"),
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    if not path.startswith(prefix):
                        self._send_error(
                            HTTPStatus.NOT_FOUND,
                            "route_not_found",
                            path,
                        )
                        return
                    self._require_management()
                    suffix = unquote(path[len(prefix) :])
                    if suffix.endswith("/secret"):
                        provider_id = suffix[: -len("/secret")]
                        if not provider_id:
                            raise ValueError("provider_id is required")
                        document = self._read_json()
                        result = system_host.set_model_provider_secret(
                            provider_id,
                            request_values.required_text(document, "api_key"),
                        )
                    elif suffix.endswith("/model"):
                        provider_id = suffix[: -len("/model")]
                        if not provider_id:
                            raise ValueError("provider_id is required")
                        document = self._read_json()
                        result = system_host.set_model_provider_model(
                            provider_id,
                            request_values.required_text(document, "model"),
                        )
                    else:
                        provider_id = suffix
                        if not provider_id:
                            raise ValueError("provider_id is required")
                        document = self._read_json()
                        supplied_id = document.get("provider_id")
                        if supplied_id is not None and supplied_id != provider_id:
                            raise ValueError(
                                "provider_id differs from request path"
                            )
                        document["provider_id"] = provider_id
                        result = system_host.upsert_model_provider(document)
                    self._send_json(HTTPStatus.OK, asdict(result))
                except Exception as error:
                    self._handle_error(error)

            def do_DELETE(self) -> None:
                target = urlsplit(self.path)
                path = target.path.rstrip("/")
                try:
                    if path == "/api/v1/starter-resources/import":
                        self._require_management()
                        operation_id = request_values.required_text(self._read_json(), "operation_id")
                        self._send_json(HTTPStatus.OK, asdict(system_host.cancel_starter_import(operation_id)))
                        return
                    preview_id = resource_paths.strategy_preview_resource(path)
                    if preview_id is not None:
                        self._require_management()
                        self._send_json(
                            HTTPStatus.OK,
                            asdict(system_host.cancel_strategy_preview(preview_id)),
                        )
                        return
                    recent_project_prefix = "/api/v1/recent-projects/"
                    if path.startswith(recent_project_prefix):
                        self._require_management()
                        registry_id = unquote(
                            path[len(recent_project_prefix) :]
                        )
                        if not registry_id:
                            raise ValueError("registry_id is required")
                        recent = next(
                            (
                                item
                                for item in system_host.list_recent_projects()
                                if item.registry_id == registry_id
                            ),
                            None,
                        )
                        if recent is not None and recent.active_session_id:
                            studio_queries.close_project(
                                recent.active_session_id
                            )
                        result = system_host.delete_project(registry_id)
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    runtime_prefix = "/api/v1/external-runtimes/"
                    if path.startswith(runtime_prefix):
                        self._require_management()
                        runtime_id = unquote(path[len(runtime_prefix) :])
                        if not runtime_id:
                            raise ValueError("runtime_id is required")
                        result = system_host.clear_external_runtime(runtime_id)
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    provider_prefix = "/api/v1/model-providers/"
                    if path.startswith(provider_prefix):
                        self._require_management()
                        suffix = unquote(path[len(provider_prefix) :])
                        if suffix.endswith("/secret"):
                            provider_id = suffix[: -len("/secret")]
                            result = system_host.delete_model_provider_secret(
                                provider_id
                            )
                        else:
                            result = system_host.delete_model_provider(suffix)
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    strategy_hash = resource_paths.strategy_resource(path)
                    if strategy_hash is not None:
                        self._require_management()
                        result = system_host.uninstall_strategy(strategy_hash)
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    catalog = resource_paths.catalog_resource(path)
                    if catalog is not None:
                        self._require_management()
                        result = system_host.delete_asset_catalog(*catalog)
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    profile_id = resource_paths.profile_resource(path)
                    if profile_id is not None:
                        self._require_management()
                        result = system_host.delete_role_binding_profile(
                            profile_id
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    resource = resource_paths.project_resource(path)
                    if (
                        resource is not None
                        and resource[1] == "plant-previews"
                        and resource[3] is None
                    ):
                        result = system_host.discard_plant_preview(
                            resource[0], resource[2]
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "composition": (
                                    asdict(result)
                                    if result is not None
                                    else None
                                )
                            },
                        )
                        return
                    if (
                        resource is not None
                        and resource[1] == "xml-outputs"
                        and resource[3] is None
                    ):
                        result = system_host.delete_project_xml_output(
                            resource[0], resource[2]
                        )
                        self._send_json(HTTPStatus.OK, asdict(result))
                        return
                    parts = [unquote(part) for part in path.split("/") if part]
                    if (
                        len(parts) == 6
                        and parts[:3] == ["api", "v1", "projects"]
                        and parts[4] == "planting-batches"
                    ):
                        result = system_host.remove_planting_batch(
                            parts[3], parts[5]
                        )
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "composition": (
                                    asdict(result)
                                    if result is not None
                                    else None
                                )
                            },
                        )
                        return
                    prefix = "/api/v1/projects/"
                    if not path.startswith(prefix):
                        self._send_error(
                            HTTPStatus.NOT_FOUND,
                            "route_not_found",
                            path,
                        )
                        return
                    session_id = unquote(path[len(prefix) :])
                    if not session_id:
                        raise ValueError("session_id is required")
                    studio_queries.close_project(session_id)
                    self._send_json(
                        HTTPStatus.OK,
                        asdict(system_host.close_project(session_id)),
                    )
                except Exception as error:
                    self._handle_error(error)

            def log_message(self, _format: str, *args: object) -> None:
                del args

            def _read_json(self, *, limit_multiplier: int = 1) -> dict[str, Any]:
                length_text = self.headers.get("Content-Length")
                if length_text is None:
                    raise ValueError("Content-Length is required")
                try:
                    length = int(length_text)
                except ValueError as error:
                    raise ValueError("Content-Length must be an integer") from error
                if length < 0 or length > body_limit * limit_multiplier:
                    raise ValueError("request body is too large")
                self._request_body_consumed = True
                try:
                    document = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("request body must be valid UTF-8 JSON") from error
                if not isinstance(document, dict):
                    raise ValueError("request body must be a JSON object")
                return document

            def _handle_error(self, error: Exception) -> None:
                if isinstance(error, StrategyAccessError):
                    self._send_error(HTTPStatus.FORBIDDEN, error.code, str(error))
                    return
                if isinstance(
                    error,
                    (
                        ProjectSessionNotFoundError,
                        ProjectResourceNotFoundError,
                        LibraryResourceNotFoundError,
                        StudioResourceNotFoundError,
                    ),
                ):
                    self._send_error(
                        HTTPStatus.NOT_FOUND,
                        (
                            "project_not_found"
                            if isinstance(error, ProjectSessionNotFoundError)
                            else "resource_not_found"
                        ),
                        str(error),
                    )
                    return
                if isinstance(error, (ValueError, FileNotFoundError)):
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_request",
                        str(error),
                    )
                    return
                if isinstance(error, PermissionError):
                    self._send_error(
                        HTTPStatus.FORBIDDEN,
                        "management_auth_required",
                        "A valid management token is required",
                    )
                    return
                if isinstance(error, SystemHostError):
                    self._send_error(
                        HTTPStatus.CONFLICT,
                        "system_host_error",
                        str(error),
                    )
                    return
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "The local service could not complete the request",
                )

            def _send_error(
                self,
                status: HTTPStatus,
                code: str,
                message: str,
            ) -> None:
                self._send_json(status, {"error": {"code": code, "message": message}})

            def _send_json(self, status: HTTPStatus, document: object) -> None:
                self._consume_unused_body()
                payload = json.dumps(
                    document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _consume_unused_body(self) -> None:
                if getattr(self, "_request_body_consumed", False):
                    return
                self._request_body_consumed = True
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return
                if not 0 < length <= body_limit:
                    return
                # Closing with unread input can reset a Windows loopback response.
                timeout = self.connection.gettimeout()
                try:
                    self.connection.settimeout(1.0)
                    self.rfile.read(length)
                except OSError:
                    self.close_connection = True
                finally:
                    self.connection.settimeout(timeout)

            def _send_export_content(
                self,
                session_id: str,
                export_id: str,
            ) -> None:
                chunk_size = 1024 * 1024
                info = system_host.get_export_download(session_id, export_id)
                first = system_host.read_export_chunk(
                    session_id,
                    export_id,
                    offset=0,
                    limit=chunk_size,
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", info.media_type)
                self.send_header("Content-Length", str(info.byte_size))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{info.suggested_filename}"',
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    self.wfile.write(first)
                    offset = len(first)
                    while offset < info.byte_size:
                        chunk = system_host.read_export_chunk(
                            session_id,
                            export_id,
                            offset=offset,
                            limit=chunk_size,
                        )
                        if not chunk:
                            raise OSError("Export Artifact ended unexpectedly")
                        self.wfile.write(chunk)
                        offset += len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def _require_management(self) -> None:
                if management_token is None:
                    raise PermissionError("Management API is disabled")
                authorization = self.headers.get("Authorization", "")
                prefix = "Bearer "
                supplied = (
                    authorization[len(prefix) :]
                    if authorization.startswith(prefix)
                    else ""
                )
                if not hmac.compare_digest(supplied, management_token):
                    raise PermissionError("Management token is invalid")

            @staticmethod
            def _material_submission_document(
                value: MaterialExtractionSubmission,
            ) -> dict[str, Any]:
                return {
                    "analysis_job": asdict(value.analysis_job),
                    "candidate": value.candidate.to_document(),
                    "confirmation": asdict(value.confirmation),
                    "extraction_job": asdict(value.extraction_job),
                }













        return Handler
