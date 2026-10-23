"""Anthropic Messages API request and response mapping."""

from __future__ import annotations

from typing import Any, Mapping

from verdflow.contracts import ModelTokenUsage

from .errors import ModelApiResponseError
from .models import ProtocolResponse


TOOL_NAME = "submit_material_classification"


def build_request(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_schema: Mapping[str, Any],
    max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_output_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [
            {
                "name": TOOL_NAME,
                "description": "Submit the VerdFlow material classification candidate",
                "input_schema": dict(output_schema),
            }
        ],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
    }


def headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }


def decode_response(body: Mapping[str, Any]) -> ProtocolResponse:
    content = body.get("content")
    if not isinstance(content, list):
        raise ModelApiResponseError("Anthropic response content must be an array")
    output: dict[str, Any] | None = None
    for block in content:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") != "tool_use" or block.get("name") != TOOL_NAME:
            continue
        candidate = block.get("input")
        if isinstance(candidate, dict):
            output = candidate
            break
    if output is None:
        raise ModelApiResponseError(
            "Anthropic response does not contain the required tool result"
        )
    return ProtocolResponse(
        request_id=_optional_text(body.get("id")),
        output=output,
        usage=_usage(body.get("usage")),
    )


def _usage(value: object) -> ModelTokenUsage:
    if not isinstance(value, Mapping):
        return ModelTokenUsage()
    input_tokens = _optional_int(value.get("input_tokens"))
    output_tokens = _optional_int(value.get("output_tokens"))
    total = None
    if input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return ModelTokenUsage(input_tokens, output_tokens, total)


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
