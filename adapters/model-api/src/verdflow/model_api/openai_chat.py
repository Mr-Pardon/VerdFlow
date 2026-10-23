"""OpenAI-compatible Chat Completions mapping used by DeepSeek."""

from __future__ import annotations

import json
from typing import Any, Mapping

from verdflow.contracts import ModelTokenUsage

from .errors import ModelApiResponseError
from .models import ProtocolResponse
from .models import ModelApiReasoningMode


def build_request(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    reasoning_mode: ModelApiReasoningMode = ModelApiReasoningMode.PROVIDER_DEFAULT,
) -> dict[str, Any]:
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_output_tokens,
        "stream": False,
    }
    if reasoning_mode is not ModelApiReasoningMode.PROVIDER_DEFAULT:
        request["thinking"] = {"type": reasoning_mode.value}
    return request


def headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def decode_response(body: Mapping[str, Any]) -> ProtocolResponse:
    try:
        choices = body["choices"]
        choice = choices[0]
        message = choice["message"]
        content = message["content"]
        finish_reason = choice["finish_reason"]
    except (KeyError, IndexError, TypeError) as error:
        raise ModelApiResponseError(
            "Chat Completions response does not contain message content"
        ) from error
    if not isinstance(finish_reason, str) or not finish_reason:
        raise ModelApiResponseError(
            "Chat Completions response does not contain finish_reason"
        )
    if finish_reason != "stop":
        raise ModelApiResponseError(
            f"Chat Completions stopped before a complete result: {finish_reason}"
        )
    if not isinstance(content, str) or not content.strip():
        raise ModelApiResponseError("Chat Completions returned empty content")
    output = _json_object(content)
    return ProtocolResponse(
        request_id=_optional_text(body.get("id")),
        output=output,
        usage=_usage(body.get("usage")),
        finish_reason=finish_reason,
    )


def _json_object(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ModelApiResponseError(
            "Chat Completions content is not valid JSON"
        ) from error
    if not isinstance(value, dict):
        raise ModelApiResponseError("Chat Completions content must be a JSON object")
    return value


def _usage(value: object) -> ModelTokenUsage:
    if not isinstance(value, Mapping):
        return ModelTokenUsage()
    return ModelTokenUsage(
        input_tokens=_optional_int(value.get("prompt_tokens")),
        output_tokens=_optional_int(value.get("completion_tokens")),
        total_tokens=_optional_int(value.get("total_tokens")),
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
