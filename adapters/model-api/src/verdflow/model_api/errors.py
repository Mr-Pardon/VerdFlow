"""Errors exposed by the model API adapter."""


class ModelApiError(RuntimeError):
    """Base failure safe to surface without request headers or raw bodies."""


class ModelApiTransportError(ModelApiError):
    """The remote endpoint could not return a usable HTTP response."""


class ModelApiResponseError(ModelApiError):
    """The provider response or structured candidate is invalid."""


class ModelApiSecretError(ModelApiError):
    """The configured secret reference cannot be resolved."""
