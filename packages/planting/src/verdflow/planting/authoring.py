"""Restricted YAML 1.2 authoring documents."""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

from .canonical import canonical_json_bytes
from .errors import PlantingDocumentError
from .validation import validate_catalog, validate_profile, validate_strategy


class _RestrictedLoader(yaml.SafeLoader):
    pass


_RestrictedLoader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for initial, resolvers in list(_RestrictedLoader.yaml_implicit_resolvers.items()):
    _RestrictedLoader.yaml_implicit_resolvers[initial] = [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag not in {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp"}
    ]
_RestrictedLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _construct_mapping(
    loader: _RestrictedLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(None, None, "expected a mapping node", node.start_mark)
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "object keys must be strings",
                key_node.start_mark,
            )
        if key == "<<":
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "YAML merge keys are not supported",
                key_node.start_mark,
            )
        if key in result:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_RestrictedLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_strategy_yaml(text: str) -> dict[str, Any]:
    return _load_authoring(text, "strategy", validate_strategy)


def load_profile_yaml(text: str) -> dict[str, Any]:
    return _load_authoring(text, "role binding profile", validate_profile)


def load_catalog_yaml(text: str) -> dict[str, Any]:
    return _load_authoring(text, "target asset catalog", validate_catalog)


def _load_authoring(
    text: str,
    label: str,
    validator: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise PlantingDocumentError(f"{label} YAML is empty")
    try:
        for token in yaml.scan(text, Loader=_RestrictedLoader):
            if isinstance(token, AnchorToken | AliasToken | TagToken):
                raise PlantingDocumentError(
                    f"{label} YAML does not allow anchors, aliases, or explicit tags"
                )
        value = yaml.load(text, Loader=_RestrictedLoader)
    except PlantingDocumentError:
        raise
    except yaml.YAMLError as error:
        raise PlantingDocumentError(f"Invalid {label} YAML: {error}") from error

    if not isinstance(value, dict):
        raise PlantingDocumentError(f"{label} YAML root must be an object")
    canonical_json_bytes(value)
    return validator(value)
