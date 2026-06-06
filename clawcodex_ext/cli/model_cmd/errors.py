"""Model command errors."""

from __future__ import annotations


class ModelCommandError(Exception):
    """Base class for model command failures."""


class UnknownModelError(ModelCommandError):
    def __init__(self, model: str, provider: str | None = None) -> None:
        suffix = f" for provider {provider}" if provider else ""
        super().__init__(f"Unknown model: {model}{suffix}")


class ProviderMismatchError(ModelCommandError):
    def __init__(self, model: str, provider: str) -> None:
        super().__init__(f"Model {model} is not available for provider {provider}")


class AmbiguousModelError(ModelCommandError):
    def __init__(self, model: str, providers: list[str]) -> None:
        super().__init__(
            f"Model {model} is available for multiple providers: {', '.join(providers)}. Use --provider."
        )


class UnsupportedScopeError(ModelCommandError):
    def __init__(self, scope: str) -> None:
        super().__init__(f"Unsupported scope: {scope}. Only user scope is supported.")
