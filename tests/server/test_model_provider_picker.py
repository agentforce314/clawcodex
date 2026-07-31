"""The /model picker's provider list, inline key entry, and disconnect.

Regression cover for a picker that could only ever show ONE provider: the TUI
synthesized step 1's list from ``get_settings``, which reports just the
*active* provider, so the 26-entry registry was never surfaced and the list
read ``anthropic · 22 models`` and nothing else.

These lock the server half — the catalog plus the three controls the picker
drives (``list_model_providers``, ``save_provider_key``, ``disconnect_provider``)
— and the ``provider_mismatch`` signal the client needs to re-drive a
cross-provider selection through ``set_provider``.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import src.config as config_mod
from src.providers import PROVIDER_INFO
from src.providers.catalog import provider_catalog
from src.server.agent_server import _AgentSession


class _StubSession:
    """Minimal stand-in exposing only what the three handlers touch.

    The handlers are pure control-plane code — provider name, live model list,
    and a reply sink — so driving them directly keeps this focused on the
    picker contract instead of the WebSocket harness.
    """

    def __init__(self, provider_name: str = "anthropic", models=None, fusion=None):
        self.provider_name = provider_name
        self.provider = SimpleNamespace(model="claude-opus-5")
        self._models = list(models or [])
        self._fusion = fusion
        self.replies: list[tuple[object, dict]] = []

    def _available_models(self) -> list[str]:
        return list(self._models)

    def _active_fusion_name(self) -> str | None:
        """The picker marks the fusion entry current rather than its base
        model, so the control reports this alongside ``model`` exactly as
        ``get_settings`` does."""
        return self._fusion

    def _do_set_fusion_model(self, request_id: object, model: object) -> bool:
        """Not a fusion model, so _do_set_model falls through to the ordinary
        provider-compare path these tests exercise. Selecting a fusion model
        is its own control flow with its own coverage."""
        return False

    def _reply(self, request_id: object, payload: dict) -> None:
        self.replies.append((request_id, payload))

    @property
    def last(self) -> dict:
        return self.replies[-1][1]


@pytest.fixture(autouse=True)
def sandbox_config(tmp_path, monkeypatch):
    """Move EVERY credential home off the developer's real one.

    Two independent roots are in play and missing either one lets a test reach
    into the real ``~/.clawcodex``:

    * ``GLOBAL_CONFIG_FILE`` binds ``Path.home()`` at import and ignores
      ``$CLAWCODEX_CONFIG_DIR``, so it has to be patched at the module
      constant;
    * the subscription OAuth files (``anthropic-oauth.json`` /
      ``openai-oauth.json``) resolve ``$CLAWCODEX_CONFIG_DIR`` at call time, so
      they need the env var.

    Autouse, and deliberately belt-and-braces: an earlier version patched only
    the config module, which left ``remove_credentials()`` pointed at the real
    home. The disconnect tests were then safe only because the handler refuses
    to disconnect the active provider — so the moment that guard was mutated
    for a mutation-test run, the suite deleted a real Claude subscription
    login. A test fixture must never depend on the code under test declining
    to do the destructive thing.
    """
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_FILE", cfg)
    config_mod._get_default_manager().invalidate()
    yield cfg
    config_mod._get_default_manager().invalidate()


# ── the catalog ──────────────────────────────────────────────────────────────


class TestProviderCatalog:
    def test_lists_every_registered_provider_not_just_the_current_one(self):
        """The bug: one row. The registry has many, and all of them belong."""
        rows = provider_catalog(current="anthropic")

        assert len(rows) == len(PROVIDER_INFO)
        assert len(rows) > 1, "a single-row catalog is the bug this fixes"
        slugs = {r["slug"] for r in rows}
        # A hand-written class, a spec-registry row, and a local server —
        # the three shapes the registry merges.
        assert {"anthropic", "openai", "deepseek", "together", "ollama"} <= slugs

    def test_active_provider_sorts_first_and_is_marked(self):
        rows = provider_catalog(current="deepseek")

        assert rows[0]["slug"] == "deepseek"
        assert rows[0]["is_current"] is True
        assert [r["slug"] for r in rows].count("deepseek") == 1
        assert sum(1 for r in rows if r["is_current"]) == 1

    def test_authenticated_providers_sort_above_unconfigured_ones(self, monkeypatch):
        monkeypatch.setattr(
            "src.providers.provider_has_credentials",
            lambda name, key: name in {"anthropic", "together"},
        )
        rows = provider_catalog(current="anthropic")

        flags = [r["authenticated"] for r in rows]
        assert flags == sorted(flags, reverse=True), "authenticated rows must lead"
        assert rows[0]["slug"] == "anthropic"

    def test_unconfigured_api_key_provider_carries_key_env_and_warning(
        self, monkeypatch
    ):
        """These two fields are what unlock the picker's inline key stage."""
        monkeypatch.setattr(
            "src.providers.provider_has_credentials", lambda name, key: False
        )
        row = next(r for r in provider_catalog() if r["slug"] == "together")

        assert row["authenticated"] is False
        assert row["auth_type"] == "api_key"
        assert row["key_env"] == "TOGETHER_API_KEY"
        assert row["warning"] == "paste TOGETHER_API_KEY to activate"

    def test_local_servers_need_no_key(self):
        """Ollama/vLLM/SGLang accept any token, so no key stage for them."""
        row = next(r for r in provider_catalog() if r["slug"] == "ollama")

        assert row["auth_type"] == "none"
        assert row["authenticated"] is True

    def test_live_model_list_overrides_the_static_one_for_the_active_provider(self):
        """Endpoint-discovered catalogues (Ollama et al) must win over the stub."""
        rows = provider_catalog(current="ollama", current_models=["llama4:70b"])

        active = next(r for r in rows if r["slug"] == "ollama")
        other = next(r for r in rows if r["slug"] == "anthropic")
        assert active["models"] == ["llama4:70b"]
        assert active["total_models"] == 1
        assert other["models"] == PROVIDER_INFO["anthropic"]["available_models"]

    def test_unreadable_config_still_yields_a_full_catalog(self, monkeypatch):
        """One bad config block must not blank the picker."""
        monkeypatch.setattr(
            "src.config.load_config",
            lambda: (_ for _ in ()).throw(RuntimeError("corrupt config")),
        )
        rows = provider_catalog(current="anthropic")

        assert len(rows) == len(PROVIDER_INFO)

    def test_active_provider_is_authenticated_even_under_an_alias_spelling(
        self, sandbox_config
    ):
        """A session launched as ``--provider glm`` must not show its own
        running provider as needing a key.

        Driven through a REAL config file, not a mocked ``load_config``: the
        packaged defaults seed an empty ``providers.zai`` block that wins
        ``get_provider_config``'s literal lookup, so a canonical probe cannot
        see the user's ``[providers.glm]`` credential. Mocking the config away
        is what let an earlier version of this test pass while the picker sent
        the user to a key prompt for the provider they were already using.
        """
        config_mod.save_config({"providers": {"glm": {"api_key": "sk-zai-real"}}})

        # Probed canonically, zai looks unconfigured — that is the trap.
        assert next(r for r in provider_catalog() if r["slug"] == "zai")[
            "authenticated"
        ] is False

        row = next(
            r
            for r in provider_catalog(current="glm", current_ready=True)
            if r["slug"] == "zai"
        )
        assert row["is_current"] is True
        assert row["authenticated"] is True
        assert "warning" not in row

    def test_a_listed_provider_is_never_one_set_provider_would_refuse(
        self, sandbox_config
    ):
        """The catalog must agree with ``_do_set_provider``'s own resolution,
        or the picker offers rows that error the moment you pick them."""
        from src.providers import provider_has_credentials, resolve_api_key

        config_mod.save_config({"providers": {"together": {"api_key": "sk-tog"}}})
        for row in provider_catalog():
            if row["is_current"]:
                continue
            # Exactly what _do_set_provider gates on.
            assert row["authenticated"] is provider_has_credentials(
                row["slug"], resolve_api_key(row["slug"])
            ), f"{row['slug']} would be offered but refused"


