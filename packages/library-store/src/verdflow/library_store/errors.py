"""Structured LibraryStore failures."""


class LibraryStoreError(RuntimeError):
    """Base class for user-library persistence failures."""


class LibrarySchemaError(LibraryStoreError):
    """Raised when the library schema is malformed or unsupported."""


class LibraryMigrationError(LibrarySchemaError):
    """Raised when an ordered library migration cannot complete."""


class LibraryIntegrityError(LibraryStoreError):
    """Raised when persisted metadata and managed package bytes disagree."""


class StrategyInstallConflictError(LibraryStoreError):
    """Raised when one package hash is reused for different package facts."""


class StrategyNotInstalledError(LibraryStoreError):
    """Raised when a mutation targets an unknown strategy package hash."""


class StrategyVersionNotInstalledError(LibraryStoreError):
    """Raised when a Profile references an unavailable Strategy version."""


class AssetCatalogConflictError(LibraryStoreError):
    """Raised when a Catalog update uses stale generation metadata."""


class AssetCatalogNotFoundError(LibraryStoreError):
    """Raised when the current Catalog identity is unavailable."""


class LibraryResourceNotFoundError(LibraryStoreError):
    """Raised when a managed library resource cannot be resolved."""


class RoleBindingProfileConflictError(LibraryStoreError):
    """Raised when a mutable Profile generation is stale."""


class RoleBindingProfileNotFoundError(LibraryStoreError):
    """Raised when a Profile cannot be resolved."""
