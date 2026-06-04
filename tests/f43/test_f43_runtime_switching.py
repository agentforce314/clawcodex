from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from clawcodex_ext.runtime.context import RuntimeContext, RuntimeOptions


class FakeProvider:
    def __init__(self, model: str | None = None) -> None:
        self.model = model


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider
        self._tools = []
        self._by_name = {}

    def register(self, tool: object) -> None:
        name = getattr(tool, "name", tool.__class__.__name__.lower())
        self._tools.append(tool)
        self._by_name[name] = tool

    def unregister(self, name: str) -> None:
        self._tools = [t for t in self._tools if getattr(t, "name", "") != name]
        self._by_name.pop(name, None)

    def list_tools(self) -> list:
        return list(self._tools)

    def dispatch(self, *args, **kwargs):  # pragma: no cover - not used in tests
        raise NotImplementedError



def test_runtime_context_build_uses_model_resolver(monkeypatch, tmp_path: Path) -> None:
    built: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        "clawcodex_ext.runtime.context.resolve",
        lambda **kwargs: SimpleNamespace(provider="glm", model="zai/glm-4"),
    )
    monkeypatch.setattr(
        "src.providers.runtime.build_provider_from_config",
        lambda provider_name, model=None: built.append((provider_name, model)) or FakeProvider(model),
    )
    monkeypatch.setattr(
        "src.tool_system.defaults.build_default_registry",
        lambda provider: FakeRegistry(provider),
    )
    monkeypatch.setattr(
        "clawcodex_ext.runtime.context.attach_cron_runtime",
        lambda runtime: None,
    )

    options = RuntimeOptions(
        provider_name=None,
        model=None,
        max_turns=20,
        stream=True,
        allowed_tools=(),
        disallowed_tools=(),
        workspace_root=tmp_path,
        permission_mode="default",
        is_bypass_permissions_mode_available=False,
        resume_session_id=None,
        resume_browse=False,
    )

    runtime = RuntimeContext.build(options)

    assert built == [("glm", "zai/glm-4")]
    assert runtime.provider_name == "glm"
    assert runtime.provider.model == "zai/glm-4"
    assert options.provider_name == "glm"
    assert options.model == "zai/glm-4"


def test_runtime_context_swap_provider_replaces_provider_and_registry(monkeypatch, tmp_path: Path) -> None:
    registries: list[FakeRegistry] = []

    monkeypatch.setattr(
        "src.providers.runtime.build_provider_from_config",
        lambda provider_name, model=None: FakeProvider(model),
    )

    def fake_registry(provider: FakeProvider) -> FakeRegistry:
        registry = FakeRegistry(provider)
        registries.append(registry)
        return registry

    monkeypatch.setattr("src.tool_system.defaults.build_default_registry", fake_registry)
    monkeypatch.setattr(
        "clawcodex_ext.cron_system.runtime.replace_cron_tools",
        lambda registry: None,
    )

    tool_context = SimpleNamespace(provider=None, provider_name=None, tool_registry=None)
    runtime = RuntimeContext(
        options=SimpleNamespace(
            allowed_tools=(),
            disallowed_tools=(),
            provider_name="anthropic",
            model="claude-sonnet-4-6",
        ),
        provider_name="anthropic",
        provider=FakeProvider("claude-sonnet-4-6"),
        session=object(),
        tool_registry=FakeRegistry(FakeProvider()),
        tool_context=tool_context,
        workspace_root=tmp_path,
    )

    runtime.swap_provider("glm", "zai/glm-4")

    assert runtime.provider_name == "glm"
    assert runtime.provider.model == "zai/glm-4"
    assert runtime.tool_registry is registries[-1]
    assert runtime.options.provider_name == "glm"
    assert runtime.options.model == "zai/glm-4"
    assert tool_context.provider is runtime.provider
    assert tool_context.provider_name == "glm"
    assert tool_context.tool_registry is runtime.tool_registry


