"""Minimal JSON HTTP transport with redacted failures."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .errors import ModelApiTransportError


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: dict[str, Any]


class JsonTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> JsonHttpResponse: ...

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        document: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonHttpResponse: ...


class UrllibJsonTransport:
    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        return self._request_json(
            url,
            method="GET",
            headers=headers,
            payload=None,
            timeout_seconds=timeout_seconds,
        )

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        document: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._request_json(
            url,
            method="POST",
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _request_json(
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        payload: bytes | None,
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        validate_endpoint(url)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        request_headers = {"Accept": "application/json", **dict(headers)}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(
            url,
            data=payload,
            method=method,
            headers=request_headers,
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = _decode_json(response.read())
                return JsonHttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=body,
                )
        except HTTPError as error:
            request_id = error.headers.get("x-request-id") if error.headers else None
            suffix = f"; request_id={request_id}" if request_id else ""
            raise ModelApiTransportError(
                f"Model API returned HTTP {error.code}{suffix}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ModelApiTransportError(
                f"Model API transport failed: {type(error).__name__}"
            ) from error


def validate_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Model API endpoint must not contain credentials or fragments")
    if parsed.scheme == "https" and parsed.netloc:
        return
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    ):
        return
    raise ValueError("Model API endpoint must use HTTPS or loopback HTTP")


def _decode_json(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelApiTransportError(
            "Model API response is not a UTF-8 JSON object"
        ) from error
    if not isinstance(value, dict):
        raise ModelApiTransportError("Model API response must be a JSON object")
    return value