# ── list_model_providers ─────────────────────────────────────────────────────


class TestListModelProvidersControl:
    def test_returns_the_whole_registry_with_the_current_model(self):
        sess = _StubSession("anthropic", models=["claude-opus-5"])
        _AgentSession._do_list_model_providers(sess, "r1")

        reply = sess.last
        assert reply["ok"] is True
        assert reply["provider"] == "anthropic"
        assert reply["model"] == "claude-opus-5"
        assert len(reply["providers"]) == len(PROVIDER_INFO) > 1

    def test_reports_the_active_fusion_name_so_the_picker_marks_the_right_row(self):
        """On a fused session ``model`` is the BASE id that serves the turn, so
        without this channel the picker's `*` would land on the base model row
        instead of the fusion entry the user selected."""
        sess = _StubSession("deepseek", models=["deepseek-v4-pro"], fusion="deepseek-v4-pro-V")
        _AgentSession._do_list_model_providers(sess, "r1")

        assert sess.last["fusion"] == "deepseek-v4-pro-V"
        assert sess.last["model"] == "claude-opus-5"

    def test_catalog_failure_reports_an_error_rather_than_crashing(self, monkeypatch):
        monkeypatch.setattr(
            "src.providers.catalog.provider_catalog",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        sess = _StubSession()
        _AgentSession._do_list_model_providers(sess, "r1")

        assert sess.last["ok"] is False
        assert sess.last["providers"] == []


# ── save_provider_key ────────────────────────────────────────────────────────


class TestSaveProviderKeyControl:
    def test_persists_the_key_and_returns_the_refreshed_row(self, sandbox_config):
        sess = _StubSession("anthropic")
        _AgentSession._do_save_provider_key(sess, "r1", "together", "sk-tog-123")

        reply = sess.last
        assert reply["ok"] is True
        assert reply["provider"]["slug"] == "together"
        assert reply["provider"]["authenticated"] is True

        block = config_mod.load_config()["providers"]["together"]
        assert block["api_key"] == "sk-tog-123"
        # Seeded so the block is complete, matching what `clawcodex login` writes.
        assert block["base_url"] == PROVIDER_INFO["together"]["default_base_url"]

    def test_does_not_clobber_a_custom_base_url(self, sandbox_config):
        config_mod.save_config(
            {"providers": {"together": {"base_url": "https://my-proxy.internal/v1"}}}
        )
        sess = _StubSession("anthropic")
        _AgentSession._do_save_provider_key(sess, "r1", "together", "sk-tog-123")

        block = config_mod.load_config()["providers"]["together"]
        assert block["base_url"] == "https://my-proxy.internal/v1"
        assert block["api_key"] == "sk-tog-123"

    def test_does_not_promote_a_project_scoped_base_url_into_global_config(
        self, sandbox_config, monkeypatch
    ):
        """Reading the MERGED view and writing GLOBAL would launder a
        repo-committable ``providers.*.base_url`` into the user's global
        config — permanently, for every other project, paired with the key
        they just typed. That redirect is exactly what the untrusted-tier
        strip exists to contain.

        ``get_provider_config`` is the merged reader; stub it to look like a
        project config is contributing an endpoint, and assert none of it
        reaches the global file.
        """
        monkeypatch.setattr(
            "src.config.get_provider_config",
            lambda name: {"base_url": "https://gateway.project-scoped.example/v1"},
        )
        sess = _StubSession("anthropic")
        _AgentSession._do_save_provider_key(sess, "r1", "together", "sk-tog-123")

        assert sess.last["ok"] is True
        on_disk = json.loads(sandbox_config.read_text())["providers"]["together"]
        assert on_disk["base_url"] == PROVIDER_INFO["together"]["default_base_url"]
        assert "project-scoped" not in on_disk["base_url"]

    @pytest.mark.parametrize(
        "slug,key,expected",
        [
            ("", "sk-1", "missing provider"),
            ("together", "   ", "missing api key"),
            ("nope-ai", "sk-1", "unknown provider"),
        ],
    )
    def test_rejects_bad_input(self, sandbox_config, slug, key, expected):
        sess = _StubSession("anthropic")
        _AgentSession._do_save_provider_key(sess, "r1", slug, key)

        assert sess.last["ok"] is False
        assert expected in sess.last["error"]

    def test_alias_slug_writes_the_canonical_block(self, sandbox_config):
        sess = _StubSession("anthropic")
        _AgentSession._do_save_provider_key(sess, "r1", "glm", "sk-zai-1")

        assert sess.last["ok"] is True
        assert config_mod.load_config()["providers"]["zai"]["api_key"] == "sk-zai-1"


# ── disconnect_provider ──────────────────────────────────────────────────────


class TestDisconnectProviderControl:
    def test_refuses_to_disconnect_the_active_provider(self, sandbox_config):
        """Pulling the live provider's key would break the session's next turn."""
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "anthropic")

        assert sess.last["ok"] is False
        assert sess.last["disconnected"] is False
        assert "active provider" in sess.last["error"]

    def test_refuses_via_an_alias_spelling_too(self, sandbox_config):
        """`glm` and `zai` are the same provider — the guard must see that."""
        sess = _StubSession("zai")
        _AgentSession._do_disconnect_provider(sess, "r1", "glm")

        assert sess.last["ok"] is False
        assert "active provider" in sess.last["error"]

    def test_clears_a_stored_key_for_an_inactive_provider(
        self, sandbox_config, monkeypatch
    ):
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        config_mod.save_config({"providers": {"together": {"api_key": "sk-tog-123"}}})
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "together")

        reply = sess.last
        assert reply["ok"] is True
        assert reply["disconnected"] is True
        assert reply["still_authenticated"] is False
        # Assert on the raw file: load_config() deep-merges the packaged
        # defaults, which supply an empty api_key for every provider, so the
        # merged view can never show the key as absent.
        on_disk = json.loads(sandbox_config.read_text())
        assert "api_key" not in on_disk["providers"]["together"]
        from src.providers import resolve_api_key

        assert resolve_api_key("together") == ""

    def test_reports_honestly_when_a_shell_env_key_survives(
        self, sandbox_config, monkeypatch
    ):
        """delete_secret cannot unset a key exported in the user's shell."""
        config_mod.save_config({"providers": {"together": {"api_key": "sk-tog-123"}}})
        monkeypatch.setenv("TOGETHER_API_KEY", "sk-from-shell")
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "together")

        reply = sess.last
        assert reply["ok"] is True
        assert reply["disconnected"] is False
        assert reply["still_authenticated"] is True
        assert "only your shell can unset" in reply["error"]

    def test_unknown_provider_is_rejected(self, sandbox_config):
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "nope-ai")

        assert sess.last["ok"] is False
        assert "unknown provider" in sess.last["error"]

    def test_does_not_delete_a_key_another_provider_also_uses(
        self, sandbox_config, monkeypatch
    ):
        """`nvidia-nim` lists DEEPSEEK_API_KEY among its candidates. Treating
        that preference list as a delete-set would destroy the DeepSeek
        credential — and DeepSeek may be the provider this very session is
        running on, routing right around the active-provider guard."""
        from src.providers import provider_env_vars, resolve_api_key
        from src.secret_store import set_secret

        assert "DEEPSEEK_API_KEY" in provider_env_vars("nvidia-nim"), (
            "precondition: nvidia-nim borrows the DeepSeek key name"
        )
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        set_secret("DEEPSEEK_API_KEY", "sk-deepseek-live")

        sess = _StubSession("deepseek")
        _AgentSession._do_disconnect_provider(sess, "r1", "nvidia-nim")

        assert resolve_api_key("deepseek") == "sk-deepseek-live"
        # And it must not claim a clean disconnect it did not achieve.
        assert sess.last["disconnected"] is False
        assert sess.last["kept_shared_env"] == ["DEEPSEEK_API_KEY"]

    def test_a_provider_can_still_disconnect_its_own_primary_key(
        self, sandbox_config, monkeypatch
    ):
        """The mirror-image trap of the fix above. DEEPSEEK_API_KEY is
        DeepSeek's own primary variable AND nvidia-nim's fallback, so a naive
        "is anyone else listing this?" ownership test makes the project's
        flagship provider permanently undisconnectable."""
        from src.providers import resolve_api_key
        from src.secret_store import set_secret

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        set_secret("DEEPSEEK_API_KEY", "sk-deepseek-1")
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "deepseek")

        assert sess.last["disconnected"] is True
        assert resolve_api_key("deepseek") == ""

    def test_a_contested_primary_key_is_reported_not_deleted(
        self, sandbox_config, monkeypatch
    ):
        """siliconflow and siliconflow-cn both lead with SILICONFLOW_API_KEY.
        Neither can claim it, so it survives and is reported."""
        from src.providers import resolve_api_key
        from src.secret_store import set_secret

        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        set_secret("SILICONFLOW_API_KEY", "sk-sf-1")
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "siliconflow")

        assert sess.last["kept_shared_env"] == ["SILICONFLOW_API_KEY"]
        assert sess.last["disconnected"] is False
        assert resolve_api_key("siliconflow-cn") == "sk-sf-1"

    def test_deletes_a_key_only_this_provider_owns(self, sandbox_config, monkeypatch):
        from src.providers import resolve_api_key
        from src.secret_store import set_secret

        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        set_secret("TOGETHER_API_KEY", "sk-tog-1")
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "together")

        assert sess.last["disconnected"] is True
        assert resolve_api_key("together") == ""

    def test_removes_a_subscription_login(self, sandbox_config, monkeypatch):
        """The OAuth-deleting branch — the most destructive lines in the
        handler, and previously the only ones no test executed."""
        import src.auth.anthropic_subscription as anthropic_auth

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        anthropic_auth.save_credentials(
            anthropic_auth.SubscriptionCredentials(
                access_token="a", refresh_token="r", expires_at=time.time() + 9999
            )
        )
        assert anthropic_auth.load_credentials() is not None
        # Sandboxed away from the real home (see sandbox_config).
        assert sandbox_config.parent in anthropic_auth.credentials_path().parents

        sess = _StubSession("deepseek")  # NOT anthropic, so the guard allows it
        _AgentSession._do_disconnect_provider(sess, "r1", "anthropic")

        assert sess.last["ok"] is True
        assert sess.last["disconnected"] is True
        assert anthropic_auth.load_credentials() is None

    def test_never_deauthenticates_the_session_through_a_borrowed_name(
        self, sandbox_config, monkeypatch
    ):
        """The active-provider guard compares slugs, so it misses this: a
        session running on nvidia-nim authenticates through the BORROWED
        DEEPSEEK_API_KEY, which deepseek owns outright. Disconnecting deepseek
        would pull the live session's own credential — a different slug, the
        same breakage the guard exists to prevent."""
        from src.providers import provider_has_credentials, resolve_api_key
        from src.secret_store import set_secret

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        set_secret("DEEPSEEK_API_KEY", "sk-borrowed")
        sess = _StubSession("nvidia-nim")
        assert provider_has_credentials("nvidia-nim", resolve_api_key("nvidia-nim"))

        _AgentSession._do_disconnect_provider(sess, "r1", "deepseek")

        assert provider_has_credentials("nvidia-nim", resolve_api_key("nvidia-nim")), (
            "the live session lost its credential"
        )
        assert sess.last["disconnected"] is False
        # Reported in its OWN field, not folded into kept_shared_env: the two
        # have different remedies, and the co-owner advice would name the
        # active provider — which this handler's guard refuses to disconnect.
        assert sess.last["kept_in_use_env"] == ["DEEPSEEK_API_KEY"]
        assert "kept_shared_env" not in sess.last
        assert "switch to another provider first" in sess.last["error"]
        assert "disconnect that too" not in sess.last["error"]

    def test_does_not_advertise_removing_a_key_the_session_needs(
        self, sandbox_config
    ):
        """The confirm screen's `removes_env` is the whole basis for calling
        this an informed choice, so it must not promise a removal the handler
        will then decline."""
        rows = provider_catalog(current="nvidia-nim", current_ready=True)
        deepseek = next(r for r in rows if r["slug"] == "deepseek")

        assert "DEEPSEEK_API_KEY" not in (deepseek.get("removes_env") or [])
        # Unrelated providers still disclose their own names.
        together = next(r for r in rows if r["slug"] == "together")
        assert together["removes_env"] == ["TOGETHER_API_KEY"]

    def test_a_shell_export_behind_a_config_key_is_not_a_clean_disconnect(
        self, sandbox_config, monkeypatch
    ):
        """delete_secret removes the config entry AND pops os.environ, so a
        name present in both places looks gone the instant after deleting it —
        while the shell export resurrects it on the next launch. Sampling the
        environment before the delete loop is what keeps this honest."""
        from src.secret_store import set_secret

        set_secret("TOGETHER_API_KEY", "sk-from-config")
        monkeypatch.setenv("TOGETHER_API_KEY", "sk-from-shell")
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "together")

        assert sess.last["disconnected"] is False
        assert sess.last["still_authenticated"] is True
        assert "only your shell can unset" in sess.last["error"]

    def test_reports_a_project_config_survivor_without_blaming_the_shell(
        self, sandbox_config, monkeypatch
    ):
        """`still_authenticated` was honest but its prose sent users hunting
        through shell rc files for a key that lives in project config."""
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        monkeypatch.setattr(
            "src.providers.resolve_api_key", lambda name, cfg=None: "sk-from-project"
        )
        sess = _StubSession("anthropic")
        _AgentSession._do_disconnect_provider(sess, "r1", "together")

        assert sess.last["still_authenticated"] is True
        assert "shell" not in sess.last["error"]
        assert "project config" in sess.last["error"]


