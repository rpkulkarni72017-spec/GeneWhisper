"""Typed Exomiser integration errors."""


class ExomiserBindingError(ValueError):
    """A case cannot be bound to an explicit VCF sample."""


class ExomiserConfigurationError(ValueError):
    """Exomiser configuration is missing or inconsistent."""


class ExomiserPreflightError(ValueError):
    """A hard preflight prerequisite was not satisfied."""


class ExomiserExecutionError(RuntimeError):
    """Exomiser process invocation failed."""

    def __init__(
        self,
        message,
        *,
        exit_code=None,
        stdout=None,
        stderr=None,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class ExomiserTimeoutError(ExomiserExecutionError):
    """Exomiser exceeded the configured timeout."""
