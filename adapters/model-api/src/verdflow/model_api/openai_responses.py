"""OpenAI Responses API request and response mapping."""

from __future__ import annotations

import json
from typing import Any, Mapping

from verdflow.contracts import ModelTokenUsage

from .errors import ModelApiResponseError
from .models import ProtocolResponse


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
        "instructions": system_prompt,
        "input": user_prompt,
        "max_output_tokens": max_output_tokens,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "material_classification",
                "description": "VerdFlow material classification candidate",
                "strict": True,
                "schema": dict(output_schema),
            }
        },
    }


def headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def decode_response(body: Mapping[str, Any]) -> ProtocolResponse:
    content = body.get("output_text")
    if not isinstance(content, str) or not content.strip():
        content = _find_output_text(body.get("output"))
    if not content:
        raise ModelApiResponseError(
            "OpenAI Responses result does not contain output text"
        )
    try:
        output = json.loads(content)
    except json.JSONDecodeError as error:
        raise ModelApiResponseError(
            "OpenAI Responses output text is not valid JSON"
        ) from error
    if not isinstance(output, dict):
        raise ModelApiResponseError("OpenAI Responses output must be a JSON object")
    return ProtocolResponse(
        request_id=_optional_text(body.get("id")),
        output=output,
        usage=_usage(body.get("usage")),
    )


def _find_output_text(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, Mapping)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                return str(part["text"])
    return None


def _usage(value: object) -> ModelTokenUsage:
    if not isinstance(value, Mapping):
        return ModelTokenUsage()
    input_tokens = _optional_int(value.get("input_tokens"))
    output_tokens = _optional_int(value.get("output_tokens"))
    total = _optional_int(value.get("total_tokens"))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return ModelTokenUsage(input_tokens, output_tokens, total)


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
