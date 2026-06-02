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
