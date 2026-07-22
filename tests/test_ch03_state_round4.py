"""ch03 round-4 acceptance tests: the two-tier bridge re-homed into the
agent-server session, live cost persistence, and per-cwd git caches.

Covers my-docs/port-improvement-round-4/ch03-state-round4-plan.md:

WI-1 — per-session AppState store: created in `_build_runtime` on the
single-session transport; persisted model seeds the provider under the
provider-match guard with explicit-model precedence; `set_model` /
`set_permission_mode` controls dispatch through the store so the
centralized on_change side effects (bootstrap mirror + settings
persistence) run structurally; `--http`-shaped sessions get no store.

WI-2 — the live persister writes the schema-owned cost block and
`_do_resume` restores it (session-ID guard; single-session gated) plus
the saved model under launch-model precedence.

WI-3 — git context caches are per-cwd (multi-session bleed fix).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.bootstrap.state import (
    get_main_loop_model_override,
    get_total_cost_usd,
    reset_state_for_tests,
)
from src.services.startup_gates import reset_session_trust_for_testing


def _reset_all() -> None:
    reset_state_for_tests()
    reset_session_trust_for_testing()
    from src.state.app_state import set_active_provider_supplier

    set_active_provider_supplier(None)


class _ServerHarness(unittest.TestCase):
    """Builds a real `_AgentSession` runtime against the `ollama` provider
    (keyless, local-config-only) with the global config redirected to a
    temp dir so settings persistence is observable and hermetic."""

    def setUp(self) -> None:
        _reset_all()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "ws"
        self.ws.mkdir()
        self.config_dir = root / "config-home"
        self.config_dir.mkdir()
        self.global_path = self.config_dir / "config.json"
        self.global_path.write_text(json.dumps({}), encoding="utf-8")
        self.sessions_dir = root / "sessions"
        self.sessions_dir.mkdir()
        self._patches = [
            patch("src.config.get_global_config_path", return_value=self.global_path),
            patch("src.config.GLOBAL_CONFIG_DIR", str(self.config_dir)),
        ]
        for p in self._patches:
            p.start()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        _reset_all()
        self._tmp.cleanup()

    def _build(self, *, model: str | None = None, single_session: bool = True):
        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )

        sess = _AgentSession(
            session_id="s-ch03",
            cwd=str(self.ws),
            config=AgentServerConfig(
                provider_name="ollama",
                model=model,
                single_session=single_session,
            ),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        self.assertIsNone(sess.init_error, f"runtime build failed: {sess.init_error}")
        return sess

    def _settings_section(self) -> dict:
        data = json.loads(self.global_path.read_text(encoding="utf-8"))
        section = data.get("settings", {})
        return section if isinstance(section, dict) else {}


class TestPerSessionStore(_ServerHarness):
    def test_single_session_gets_store_http_shape_does_not(self) -> None:
        sess = self._build(single_session=True)
        self.assertIsNotNone(sess.app_state_store)
        sess2 = self._build(single_session=False)
        self.assertIsNone(sess2.app_state_store)

    def test_persisted_model_seeds_provider_with_guard(self) -> None:
        # Persisted under the SAME provider → applies.
        self.global_path.write_text(json.dumps({
            "settings": {"model": "persisted-model", "model_provider": "ollama"},
        }), encoding="utf-8")
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        sess = self._build(model=None)
        self.assertEqual(sess.provider.model, "persisted-model")
        self.assertEqual(get_main_loop_model_override(), "persisted-model")

    def test_explicit_launch_model_wins_over_persisted(self) -> None:
        self.global_path.write_text(json.dumps({
            "settings": {"model": "persisted-model", "model_provider": "ollama"},
        }), encoding="utf-8")
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        sess = self._build(model="explicit-model")
        self.assertEqual(sess.provider.model, "explicit-model")

    def test_cross_provider_persisted_model_never_applies(self) -> None:
        self.global_path.write_text(json.dumps({
            "settings": {"model": "persisted-model", "model_provider": "deepseek"},
        }), encoding="utf-8")
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        sess = self._build(model=None)
        self.assertNotEqual(sess.provider.model, "persisted-model")

    def test_set_model_control_persists_and_mirrors(self) -> None:
        sess = self._build()
        asyncio.run(sess._handle_control_request({
            "request_id": "r1",
            "request": {"subtype": "set_model", "model": "chosen-model"},
        }))
        self.assertEqual(sess.provider.model, "chosen-model")
        self.assertEqual(
            sess.app_state_store.get_state().main_loop_model, "chosen-model",
        )
        self.assertEqual(get_main_loop_model_override(), "chosen-model")
        section = self._settings_section()
        self.assertEqual(section.get("model"), "chosen-model")
        self.assertEqual(section.get("model_provider"), "ollama")

    def test_set_permission_mode_control_updates_both_homes(self) -> None:
        sess = self._build()
        asyncio.run(sess._handle_control_request({
            "request_id": "r2",
            "request": {"subtype": "set_permission_mode", "mode": "acceptEdits"},
        }))
        self.assertEqual(
            sess.tool_context.permission_context.mode, "acceptEdits",
        )
        self.assertEqual(
            sess.app_state_store.get_state().permission_mode, "acceptEdits",
        )


class TestLiveCostPersistence(_ServerHarness):
    def _seed_cost(self) -> None:
        from src.cost_tracker import record_api_usage

        # A priced model so total_cost_usd is non-zero.
        record_api_usage(
            "deepseek-v4-pro", {"input_tokens": 1000, "output_tokens": 500},
        )

    def test_save_session_includes_schema_complete_cost_block(self) -> None:
        sess = self._build()
        self._seed_cost()
        from src.agent.conversation import Conversation

        sess.session.conversation = Conversation.from_dict(
            {"messages": [{"role": "user", "content": "hi"}]},
        )
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._save_session()
        payload = json.loads(
            (self.sessions_dir / "s-ch03.json").read_text(encoding="utf-8"),
        )
        cost = payload.get("cost")
        self.assertIsInstance(cost, dict)
        # Schema keys the reader consumes (cost_restore.py) — writer owned
        # by build_cost_block, so drift here means the extraction broke.
        for key in (
            "total_cost_usd", "total_api_duration",
            "total_api_duration_without_retries", "total_tool_duration",
            "total_lines_added", "total_lines_removed", "last_duration",
            "model_usage",
        ):
            self.assertIn(key, cost)
        self.assertGreater(cost["total_cost_usd"], 0.0)
        self.assertIn("deepseek-v4-pro", cost["model_usage"])

    def test_resume_restores_cost_with_matching_sid(self) -> None:
        sess = self._build()
        self._seed_cost()
        from src.agent.conversation import Conversation

        sess.session.conversation = Conversation.from_dict(
            {"messages": [{"role": "user", "content": "hi"}]},
        )
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir), \
             patch("src.services.cost_restore._sessions_dir", return_value=self.sessions_dir):
            sess._save_session()
            saved_cost = get_total_cost_usd()
            reset_state_for_tests()
            self.assertEqual(get_total_cost_usd(), 0.0)
            sess._do_resume("rq", "s-ch03")
        self.assertAlmostEqual(get_total_cost_usd(), saved_cost, places=12)

    def test_resume_guard_refuses_mismatched_header(self) -> None:
        sess = self._build()
        (self.sessions_dir / "s-ch03.json").write_text(json.dumps({
            "session_id": "SOMETHING-ELSE",
            "conversation": {"messages": [{"role": "user", "content": "x"}]},
            "cost": {"total_cost_usd": 99.0},
        }), encoding="utf-8")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir), \
             patch("src.services.cost_restore._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq", "s-ch03")
        self.assertEqual(get_total_cost_usd(), 0.0)

    def test_resume_cross_provider_model_ignored(self) -> None:
        """critic B1 — a model saved under a DIFFERENT provider must not be
        applied (stale model at the wrong endpoint) nor persisted (the
        store dispatch would poison the (model, provider) pairing)."""
        sess = self._build(model=None)
        before = sess.provider.model
        (self.sessions_dir / "s-ch03.json").write_text(json.dumps({
            "session_id": "s-ch03",
            "model": "deepseek-v4-pro",
            "provider": "deepseek",  # session's provider is ollama
            "conversation": {"messages": [{"role": "user", "content": "x"}]},
        }), encoding="utf-8")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir), \
             patch("src.services.cost_restore._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq", "s-ch03")
        self.assertEqual(sess.provider.model, before)
        self.assertNotEqual(self._settings_section().get("model"), "deepseek-v4-pro")

    def test_resume_legacy_file_without_provider_never_applies_model(self) -> None:
        """Old session files predate the provider field — fail-safe: no match,
        no model restore."""
        sess = self._build(model=None)
        before = sess.provider.model
        (self.sessions_dir / "s-ch03.json").write_text(json.dumps({
            "session_id": "s-ch03",
            "model": "resumed-model",
            "conversation": {"messages": [{"role": "user", "content": "x"}]},
        }), encoding="utf-8")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir), \
             patch("src.services.cost_restore._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq", "s-ch03")
        self.assertEqual(sess.provider.model, before)

    def test_resume_restores_saved_model_under_precedence(self) -> None:
        sess = self._build(model=None)
        (self.sessions_dir / "s-ch03.json").write_text(json.dumps({
            "session_id": "s-ch03",
            "model": "resumed-model",
            "provider": "ollama",
            "conversation": {"messages": [{"role": "user", "content": "x"}]},
        }), encoding="utf-8")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir), \
             patch("src.services.cost_restore._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq", "s-ch03")
        self.assertEqual(sess.provider.model, "resumed-model")
        # Explicit launch model wins over the saved one.
        sess2 = self._build(model="explicit-model")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir), \
             patch("src.services.cost_restore._sessions_dir", return_value=self.sessions_dir):
            sess2._do_resume("rq", "s-ch03")
        self.assertEqual(sess2.provider.model, "explicit-model")


class TestPerCwdGitCaches(unittest.TestCase):
    def setUp(self) -> None:
        from src.context_system.git_context import clear_git_caches

        clear_git_caches()
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        from src.context_system.git_context import clear_git_caches

        clear_git_caches()
        self._tmp.cleanup()

    def test_two_cwds_get_independent_snapshots(self) -> None:
        import shutil
        import subprocess

        if shutil.which("git") is None:  # pragma: no cover
            self.skipTest("git not available")
        from src.context_system.git_context import (
            clear_git_caches,
            collect_git_context,
        )

        root = Path(self._tmp.name)
        repo_a = root / "a"
        repo_b = root / "b"
        for repo, branch in ((repo_a, "branch-a"), (repo_b, "branch-b")):
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", branch, str(repo)],
                           check=True, capture_output=True)
            (repo / "f.txt").write_text("x")
            subprocess.run(["git", "-C", str(repo), "add", "."],
                           check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                 "user.name=T", "commit", "-qm", "init"],
                check=True, capture_output=True)

        snap_a = asyncio.run(collect_git_context(str(repo_a)))
        snap_b = asyncio.run(collect_git_context(str(repo_b)))
        self.assertEqual(snap_a.branch, "branch-a")
        self.assertEqual(snap_b.branch, "branch-b")
        # Cached per key: repeated reads return the same snapshots.
        self.assertIs(asyncio.run(collect_git_context(str(repo_a))), snap_a)
        clear_git_caches()
        self.assertIsNot(asyncio.run(collect_git_context(str(repo_a))), snap_a)


class TestEstimatedCostOnSubscription(unittest.TestCase):
    """``build_cost_block`` carries a list-price ``estimated_cost_usd`` even
    when the billed ``total_cost_usd`` is $0 under a subscription — an
    observability figure for downstream trajectory/leaderboard tooling that
    never feeds the live ``/cost`` display."""

    def setUp(self) -> None:
        _reset_all()

    def tearDown(self) -> None:
        _reset_all()

    def test_subscription_run_has_zero_billed_but_nonzero_estimate(self) -> None:
        from src.cost_tracker import record_api_usage
        from src.services.cost_restore import build_cost_block
        from src.services.pricing import compute_cost

        tokens = {
            "input_tokens": 165812,
            "output_tokens": 9246,
            "cache_read_input_tokens": 82608,
            "cache_creation_input_tokens": 0,
        }
        record_api_usage("claude-opus-4-8", {**tokens, "billing_mode": "subscription"})
        block = build_cost_block()

        # Billed cost stays $0 (subscription consumes plan allowance).
        self.assertEqual(block["total_cost_usd"], 0.0)
        self.assertEqual(block["model_usage"]["claude-opus-4-8"]["cost_usd"], 0.0)
        # The list-price estimate equals compute_cost for the same tokens.
        self.assertGreater(block["estimated_cost_usd"], 0.0)
        self.assertAlmostEqual(
            block["estimated_cost_usd"],
            compute_cost("claude-opus-4-8", tokens),
            places=6,
        )

    def test_metered_run_estimate_equals_billed(self) -> None:
        from src.cost_tracker import record_api_usage
        from src.services.cost_restore import build_cost_block

        record_api_usage(
            "claude-opus-4-8",
            {"input_tokens": 1000, "output_tokens": 500},  # no billing_mode → metered
        )
        block = build_cost_block()
        self.assertGreater(block["total_cost_usd"], 0.0)
        self.assertAlmostEqual(
            block["estimated_cost_usd"], block["total_cost_usd"], places=6
        )


if __name__ == "__main__":
    unittest.main()
