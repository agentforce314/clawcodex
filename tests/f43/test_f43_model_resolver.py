from __future__ import annotations

from clawcodex_ext.cli.model_cmd.resolver import resolve


def test_resolve_prefers_cli_provider_and_model(monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_PROVIDER", "glm")
    monkeypatch.setenv("CLAWCODEX_MODEL", "zai/glm-4")
    monkeypatch.setattr("clawcodex_ext.cli.model_cmd.resolver.get_default_provider", lambda: "openai")

    resolution = resolve(cli_provider="anthropic", cli_model="claude-sonnet-4-6")

    assert resolution.provider == "anthropic"
    assert resolution.model == "claude-sonnet-4-6"
    assert resolution.provider_source == "cli"
    assert resolution.model_source == "cli"


def test_resolve_uses_environment_before_user_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_PROVIDER", "glm")
    monkeypatch.setenv("CLAWCODEX_MODEL", "zai/glm-4")
    monkeypatch.setattr("clawcodex_ext.cli.model_cmd.resolver.get_default_provider", lambda: "anthropic")

    resolution = resolve()

    assert resolution.provider == "glm"
    assert resolution.model == "zai/glm-4"
    assert resolution.provider_source == "env"
    assert resolution.model_source == "env"


def test_resolve_falls_back_from_invalid_configured_model(monkeypatch) -> None:
    monkeypatch.delenv("CLAWCODEX_PROVIDER", raising=False)
    monkeypatch.delenv("CLAWCODEX_MODEL", raising=False)
    monkeypatch.setattr("clawcodex_ext.cli.model_cmd.resolver.get_default_provider", lambda: "glm")
    monkeypatch.setattr(
        "clawcodex_ext.cli.model_cmd.resolver.get_provider_config",
        lambda provider: {"default_model": "not-valid-for-glm"},
    )

    resolution = resolve()

    assert resolution.provider == "glm"
    assert resolution.model == "not-valid-for-glm"
    assert resolution.provider_source == "user"
    assert resolution.model_source == "user-warn"


def test_resolve_inferrs_provider_from_cli_model(monkeypatch) -> None:
    monkeypatch.delenv("CLAWCODEX_PROVIDER", raising=False)
    monkeypatch.delenv("CLAWCODEX_MODEL", raising=False)
    monkeypatch.setattr("clawcodex_ext.cli.model_cmd.resolver.get_default_provider", lambda: "anthropic")

    resolution = resolve(cli_model="zai/glm-4")

    assert resolution.provider == "glm"
    assert resolution.model == "zai/glm-4"
    assert resolution.provider_source == "cli-model"
    assert resolution.model_source == "cli"
