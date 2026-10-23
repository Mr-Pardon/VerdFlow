"""Errors for trusted-local strategy package inspection and execution."""


class StrategyWorkerError(RuntimeError):
    """Base error for the strategy worker adapter."""


class StrategyPackageError(StrategyWorkerError):
    """The strategy package structure or identity is invalid."""


class StrategyTrustRequiredError(StrategyWorkerError):
    """The exact inspected package hash has not been trusted."""


class StrategyExecutionError(StrategyWorkerError):
    """The trusted strategy subprocess failed or returned invalid output."""


class StrategyExecutionCancelled(StrategyWorkerError):
    """The strategy subprocess was terminated after Job cancellation."""
