"""Public structured model API adapter surface."""

from .client import ModelApiClient
from .errors import (
    ModelApiError,
    ModelApiResponseError,
    ModelApiSecretError,
    ModelApiTransportError,
)
from .models import (
    MaterialAnalysisCallPlan,
    ModelApiCapabilities,
    ModelApiModelInfo,
    ModelApiPreset,
    ModelApiProviderConfig,
    ModelApiReasoningMode,
)
from .presets import PRESET_VERSION, provider_preset, provider_presets
from .provider_config import (
    MODEL_API_PROVIDER_CONFIG_SCHEMA,
    MODEL_API_PROVIDER_CONFIG_VERSION,
    ModelApiProviderRegistry,
    parse_provider_config_document,
    provider_config_collection_document,
    provider_config_to_document,
    validate_provider_config,
)
from .secrets import EnvironmentSecretResolver, SecretResolver
from .protocol import (
    MODEL_API_ANALYZE_MATERIALS_COMMAND,
    MODEL_API_SUPPORTED_COMMANDS,
    MODEL_API_WORKER_ADAPTER_ID,
    ModelApiWorkerConfig,
    ModelApiWorkerError,
)
from .supervisor import ModelApiWorkerSupervisor
from .transport import JsonHttpResponse, JsonTransport, UrllibJsonTransport

__all__ = [
    "EnvironmentSecretResolver",
    "JsonHttpResponse",
    "JsonTransport",
    "ModelApiClient",
    "ModelApiError",
    "ModelApiPreset",
    "ModelApiCapabilities",
    "ModelApiModelInfo",
    "ModelApiProviderConfig",
    "ModelApiProviderRegistry",
    "ModelApiReasoningMode",
    "MaterialAnalysisCallPlan",
    "ModelApiResponseError",
    "ModelApiSecretError",
    "ModelApiTransportError",
    "ModelApiWorkerConfig",
    "ModelApiWorkerError",
    "ModelApiWorkerSupervisor",
    "MODEL_API_ANALYZE_MATERIALS_COMMAND",
    "MODEL_API_SUPPORTED_COMMANDS",
    "MODEL_API_WORKER_ADAPTER_ID",
    "PRESET_VERSION",
    "MODEL_API_PROVIDER_CONFIG_SCHEMA",
    "MODEL_API_PROVIDER_CONFIG_VERSION",
    "SecretResolver",
    "UrllibJsonTransport",
    "provider_preset",
    "provider_presets",
    "parse_provider_config_document",
    "provider_config_collection_document",
    "provider_config_to_document",
    "validate_provider_config",
]
