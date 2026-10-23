"""Authenticated loopback HTTP transport for Explorer WebUI callbacks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sys
from threading import Thread
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit
from verdflow.contracts import (
    parse_assignment_preview_request,
    parse_strategy_preview_request,
)
from verdflow.core import StrategyAccessError

from verdflow.dts_adapter import (
    DtsLiveCorrelation, DtsLiveSelection, DtsLiveRegion, normalize_live_regions,
)
from .region_exchange import (
    RegionExchangeBusyError, RegionExportResult, RegionImportError, RegionImportResult,
)
from verdflow.system_host import (
    AssetSearchRequest,
    AssetSearchPage,
    parse_asset_search_request,
    DtsLiveAuthorizationError,
    AssetCatalogAssetPage,
    DtsLiveConfigurationOptions,
    DtsLiveConfigurationRequest,
    DtsLiveConnectorError,
    DtsLiveConnectorManager,
    DtsLiveOperationBusyError,
    DtsLiveSessionNotFoundError,
    RoleBindingProfileDetail,
    RoleBindingProfileSaveRequest,
)
from .previews import ConnectorPreviews


_COOKIE_NAME = "verdflow_dts_live"
_ELEVATION_CALLBACK_PATH = "/api/v1/callbacks/elevation"
_ELEVATION_CHUNK_PATH = "/api/v1/callbacks/elevation-chunk"
_SELECTION_BODY_LIMIT = 32 * 1024 * 1024
_REQUEST_BODY_DRAIN_CHUNK_BYTES = 64 * 1024
_MAX_OVERSIZE_DRAIN_BYTES = 32 * 1024 * 1024


class _RequestBodyTooLargeError(ValueError):
    pass


class _DtsLiveHttpServer(ThreadingHTTPServer):
    """Keep expected browser disconnects out of the server error stream."""

    def handle_error(
        self,
        request: object,
        client_address: tuple[str, int],
    ) -> None:
        if _is_client_disconnect(sys.exception()):
            return
        super().handle_error(request, client_address)


@dataclass(frozen=True, slots=True)
class DtsLiveConnectorConfig:
    host: str = "127.0.0.1"
    port: int = 0
    request_body_limit_bytes: int = 1024 * 1024
    elevation_request_body_limit_bytes: int = 16 * 1024 * 1024
    command_wait_limit_seconds: float = 25.0
    webui_root: Path | None = None
    sdk_root: Path | None = None

    def validate(self) -> None:
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("DTS Live Connector must bind to loopback")
        if self.port < 0 or self.port > 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.request_body_limit_bytes <= 0:
            raise ValueError("request_body_limit_bytes must be positive")
        if self.elevation_request_body_limit_bytes <= 0:
            raise ValueError(
                "elevation_request_body_limit_bytes must be positive"
            )
        if self.command_wait_limit_seconds <= 0:
            raise ValueError("command_wait_limit_seconds must be positive")
        if self.webui_root is not None and not self.webui_root.resolve().is_dir():
            raise ValueError("webui_root must be a directory")
        if self.sdk_root is not None and not self.sdk_root.resolve().is_dir():
            raise ValueError("sdk_root must be a directory")


class DtsLiveConnectorServer:
    """Own the Explorer transport while SystemHost owns domain sessions."""

    def __init__(
        self,
        manager: DtsLiveConnectorManager,
        config: DtsLiveConnectorConfig | None = None,
        *,
        on_selection_ready: Callable[[DtsLiveSelection], None] | None = None,
        on_session_connected: Callable[[str], None] | None = None,
        on_session_closed: Callable[[str], None] | None = None,
        configuration_options: Callable[[], DtsLiveConfigurationOptions]
        | None = None,
        asset_search: Callable[[AssetSearchRequest], AssetSearchPage] | None = None,
        catalog_assets: Callable[[str], AssetCatalogAssetPage]
        | None = None,
        library_resource: Callable[[str], tuple[Any, bytes]] | None = None,
        profile_detail: Callable[[str], RoleBindingProfileDetail] | None = None,
        save_profile: Callable[
            [RoleBindingProfileSaveRequest], RoleBindingProfileDetail
        ]
        | None = None,
        export_regions: Callable[[tuple[DtsLiveRegion, ...]], RegionExportResult] | None = None,
        import_regions: Callable[[], RegionImportResult] | None = None,
        previews: ConnectorPreviews | None = None,
    ) -> None:
        self.manager = manager
        self.config = config or DtsLiveConnectorConfig()
        self.config.validate()
        self._on_selection_ready = on_selection_ready
        self._on_session_connected = on_session_connected
        self._on_session_closed = on_session_closed
        self._configuration_options = configuration_options
        self._catalog_assets = catalog_assets
        self._asset_search = asset_search
        self._library_resource = library_resource
        self._profile_detail = profile_detail
        self._save_profile = save_profile
        self._export_regions = export_regions
        self._import_regions = import_regions
        self._previews = previews
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def __enter__(self) -> DtsLiveConnectorServer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("DTS Live Connector is not running")
        host, port = self._server.server_address[:2]
        display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        return f"http://{display_host}:{port}"

    def start(self) -> str:
        if self._server is not None:
            return self.base_url
        if self._previews is not None:
            self._previews.reopen()
        server = _DtsLiveHttpServer(
            (self.config.host, self.config.port),
            self._handler_type(),
        )
        server.daemon_threads = True
        self._server = server
        self._thread = Thread(
            target=server.serve_forever,
            name="verdflow-dts-live-connector",
            daemon=True,
        )
        self._thread.start()
        return self.base_url

    def close(self) -> None:
        if self._previews is not None:
            self._previews.close()
        server = self._server
        thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
        self._server = None
        self._thread = None

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        manager = self.manager
        body_limit = self.config.request_body_limit_bytes
        elevation_body_limit = self.config.elevation_request_body_limit_bytes
        wait_limit = self.config.command_wait_limit_seconds
        webui_root = (
            self.config.webui_root.resolve()
            if self.config.webui_root is not None
            else (Path(__file__).resolve().parent / "static")
        )
        sdk_root = (
            self.config.sdk_root.resolve()
            if self.config.sdk_root is not None
            else None
        )
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "VerdFlowDtsLiveConnector/0.1"

            def do_GET(self) -> None:
                if not self._same_origin():
                    return
                target = urlsplit(self.path)
                try:
                    if target.path in {"/", "/index.html"}:
                        self._file(webui_root / "index.html", cache=False)
                        return
                    if target.path.startswith("/assets/"):
                        self._static_file(webui_root, target.path.removeprefix("/"))
                        return
                    if target.path.startswith("/brand/"):
                        self._static_file(webui_root, target.path.removeprefix("/"), cache=False)
                        return
                    if target.path in {"/sdk/ac_conf.js", "/sdk/ac.min.js"}:
                        if sdk_root is None:
                            self._error(HTTPStatus.NOT_FOUND, "sdk_not_configured")
                            return
                        self._file(sdk_root / target.path.rsplit("/", 1)[-1], cache=True)
                        return
                    session_id, token = self._authorization()
                    preview_id = _preview_id(target.path)
                    if preview_id is not None:
                        manager.snapshot(session_id, token)
                        if owner._previews is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        self._json(
                            HTTPStatus.OK,
                            asdict(owner._previews.get(session_id, preview_id)),
                        )
                        return
                    if target.path == "/api/v1/session":
                        snapshot = manager.snapshot(session_id, token)
                        if parse_qs(target.query).get("include_regions") == ["false"]:
                            snapshot = replace(snapshot, selection_regions=())
                        document = asdict(snapshot)
                        self._json(
                            HTTPStatus.OK,
                            document,
                        )
                        return
                    if target.path == "/api/v1/configuration/options":
                        if owner._configuration_options is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        self._json(
                            HTTPStatus.OK,
                            asdict(owner._configuration_options()),
                        )
                        return
                    profile_id = _configuration_profile_id(target.path)
                    if profile_id is not None:
                        if owner._profile_detail is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        self._json(
                            HTTPStatus.OK,
                            asdict(owner._profile_detail(profile_id)),
                        )
                        return
                    resource_id = _configuration_resource_id(target.path)
                    if resource_id is not None:
                        if owner._library_resource is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        resource, payload = owner._library_resource(resource_id)
                        self._bytes(
                            HTTPStatus.OK,
                            payload,
                            content_type=resource.media_type,
                            cache=True,
                        )
                        return
                    if target.path == "/api/v1/commands":
                        query = parse_qs(target.query)
                        after = _query_int(query, "after", default=0)
                        timeout_ms = _query_int(query, "timeout_ms", default=0)
                        timeout_seconds = min(timeout_ms / 1000, wait_limit)
                        commands = manager.commands_after(
                            session_id,
                            token,
                            after,
                            timeout_seconds=timeout_seconds,
                        )
                        self._json(
                            HTTPStatus.OK,
                            {"items": [asdict(item) for item in commands]},
                        )
                        return
                    self._error(HTTPStatus.NOT_FOUND, "resource_not_found")
                except Exception as error:
                    self._known_error(error)

            def do_POST(self) -> None:
                if not self._same_origin():
                    return
                target = urlsplit(self.path)
                try:
                    limit = body_limit
                    if target.path == _ELEVATION_CALLBACK_PATH:
                        limit = elevation_body_limit
                    elif target.path in {
                        "/api/v1/operations/selection", "/api/v1/selections/export-shp",
                    }:
                        limit = _SELECTION_BODY_LIMIT
                    elif target.path == "/api/v1/asset-assignment-previews":
                        limit = body_limit * 8
                    document = self._document(limit)
                    if target.path == "/api/v1/bootstrap/exchange":
                        grant = manager.exchange_bootstrap(
                            _text(document, "connector_session_id"),
                            _text(document, "bootstrap_nonce"),
                        )
                        self._json(
                            HTTPStatus.OK,
                            {
                                "connector_session_id": grant.connector_session_id,
                                "expires_at": grant.expires_at,
                            },
                            cookie=(grant.connector_session_id, grant.access_token),
                        )
                        if owner._on_session_connected is not None:
                            owner._on_session_connected(grant.connector_session_id)
                        return
                    session_id, token = self._authorization()
                    if target.path in {
                        "/api/v1/strategy-previews", "/api/v1/asset-assignment-previews",
                    }:
                        manager.snapshot(session_id, token)
                        if owner._previews is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        if target.path == "/api/v1/strategy-previews":
                            result = owner._previews.submit(
                                session_id, parse_strategy_preview_request(document),
                            )
                            self._json(HTTPStatus.ACCEPTED, asdict(result))
                        else:
                            result = owner._previews.assignments(
                                parse_assignment_preview_request(document),
                            )
                            self._json(HTTPStatus.OK, asdict(result))
                        return
                    if target.path == "/api/v1/strategy-previews/cancel-all":
                        manager.snapshot(session_id, token)
                        if owner._previews is not None:
                            owner._previews.close_session(session_id)
                        self._json(HTTPStatus.OK, {"cancelled": True})
                        return
                    if target.path == "/api/v1/selections/export-shp":
                        manager.snapshot(session_id, token)
                        if owner._export_regions is None:
                            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "export_unavailable")
                            return
                        regions = normalize_live_regions(document.get("regions"))
                        try:
                            result = owner._export_regions(regions)
                        except RegionExchangeBusyError:
                            self._error(HTTPStatus.CONFLICT, "export_busy")
                            return
                        except OSError:
                            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "export_save_failed")
                            return
                        self._json(HTTPStatus.OK, asdict(result))
                        return
                    if target.path == "/api/v1/selections/import-shp":
                        manager.snapshot(session_id, token)
                        if owner._import_regions is None:
                            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "import_unavailable")
                            return
                        if document:
                            raise ValueError("Import paths must be chosen using the native dialog")
                        try:
                            imported = owner._import_regions()
                        except RegionExchangeBusyError:
                            self._error(HTTPStatus.CONFLICT, "export_busy")
                            return
                        except RegionImportError as error:
                            self._error(HTTPStatus.BAD_REQUEST, error.code)
                            return
                        except OSError:
                            self._error(HTTPStatus.BAD_REQUEST, "shp_read_failed")
                            return
                        self._json(HTTPStatus.OK, asdict(imported))
                        return
                    if target.path == "/api/v1/operations/selection":
                        if "regions" in document and not isinstance(document["regions"], list):
                            raise ValueError("regions must be an array")
                        command = manager.begin_selection(
                            session_id,
                            token,
                            self._configuration_request(document),
                            regions=document.get("regions"),
                        )
                        if "regions" in document and owner._on_selection_ready is not None:
                            try:
                                owner._on_selection_ready(command)
                            except BaseException:
                                manager.cancel(session_id, token)
                                raise
                        self._json(HTTPStatus.ACCEPTED, asdict(command))
                        return
                    if target.path == "/api/v1/operations/regeneration":
                        selection = manager.regenerate(
                            session_id,
                            token,
                            self._configuration_request(document),
                        )
                        if owner._on_selection_ready is not None:
                            try:
                                owner._on_selection_ready(selection)
                            except BaseException:
                                manager.cancel(session_id, token)
                                raise
                        self._json(HTTPStatus.ACCEPTED, asdict(selection))
                        return
                    if target.path == "/api/v1/configuration/asset-search":
                        manager.snapshot(session_id, token)
                        if owner._asset_search is None:
                            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "configuration_unavailable")
                            return
                        request = parse_asset_search_request(document)
                        if request.target_type != "dts":
                            raise ValueError("Live asset search requires the DTS target")
                        self._json(HTTPStatus.OK, asdict(owner._asset_search(request)))
                        return
                    if target.path == "/api/v1/configuration/assets":
                        if owner._catalog_assets is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        page = owner._catalog_assets(
                            _text(document, "catalog_id"),
                        )
                        self._json(HTTPStatus.OK, asdict(page))
                        return
                    if target.path == "/api/v1/configuration/profiles":
                        if owner._save_profile is None:
                            self._error(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "configuration_unavailable",
                            )
                            return
                        assignments = _object(document, "assignments")
                        if not assignments:
                            raise ValueError("assignments must not be empty")
                        detail = owner._save_profile(
                            RoleBindingProfileSaveRequest(
                                profile_id=_optional_text(document, "profile_id"),
                                source_profile_id=_optional_text(document, "source_profile_id"),
                                display_name=_text(document, "display_name"),
                                strategy_id=_text(document, "strategy_id"),
                                strategy_version=_text(
                                    document,
                                    "strategy_version",
                                ),
                                target_type="dts",
                                catalog_id=_optional_text(document, "catalog_id"),
                                assignments=assignments,
                                strategy_parameters=document.get("strategy_parameters"),
                                replace_strategy_parameters="strategy_parameters" in document,
                                expected_generation=(
                                    _optional_integer(
                                        document,
                                        "expected_generation",
                                    )
                                    or 0
                                ),
                            )
                        )
                        self._json(HTTPStatus.CREATED, asdict(detail))
                        return
                    if target.path == "/api/v1/callbacks/selection":
                        selection = manager.accept_selection(
                            session_id,
                            token,
                            _correlation(document),
                            document.get("coordinates"),
                        )
                        if owner._on_selection_ready is not None:
                            try:
                                owner._on_selection_ready(selection)
                            except BaseException:
                                manager.cancel(session_id, token)
                                raise
                        self._json(HTTPStatus.ACCEPTED, asdict(selection))
                        return
                    if target.path == _ELEVATION_CHUNK_PATH:
                        count = manager.accept_elevation_chunk(
                            session_id, token, _correlation(document),
                            document.get("indices"), document.get("intersects"),
                        )
                        self._json(HTTPStatus.ACCEPTED, {"accepted": True, "sample_count": count})
                        return
                    if target.path == _ELEVATION_CALLBACK_PATH:
                        samples = manager.accept_elevation(
                            session_id,
                            token,
                            _correlation(document),
                            document.get("intersects"),
                            failed_regions=document.get("failed_regions"),
                            chunked=_boolean(document, "chunked") if "chunked" in document else False,
                        )
                        self._json(
                            HTTPStatus.ACCEPTED,
                            {
                                "accepted": True,
                                "sample_count": len(samples),
                            },
                        )
                        return
                    if target.path == "/api/v1/callbacks/delivery":
                        receipt = manager.accept_delivery(
                            session_id,
                            token,
                            _correlation(document),
                            export_id=_text(document, "export_id"),
                            succeeded=_boolean(document, "succeeded"),
                            message=document.get("message"),
                        )
                        self._json(HTTPStatus.ACCEPTED, asdict(receipt))
                        return
                    if target.path == "/api/v1/operations/cancel":
                        self._json(
                            HTTPStatus.OK,
                            asdict(manager.cancel(session_id, token)),
                        )
                        return
                    if target.path == "/api/v1/session/close":
                        manager.close_session(session_id, token)
                        if owner._previews is not None:
                            owner._previews.close_session(session_id)
                        if owner._on_session_closed is not None:
                            owner._on_session_closed(session_id)
                        self._json(
                            HTTPStatus.OK,
                            {"closed": True},
                            clear_cookie=True,
                        )
                        return
                    self._error(HTTPStatus.NOT_FOUND, "resource_not_found")
                except Exception as error:
                    self._known_error(error)

            def do_DELETE(self) -> None:
                if not self._same_origin():
                    return
                try:
                    session_id, token = self._authorization()
                    manager.snapshot(session_id, token)
                    preview_id = _preview_id(urlsplit(self.path).path)
                    if preview_id is None or owner._previews is None:
                        self._error(HTTPStatus.NOT_FOUND, "resource_not_found")
                        return
                    self._json(
                        HTTPStatus.OK,
                        asdict(owner._previews.cancel(session_id, preview_id)),
                    )
                except Exception as error:
                    self._known_error(error)

            def _same_origin(self) -> bool:
                origin = self.headers.get("Origin")
                if origin is not None and origin != owner.base_url:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    if 0 < length <= body_limit:
                        timeout = self.connection.gettimeout()
                        try:
                            self.connection.settimeout(1.0)
                            self._drain_request_body(length)
                        except OSError:
                            self.close_connection = True
                        finally:
                            self.connection.settimeout(timeout)
                    self._error(HTTPStatus.FORBIDDEN, "origin_not_allowed")
                    return False
                return True

            def _authorization(self) -> tuple[str, str]:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                morsel = cookie.get(_COOKIE_NAME)
                if morsel is None or "." not in morsel.value:
                    raise DtsLiveAuthorizationError(
                        "DTS Live session cookie is required"
                    )
                return tuple(morsel.value.split(".", 1))  # type: ignore[return-value]

            def _document(self, limit: int) -> dict[str, Any]:
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError as error:
                    raise ValueError("Invalid Content-Length") from error
                if length <= 0:
                    raise ValueError("Request body size is invalid")
                if length > limit:
                    drain_limit = min(
                        max(limit * 2, _REQUEST_BODY_DRAIN_CHUNK_BYTES),
                        _MAX_OVERSIZE_DRAIN_BYTES,
                    )
                    if length <= drain_limit:
                        self._drain_request_body(length)
                    else:
                        self.close_connection = True
                    raise _RequestBodyTooLargeError(
                        "Request body exceeds the route limit"
                    )
                try:
                    value = json.loads(self.rfile.read(length))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("Request body must be valid JSON") from error
                if not isinstance(value, dict):
                    raise ValueError("Request body must be an object")
                return value

            def _drain_request_body(self, length: int) -> None:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(
                        min(remaining, _REQUEST_BODY_DRAIN_CHUNK_BYTES)
                    )
                    if not chunk:
                        return
                    remaining -= len(chunk)

            def _configuration_request(
                self,
                document: dict[str, Any],
            ) -> DtsLiveConfigurationRequest:
                return DtsLiveConfigurationRequest(
                    strategy_package_hash=_text(
                        document,
                        "strategy_package_hash",
                    ),
                    profile_id=_optional_text(document, "profile_id"),
                    catalog_id=_optional_text(document, "catalog_id"),
                    assignments=_object(document, "assignments"),
                    random_seed=_optional_integer(document, "random_seed"),
                    generation_args=_object(document, "generation_args"),
                )

            def _known_error(self, error: Exception) -> None:
                if _is_client_disconnect(error):
                    return
                if isinstance(error, _RequestBodyTooLargeError):
                    self._error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "payload_too_large",
                    )
                elif isinstance(error, DtsLiveAuthorizationError):
                    self._error(HTTPStatus.UNAUTHORIZED, "authorization_failed")
                elif isinstance(error, DtsLiveSessionNotFoundError):
                    self._error(HTTPStatus.NOT_FOUND, "session_not_found")
                elif isinstance(error, DtsLiveOperationBusyError):
                    self._error(HTTPStatus.CONFLICT, "operation_busy")
                elif isinstance(error, StrategyAccessError):
                    self._error(HTTPStatus.FORBIDDEN, "preview_not_allowed")
                elif isinstance(error, (DtsLiveConnectorError, ValueError)):
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
                else:
                    self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error")

            def _json(
                self,
                status: HTTPStatus,
                document: object,
                *,
                cookie: tuple[str, str] | None = None,
                clear_cookie: bool = False,
            ) -> None:
                payload = json.dumps(
                    document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status.value)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                if cookie is not None:
                    self.send_header(
                        "Set-Cookie",
                        f"{_COOKIE_NAME}={cookie[0]}.{cookie[1]}; "
                        "HttpOnly; SameSite=Strict; Path=/",
                    )
                elif clear_cookie:
                    self.send_header(
                        "Set-Cookie",
                        f"{_COOKIE_NAME}=; HttpOnly; SameSite=Strict; "
                        "Path=/; Max-Age=0",
                    )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _error(self, status: HTTPStatus, code: str) -> None:
                self._json(status, {"error": {"code": code}})

            def _bytes(
                self,
                status: HTTPStatus,
                payload: bytes,
                *,
                content_type: str,
                cache: bool,
            ) -> None:
                self.send_response(status.value)
                self.send_header("Content-Type", content_type)
                self.send_header(
                    "Cache-Control",
                    "private, max-age=31536000, immutable"
                    if cache
                    else "no-store",
                )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _static_file(self, root: Path, relative: str, *, cache: bool = True) -> None:
                candidate = (root / relative).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    self._error(HTTPStatus.NOT_FOUND, "resource_not_found")
                    return
                self._file(candidate, cache=cache)

            def _file(self, path: Path, *, cache: bool) -> None:
                if not path.is_file():
                    self._error(HTTPStatus.NOT_FOUND, "resource_not_found")
                    return
                payload = path.read_bytes()
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", _media_type(path))
                self.send_header(
                    "Cache-Control",
                    "public, max-age=31536000, immutable"
                    if cache
                    else "no-store",
                )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return Handler


def _is_client_disconnect(error: BaseException | None) -> bool:
    return isinstance(error, ConnectionError)


def _text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} is required")
    return value


def _optional_text(document: dict[str, Any], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    return _text(document, field)


def _optional_integer(document: dict[str, Any], field: str) -> int | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _configuration_resource_id(path: str) -> str | None:
    prefix = "/api/v1/configuration/resources/"
    if not path.startswith(prefix):
        return None
    resource_id = path.removeprefix(prefix)
    if not resource_id or "/" in resource_id:
        return None
    return resource_id


def _preview_id(path: str) -> str | None:
    prefix = "/api/v1/strategy-previews/"
    if not path.startswith(prefix):
        return None
    value = path.removeprefix(prefix)
    if not value or len(value) > 128 or not all(c in "0123456789abcdef" for c in value):
        raise ValueError("Invalid preview handle")
    return value


def _configuration_profile_id(path: str) -> str | None:
    prefix = "/api/v1/configuration/profiles/"
    if not path.startswith(prefix):
        return None
    profile_id = unquote(path.removeprefix(prefix))
    if not profile_id or "/" in profile_id or "\\" in profile_id:
        return None
    return profile_id


def _object(document: dict[str, Any], field: str) -> dict[str, Any]:
    value = document.get(field, {})
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _media_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _boolean(document: dict[str, Any], field: str) -> bool:
    value = document.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _correlation(document: dict[str, Any]) -> DtsLiveCorrelation:
    value = document.get("correlation")
    if not isinstance(value, dict):
        raise ValueError("correlation must be an object")
    generation = value.get("generation_token")
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise ValueError("generation_token must be an integer")
    return DtsLiveCorrelation(
        connector_session_id=_text(value, "connector_session_id"),
        operation_id=_text(value, "operation_id"),
        generation_token=generation,
        request_id=_text(value, "request_id"),
    )


def _query_int(
    query: dict[str, list[str]],
    field: str,
    *,
    default: int,
) -> int:
    values = query.get(field)
    if not values:
        return default
    try:
        value = int(values[-1])
    except ValueError as error:
        raise ValueError(f"{field} must be an integer") from error
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value
