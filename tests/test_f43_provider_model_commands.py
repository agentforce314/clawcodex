from __future__ import annotations

from clawcodex_ext.cli.model_cmd.commands import format_model_current, run_model_command, use_model
from clawcodex_ext.cli.provider_cmd.commands import format_provider_current, run_provider_command


def test_provider_current_formats_resolution(monkeypatch) -> None:
    monkeypatch.delenv("CLAWCODEX_PROVIDER", raising=False)
    monkeypatch.delenv("CLAWCODEX_MODEL", raising=False)
    monkeypatch.setattr("clawcodex_ext.cli.model_cmd.resolver.get_default_provider", lambda: "glm")
    monkeypatch.setattr(
        "clawcodex_ext.cli.model_cmd.resolver.get_provider_config",
        lambda provider: {"default_model": "zai/glm-4"},
    )

    assert format_provider_current().splitlines() == [
        "provider: glm [user]",
        "model: zai/glm-4 [user]",
    ]
    assert format_model_current().splitlines() == [
        "provider: glm [user]",
        "model: zai/glm-4 [user]",
    ]


def test_provider_command_use_persists_default(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr("src.config.set_default_provider", calls.append)

    rc = run_provider_command(["use", "glm"])

    assert rc == 0
    assert calls == ["glm"]
    assert "Default provider set to: glm" in capsys.readouterr().out


def test_model_use_sets_provider_and_model(monkeypatch) -> None:
    provider_calls: list[str] = []
    model_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "clawcodex_ext.cli.model_cmd.store.ModelStore.set_default_provider",
        lambda self, provider, scope="user": provider_calls.append(provider),
    )
    monkeypatch.setattr(
        "clawcodex_ext.cli.model_cmd.store.ModelStore.set_default_model",
        lambda self, provider, model, scope="user": model_calls.append((provider, model)),
    )

    lines = use_model("zai/glm-4", provider="glm")

    assert provider_calls == ["glm"]
    assert model_calls == [("glm", "zai/glm-4")]
    assert lines == [
        "Default provider set to: glm",
        "Default model for glm set to: zai/glm-4",
    ]


def test_model_command_invalid_provider_returns_exit_2(capsys) -> None:
    rc = run_model_command(["list", "--provider", "missing"])

    assert rc == 2
    assert "Unknown provider" in capsys.readouterr().err