# ── the cross-provider signal ────────────────────────────────────────────────


class TestCrossProviderSignal:
    def test_mismatched_provider_refusal_is_machine_readable(self):
        """The picker's client re-drives this through ``set_provider``; without
        the flag it would have to pattern-match the error prose."""
        sess = _StubSession("anthropic")
        _AgentSession._do_set_model(sess, "r1", "gpt-5.4", "openai")

        reply = sess.last
        assert reply["ok"] is False
        assert reply["provider_mismatch"] is True
        assert reply["provider"] == "anthropic"
        # The human-readable half stays intact.
        assert "openai" in reply["error"]

    def test_an_alias_spelling_is_not_a_cross_provider_switch(self):
        """A session launched as ``--provider glm`` keeps that spelling while
        the picker's rows carry canonical ``zai``. A raw string compare would
        refuse this same-provider switch and then send the client off to
        "switch" to the provider it is already on — where set_provider fails
        because the credential lives under the alias key. /model would be
        dead for all 59 aliases."""
        sess = _StubSession("glm")
        _AgentSession._do_set_model(sess, "r1", "GLM-5.2", "zai")

        assert sess.last["ok"] is True
        assert sess.last["model"] == "GLM-5.2"

    def test_a_genuine_cross_provider_switch_under_an_alias_still_refuses(self):
        sess = _StubSession("glm")
        _AgentSession._do_set_model(sess, "r1", "gpt-5.4", "openai")

        assert sess.last["ok"] is False
        assert sess.last["provider_mismatch"] is True
