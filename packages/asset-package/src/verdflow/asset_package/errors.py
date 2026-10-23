"""Asset package failures exposed without ZIP implementation details."""


class AssetPackageError(RuntimeError):
    """The package is malformed, unsafe, or inconsistent."""
