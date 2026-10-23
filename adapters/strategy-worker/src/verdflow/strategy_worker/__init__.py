"""Trusted-local strategy package inspection and subprocess execution."""

from typing import TYPE_CHECKING, Any

from .errors import (
    StrategyExecutionCancelled,
    StrategyExecutionError,
    StrategyPackageError,
    StrategyTrustRequiredError,
    StrategyWorkerError,
)
from .package import StrategyPackage, inspect_strategy_package
from .package_archive import (
    OpenStrategyPackageArchive,
    StrategyPackageArchiveInfo,
    open_strategy_package_archive,
    write_strategy_package_archive,
)
from .runner import StrategyWorker, StrategyWorkerConfig
from .preview import StrategyPreviewWorker, StrategyPreviewWorkerConfig
from .protocol import (
    STRATEGY_GENERATE_POINTS_COMMAND,
    STRATEGY_SUPPORTED_COMMANDS,
    StrategyWorkerSupervisorConfig,
)

if TYPE_CHECKING:
    from .documents import ProjectStrategyDocumentProvider
    from .supervisor import StrategyPackageResolver, StrategyWorkerSupervisor

__all__ = [
    "StrategyPreviewWorker",
    "StrategyPreviewWorkerConfig",
    "STRATEGY_GENERATE_POINTS_COMMAND",
    "STRATEGY_SUPPORTED_COMMANDS",
    "OpenStrategyPackageArchive",
    "StrategyExecutionCancelled",
    "StrategyExecutionError",
    "ProjectStrategyDocumentProvider",
    "StrategyPackage",
    "StrategyPackageArchiveInfo",
    "StrategyPackageError",
    "StrategyTrustRequiredError",
    "StrategyWorker",
    "StrategyWorkerConfig",
    "StrategyWorkerSupervisor",
    "StrategyWorkerSupervisorConfig",
    "StrategyWorkerError",
    "StrategyPackageResolver",
    "inspect_strategy_package",
    "open_strategy_package_archive",
    "write_strategy_package_archive",
]


def __getattr__(name: str) -> Any:
    if name == "ProjectStrategyDocumentProvider":
        from .documents import ProjectStrategyDocumentProvider

        return ProjectStrategyDocumentProvider
    if name in {"StrategyPackageResolver", "StrategyWorkerSupervisor"}:
        from . import supervisor

        return getattr(supervisor, name)
    raise AttributeError(name)
