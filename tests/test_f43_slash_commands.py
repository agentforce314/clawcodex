from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from clawcodex_ext.cli.runtime_commands import register_runtime_commands
from src.command_system.builtins import execute_command_sync, register_builtin_commands
from src.command_system.engine import create_command_context
from src.command_system.registry import CommandRegistry


class Runtime:
    def __init__(self) -> None:
        self.provider_name = "anthropic"
        self.provider = SimpleNamespace(model="claude-sonnet-4-6")
        self.options = SimpleNamespace(model="claude-sonnet-4-6")
        self.tool_registry = object()
        self.tool_context = SimpleNamespace()
        self.swaps: list[tuple[str, str | None]] = []

    def swap_provider(self, provider: str, model: str | None = None) -> None:
        self.swaps.append((provider, model))
        self.provider_name = provider
        self.provider = SimpleNamespace(model=model or "zai/glm-5")
        self.options.model = self.provider.model


def _context(tmp_path: Path, runtime: Runtime):
    return create_command_context(
        workspace_root=tmp_path,
        conversation=SimpleNamespace(messages=[]),
        provider=runtime.provider,
        tool_registry=runtime.tool_registry,
        tool_context=runtime.tool_context,
        runtime_context=runtime,
    )


def test_runtime_provider_command_switches_runtime(monkeypatch, tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    register_runtime_commands(registry)
    monkeypatch.setattr("src.command_system.builtins.get_command_registry", lambda: registry)
    monkeypatch.setattr("clawcodex_ext.cli.runtime_commands.ModelStore.set_default_provider", lambda self, provider: None)
    runtime = Runtime()

    success, text, error = execute_command_sync("provider", "glm", _context(tmp_path, runtime))

    assert success is True
    assert error is None
    assert runtime.swaps == [("glm", None)]
    assert "provider: glm" in text


def test_runtime_model_command_switches_runtime(monkeypatch, tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    register_runtime_commands(registry)
    monkeypatch.setattr("src.command_system.builtins.get_command_registry", lambda: registry)
    monkeypatch.setattr("clawcodex_ext.cli.runtime_commands.ModelStore.set_default_provider", lambda self, provider: None)
    monkeypatch.setattr("clawcodex_ext.cli.runtime_commands.ModelStore.set_default_model", lambda self, provider, model: None)
    runtime = Runtime()

    success, text, error = execute_command_sync(
        "model",
        "zai/glm-4 --provider glm",
        _context(tmp_path, runtime),
    )

    assert success is True
    assert error is None
    assert runtime.swaps == [("glm", "zai/glm-4")]
    assert "provider: glm" in text
    assert "model: zai/glm-4" in text