class _CountingObserver:
    """Test double that records every swap notification."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def on_runtime_swap(self, runtime) -> None:
        self.calls.append(runtime)


def test_swap_provider_notifies_attached_observers(monkeypatch, tmp_path: Path) -> None:
    """``swap_provider`` must fan out to all attached observers."""
    from clawcodex_ext.runtime.observer import attach_observer

    monkeypatch.setattr(
        "src.providers.runtime.build_provider_from_config",
        lambda provider_name, model=None: FakeProvider(model),
    )
    monkeypatch.setattr(
        "src.tool_system.defaults.build_default_registry",
        lambda provider: FakeRegistry(provider),
    )
    monkeypatch.setattr(
        "clawcodex_ext.cron_system.runtime.replace_cron_tools",
        lambda registry: None,
    )

    runtime = RuntimeContext(
        options=SimpleNamespace(
            allowed_tools=(),
            disallowed_tools=(),
            provider_name="anthropic",
            model="claude-sonnet-4-6",
        ),
        provider_name="anthropic",
        provider=FakeProvider("claude-sonnet-4-6"),
        session=object(),
        tool_registry=FakeRegistry(FakeProvider()),
        tool_context=SimpleNamespace(),
        workspace_root=tmp_path,
    )

    observer_a = _CountingObserver()
    observer_b = _CountingObserver()
    attach_observer(runtime, observer_a)
    attach_observer(runtime, observer_b)

    runtime.swap_provider("glm", "zai/glm-4")

    assert observer_a.calls == [runtime]
    assert observer_b.calls == [runtime]


def test_swap_provider_observer_errors_do_not_break_swap(monkeypatch, tmp_path: Path) -> None:
    """A faulty observer must not abort the swap."""
    from clawcodex_ext.runtime.observer import attach_observer

    monkeypatch.setattr(
        "src.providers.runtime.build_provider_from_config",
        lambda provider_name, model=None: FakeProvider(model),
    )
    monkeypatch.setattr(
        "src.tool_system.defaults.build_default_registry",
        lambda provider: FakeRegistry(provider),
    )
    monkeypatch.setattr(
        "clawcodex_ext.cron_system.runtime.replace_cron_tools",
        lambda registry: None,
    )

    runtime = RuntimeContext(
        options=SimpleNamespace(
            allowed_tools=(),
            disallowed_tools=(),
            provider_name="anthropic",
            model="claude-sonnet-4-6",
        ),
        provider_name="anthropic",
        provider=FakeProvider("claude-sonnet-4-6"),
        session=object(),
        tool_registry=FakeRegistry(FakeProvider()),
        tool_context=SimpleNamespace(),
        workspace_root=tmp_path,
    )

    class _Boom:
        def on_runtime_swap(self, runtime) -> None:
            raise RuntimeError("boom")

    healthy = _CountingObserver()
    attach_observer(runtime, _Boom())
    attach_observer(runtime, healthy)

    runtime.swap_provider("glm", "zai/glm-4")

    assert runtime.provider_name == "glm"
    assert healthy.calls == [runtime]


def test_install_repl_extensions_attaches_observer(monkeypatch) -> None:
    """``install_repl_extensions`` must register runtime commands + observer."""
    from clawcodex_ext.frontend.repl_extensions import install_repl_extensions

    runtime = SimpleNamespace(
        provider=FakeProvider("claude-sonnet-4-6"),
        provider_name="anthropic",
        tool_registry=FakeRegistry(FakeProvider()),
        tool_context=SimpleNamespace(),
        options=SimpleNamespace(model="claude-sonnet-4-6"),
        _observers=[],
    )

    class _Repl:
        provider = None
        provider_name = None
        tool_registry = None
        tool_context = None
        command_context = SimpleNamespace(
            provider=None,
            tool_registry=None,
            tool_context=None,
        )
        command_registry = SimpleNamespace(register=lambda cmd: None)

    repl = _Repl()
    install_repl_extensions(repl, runtime)

    assert runtime._observers
    observer = runtime._observers[0]
    assert hasattr(observer, "on_runtime_swap")

    fake_new = SimpleNamespace(
        provider=FakeProvider("zai/glm-4"),
        provider_name="glm",
        tool_registry=FakeRegistry(FakeProvider("zai/glm-4")),
        tool_context=SimpleNamespace(),
    )
    observer.on_runtime_swap(fake_new)

    assert repl.provider is fake_new.provider
    assert repl.provider_name == "glm"
    assert repl.tool_registry is fake_new.tool_registry
    assert repl.tool_context is fake_new.tool_context
    assert repl.command_context.provider is fake_new.provider
    assert repl.command_context.tool_registry is fake_new.tool_registry
    assert repl.command_context.tool_context is fake_new.tool_context


def test_install_tui_extensions_attaches_observer(monkeypatch) -> None:
    """``install_tui_extensions`` must wire the TUI observer to the runtime."""
    from clawcodex_ext.frontend.tui_extensions import install_tui_extensions

    runtime = SimpleNamespace(
        provider=FakeProvider("claude-sonnet-4-6"),
        provider_name="anthropic",
        tool_registry=FakeRegistry(FakeProvider()),
        tool_context=SimpleNamespace(),
        options=SimpleNamespace(model="claude-sonnet-4-6"),
        _observers=[],
    )

    class _StatusBar:
        def __init__(self) -> None:
            self.refreshes: list[tuple[str, str | None]] = []

        def refresh_identity(self, provider, model) -> None:
            self.refreshes.append((provider, model))

    class _ReplScreen:
        status_bar: _StatusBar

    class _App:
        provider = None
        provider_name = None
        model = None
        tool_registry = None
        tool_context = None
        app_state = SimpleNamespace(provider=None, model=None)
        _command_context = SimpleNamespace(
            provider=None,
            tool_registry=None,
            tool_context=None,
        )
        _repl_screen = _ReplScreen()
        _repl_screen.status_bar = _StatusBar()

        class _Bridge:
            def replace_runtime(self, **kwargs) -> None:
                self.replaced = kwargs

        _agent_bridge = _Bridge()

    app = _App()
    install_tui_extensions(app, runtime)

    assert runtime._observers
    observer = runtime._observers[0]
    assert hasattr(observer, "on_runtime_swap")

    fake_new = SimpleNamespace(
        provider=FakeProvider("zai/glm-4"),
        provider_name="glm",
        tool_registry=FakeRegistry(FakeProvider("zai/glm-4")),
        tool_context=SimpleNamespace(),
        options=SimpleNamespace(model="zai/glm-4"),
    )
    observer.on_runtime_swap(fake_new)

    assert app.provider is fake_new.provider
    assert app.provider_name == "glm"
    assert app.model == "zai/glm-4"
    assert app.tool_registry is fake_new.tool_registry
    assert app.app_state.provider == "glm"
    assert app.app_state.model == "zai/glm-4"
    assert app._repl_screen.status_bar.refreshes == [("glm", "zai/glm-4")]
