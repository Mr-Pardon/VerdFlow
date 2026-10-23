"""System-scoped persistence for secret-free Model API Provider config."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
import time
from typing import Any
from uuid import uuid4

from verdflow.model_api import (
    MODEL_API_PROVIDER_CONFIG_SCHEMA,
    MODEL_API_PROVIDER_CONFIG_VERSION,
    ModelApiProviderConfig,
    ModelApiProviderRegistry,
    parse_provider_config_document,
    provider_config_collection_document,
)


MAX_PROVIDER_CONFIG_BYTES = 1024 * 1024
MODEL_PROVIDER_SELECTIONS_SCHEMA = "verdflow.model-provider-selections"
MODEL_PROVIDER_SELECTIONS_VERSION = "1"


class ModelProviderConfigStore:
    """Serialize custom Provider writes under the SystemHost ownership boundary."""

    def __init__(self, system_root: Path) -> None:
        self.root = system_root.resolve()
        self.path = self.root / "model-providers.json"
        self.selections_path = self.root / "model-provider-selections.json"
        self._lock = RLock()

    def prepare(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self._write(())
            else:
                self._load_custom()
            if not self.selections_path.exists():
                self._write_selections({})
            else:
                self._load_selections()

    def registry(self) -> ModelApiProviderRegistry:
        with self._lock:
            return ModelApiProviderRegistry(
                self._load_custom(),
                selected_models=self._load_selections(),
            )

    def custom_providers(self) -> tuple[ModelApiProviderConfig, ...]:
        with self._lock:
            return self._load_custom()

    def upsert(self, provider_document: dict[str, Any]) -> ModelApiProviderConfig:
        if not isinstance(provider_document, dict):
            raise ValueError("Model Provider must be an object")
        document = {
            "schema": MODEL_API_PROVIDER_CONFIG_SCHEMA,
            "version": MODEL_API_PROVIDER_CONFIG_VERSION,
            "providers": [provider_document],
        }
        parsed = parse_provider_config_document(document)[0]
        if parsed.builtin:
            raise ValueError("Custom Model Provider cannot be builtin")
        with self._lock:
            by_id = {
                item.descriptor.provider_id: item
                for item in self._load_custom()
            }
            by_id[parsed.descriptor.provider_id] = parsed
            providers = tuple(by_id.values())
            ModelApiProviderRegistry(providers)
            self._write(providers)
            selections = self._load_selections()
            if parsed.descriptor.provider_id in selections:
                selections.pop(parsed.descriptor.provider_id)
                self._write_selections(selections)
        return parsed

    def delete(self, provider_id: str) -> ModelApiProviderConfig:
        normalized = _required_text(provider_id, "provider_id")
        with self._lock:
            providers = self._load_custom()
            by_id = {item.descriptor.provider_id: item for item in providers}
            try:
                removed = by_id.pop(normalized)
            except KeyError as error:
                raise ValueError(
                    f"Custom Model Provider was not found: {normalized}"
                ) from error
            self._write(tuple(by_id.values()))
            selections = self._load_selections()
            if normalized in selections:
                selections.pop(normalized)
                self._write_selections(selections)
            return removed

    def set_selected_model(
        self,
        provider_id: str,
        model: str,
    ) -> ModelApiProviderConfig:
        normalized = _required_text(provider_id, "provider_id")
        selected = _required_text(model, "model")
        with self._lock:
            ModelApiProviderRegistry(self._load_custom()).get(normalized)
            selections = self._load_selections()
            selections[normalized] = selected
            self._write_selections(selections)
            return ModelApiProviderRegistry(
                self._load_custom(),
                selected_models=selections,
            ).get(normalized)

    def _load_custom(self) -> tuple[ModelApiProviderConfig, ...]:
        if not self.path.is_file():
            return ()
        if self.path.stat().st_size > MAX_PROVIDER_CONFIG_BYTES:
            raise ValueError("Model Provider config exceeds size limit")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Model Provider config is not valid UTF-8 JSON") from error
        return parse_provider_config_document(document)

    def _write(self, providers: tuple[ModelApiProviderConfig, ...]) -> None:
        document = provider_config_collection_document(providers)
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_PROVIDER_CONFIG_BYTES:
            raise ValueError("Model Provider config exceeds size limit")
        temporary = self.root / f".{self.path.name}.{uuid4().hex}.tmp"
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            for attempt in range(20):
                try:
                    os.replace(temporary, self.path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.01)
        finally:
            temporary.unlink(missing_ok=True)

    def _load_selections(self) -> dict[str, str]:
        if not self.selections_path.is_file():
            return {}
        if self.selections_path.stat().st_size > MAX_PROVIDER_CONFIG_BYTES:
            raise ValueError("Model Provider selections exceed size limit")
        try:
            document = json.loads(self.selections_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Model Provider selections are not valid UTF-8 JSON"
            ) from error
        if not isinstance(document, dict):
            raise ValueError("Model Provider selections must be an object")
        if document.get("schema") != MODEL_PROVIDER_SELECTIONS_SCHEMA:
            raise ValueError("Model Provider selections schema is unsupported")
        if str(document.get("version")) != MODEL_PROVIDER_SELECTIONS_VERSION:
            raise ValueError("Model Provider selections version is unsupported")
        values = document.get("models")
        if not isinstance(values, dict):
            raise ValueError("Model Provider selections models must be an object")
        return {
            _required_text(provider_id, "provider_id"): _required_text(model, "model")
            for provider_id, model in values.items()
        }

    def _write_selections(self, selections: dict[str, str]) -> None:
        document = {
            "schema": MODEL_PROVIDER_SELECTIONS_SCHEMA,
            "version": MODEL_PROVIDER_SELECTIONS_VERSION,
            "models": dict(sorted(selections.items())),
        }
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_PROVIDER_CONFIG_BYTES:
            raise ValueError("Model Provider selections exceed size limit")
        temporary = self.root / f".{self.selections_path.name}.{uuid4().hex}.tmp"
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            for attempt in range(20):
                try:
                    os.replace(temporary, self.selections_path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.01)
        finally:
            temporary.unlink(missing_ok=True)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
