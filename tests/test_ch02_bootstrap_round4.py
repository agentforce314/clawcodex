"""ch02 round-4 acceptance tests: cwd-scoped trust reads, interactive
prefetch wire, fast-path hygiene, child startup checkpoints.

Covers the four work items of my-docs/port-improvement-round-4/
ch02-bootstrap-round4-plan.md:

WI-1 — `config.get_merged`'s untrusted-tier strip gates per-cwd on the
persisted verdict (`check_trust_accepted`), so the agent-server child —
which never sets the process-global session flag — honors a trusted
project's `providers`/`default_provider`/`env` config, while sessions in
untrusted cwds within the SAME process stay stripped.

WI-2 — `_build_runtime` kicks `start_deferred_prefetches(cwd)`.

WI-3 — fast paths (`--version`, sieve subcommands) do not spawn the
keychain/MDM prefetch; the full-pipeline path fires it before
`run_pre_action`.

WI-4 — `_build_runtime` records startup checkpoints.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.startup_gates import (
    TRUST_KEY,
    check_trust_accepted,
    reset_session_trust_for_testing,
)


def _reset_all_trust_flags() -> None:
    """Both session-trust flags (startup_gates' + bootstrap state's) — the
    canonical gate honors either, so tests must clear both."""
    reset_session_trust_for_testing()
    from src.bootstrap.state import set_session_trust_accepted

    set_session_trust_accepted(False)


def _write_global_config(config_dir: Path, *, trusted_paths: list[str]) -> Path:
    """Write a global config whose ``projects`` map trusts *trusted_paths*."""
    from src.config import normalize_path_for_config_key

    cfg = {
        "projects": {
            normalize_path_for_config_key(p): {TRUST_KEY: True}
            for p in trusted_paths
        }
    }
    path = config_dir / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


class _TrustHarness(unittest.TestCase):
    """Temp global config + two workspaces (one trusted, one not), with the
    session flag OFF — the agent-server child's exact situation."""

    def setUp(self) -> None:
        import shutil
        import subprocess

        if shutil.which("git") is None:  # pragma: no cover
            self.skipTest("git not available (project config needs a git root)")
        _reset_all_trust_flags()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.trusted_ws = root / "trusted-ws"
        self.untrusted_ws = root / "untrusted-ws"
        for ws in (self.trusted_ws, self.untrusted_ws):
            (ws / ".clawcodex").mkdir(parents=True)
            # Project-tier config resolves from the git root.
            subprocess.run(
                ["git", "init", "-q", str(ws)], check=True, capture_output=True,
            )
            (ws / ".clawcodex" / "config.json").write_text(json.dumps({
                "providers": {"deepseek": {"base_url": "https://project.example"}},
                "default_provider": "deepseek",
                "env": {"PROJECT_MARKER": "1"},
                "harmless_key": {"kept": True},
            }), encoding="utf-8")
        self.config_dir = root / "config-home"
        self.config_dir.mkdir()
        self.global_path = _write_global_config(
            self.config_dir, trusted_paths=[str(self.trusted_ws)],
        )
        self._patches = [
            # ConfigManager.load_global resolves via get_global_config_path;
            # the trust walk (get_project_entry) + update_project_entry
            # resolve via GLOBAL_CONFIG_DIR at call time. Point both at the
            # same temp file so the two lanes agree.
            patch("src.config.get_global_config_path", return_value=self.global_path),
            patch("src.config.GLOBAL_CONFIG_DIR", str(self.config_dir)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        _reset_all_trust_flags()
        self._tmp.cleanup()


class TestCwdScopedTrustGate(_TrustHarness):
    def _merged(self, cwd: Path) -> dict:
        from src.config import ConfigManager

        return ConfigManager(cwd=cwd).get_merged()

    def test_trusted_cwd_honors_project_provider_config(self) -> None:
        merged = self._merged(self.trusted_ws)
        self.assertEqual(merged.get("default_provider"), "deepseek")
        self.assertEqual(
            merged.get("providers", {}).get("deepseek", {}).get("base_url"),
            "https://project.example",
        )
        self.assertEqual(merged.get("env", {}).get("PROJECT_MARKER"), "1")

    def test_untrusted_cwd_strips_blocked_keys_but_keeps_others(self) -> None:
        merged = self._merged(self.untrusted_ws)
        self.assertNotEqual(merged.get("default_provider"), "deepseek")
        # The global tier's built-in deepseek defaults legitimately remain;
        # what must NOT land is the project tier's base_url override.
        self.assertNotEqual(
            merged.get("providers", {}).get("deepseek", {}).get("base_url"),
            "https://project.example",
        )
        self.assertNotIn("PROJECT_MARKER", merged.get("env", {}) or {})
        self.assertEqual(merged.get("harmless_key"), {"kept": True})

    def test_multi_session_property_independent_verdicts(self) -> None:
        """One process, two managers, different cwds — the trusted one must
        not leak trust into the untrusted one (the reason WI-1 rejects a
        process-global flag flip)."""
        trusted = self._merged(self.trusted_ws)
        untrusted = self._merged(self.untrusted_ws)
        self.assertEqual(trusted.get("default_provider"), "deepseek")
        self.assertNotEqual(untrusted.get("default_provider"), "deepseek")

    def test_memo_invalidated_by_record_trust_accepted(self) -> None:
        from src.services.startup_gates import record_trust_accepted

        self.assertFalse(check_trust_accepted(self.untrusted_ws))
        # record_trust_accepted persists via update_project_entry (which
        # drops the memo) and grants the session flag; the next read must
        # see the flip.
        record_trust_accepted(self.untrusted_ws)
        self.assertTrue(check_trust_accepted(self.untrusted_ws))
        # And the config gate follows.
        _reset_all_trust_flags()  # drop the session flag; persisted verdict remains
        merged = self._merged(self.untrusted_ws)
        self.assertEqual(merged.get("default_provider"), "deepseek")

    def test_session_flag_shortcircuit_preserved(self) -> None:
        """Parent semantics: implicit (piped-stdin) trust grants apply to
        every cwd in that process — unchanged by the cwd scoping."""
        from src.services.startup_gates import grant_session_trust

        grant_session_trust()
        merged = self._merged(self.untrusted_ws)
        self.assertEqual(merged.get("default_provider"), "deepseek")

    def test_deferred_prefetch_gate_is_cwd_scoped(self) -> None:
        from src.deferred_init import _system_context_allowed

        self.assertTrue(_system_context_allowed(str(self.trusted_ws)))
        self.assertFalse(_system_context_allowed(str(self.untrusted_ws)))


class TestBuildRuntimeWiring(_TrustHarness):
    """WI-1 step 3 + WI-2 + WI-4 at the `_build_runtime` seam. The config
    names an unknown provider so the build early-returns right after the
    trust/prefetch/checkpoint block — cheap and hermetic."""

    def _build(self, cwd: Path, checkpoints: list[str], *, single_session: bool = True):
        from src.server.agent_server import AgentServerConfig, _AgentSession

        sess = _AgentSession(
            session_id="s1",
            cwd=str(cwd),
            config=AgentServerConfig(
                provider_name="definitely-not-a-provider",
                single_session=single_session,
            ),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        with patch(
            "src.utils.startup_profiler.profile_checkpoint",
            side_effect=checkpoints.append,
        ), patch(
            "src.deferred_init.start_deferred_prefetches",
        ) as prefetch_spy, patch(
            "src.permissions.trust_boundary.apply_full_config_environment_variables",
        ) as env_spy:
            from src.server.agent_server import _build_runtime

            _build_runtime(sess, None)
        return sess, prefetch_spy, env_spy

    def test_trusted_cwd_applies_env_and_kicks_prefetch(self) -> None:
        checkpoints: list[str] = []
        sess, prefetch_spy, env_spy = self._build(self.trusted_ws, checkpoints)
        env_spy.assert_called_once()
        prefetch_spy.assert_called_once_with(cwd=str(self.trusted_ws))
        self.assertIn("agent_server_build_runtime_start", checkpoints)
        self.assertIn("agent_server_trust_prefetch_done", checkpoints)

    def test_untrusted_cwd_skips_env_apply_but_still_prefetches(self) -> None:
        checkpoints: list[str] = []
        sess, prefetch_spy, env_spy = self._build(self.untrusted_ws, checkpoints)
        env_spy.assert_not_called()
        prefetch_spy.assert_called_once_with(cwd=str(self.untrusted_ws))

    def test_multi_session_transport_never_touches_process_globals(self) -> None:
        """critic B1/M3 — the --http shape (single_session=False): even a
        TRUSTED cwd must not apply project env process-wide nor warm the
        non-cwd-keyed context caches. Over-strict is the safe direction;
        the per-cwd config gate (TestCwdScopedTrustGate) still applies."""
        checkpoints: list[str] = []
        sess, prefetch_spy, env_spy = self._build(
            self.trusted_ws, checkpoints, single_session=False,
        )
        env_spy.assert_not_called()
        prefetch_spy.assert_not_called()

    def test_no_global_trust_flag_flip_ever(self) -> None:
        """critic B1 — a trusted single-session build must not set the
        process-global session-trust flags (a later session with another
        cwd would short-circuit check_trust_accepted on them)."""
        from src.bootstrap.state import get_session_trust_accepted
        import src.services.startup_gates as gates

        checkpoints: list[str] = []
        self._build(self.trusted_ws, checkpoints)
        self.assertFalse(gates._session_trust_accepted)
        self.assertFalse(get_session_trust_accepted())
        # And an untrusted cwd evaluated afterwards stays untrusted.
        self.assertFalse(check_trust_accepted(self.untrusted_ws))


class TestFastPathHygiene(unittest.TestCase):
    """WI-3 — fast paths must not spawn the keychain/MDM prefetch."""

    def _spies(self):
        return (
            patch("src.cli.get_or_start_keychain_prefetch"),
            patch("src.cli.get_or_start_mdm_raw_read"),
        )

    def test_version_flag_does_not_fire_prefetch(self) -> None:
        import src.cli as cli

        kp, mp = self._spies()
        with kp as k, mp as m, patch.object(cli.sys, "argv", ["clawcodex", "--version"]):
            rc = cli.main()
        self.assertEqual(rc, 0)
        k.assert_not_called()
        m.assert_not_called()

    def test_sieve_subcommand_does_not_fire_prefetch(self) -> None:
        import src.cli as cli

        kp, mp = self._spies()
        with kp as k, mp as m, patch.object(
            cli.sys, "argv", ["clawcodex", "doctor"],
        ), patch("src.entrypoints.doctor.run_doctor", return_value=0):
            rc = cli.main()
        self.assertEqual(rc, 0)
        k.assert_not_called()
        m.assert_not_called()

    def test_full_pipeline_fires_prefetch_before_pre_action(self) -> None:
        import src.cli as cli

        order: list[str] = []
        kp, mp = self._spies()
        with kp as k, mp as m, patch.object(
            cli.sys, "argv", ["clawcodex", "-p", "hi"],
        ), patch("src.init.run_pre_action", side_effect=lambda a: order.append("pre_action")):
            k.side_effect = lambda: order.append("keychain")
            m.side_effect = lambda: order.append("mdm")
            # Stop the run right after pre_action — print mode would need a
            # provider; the ordering property is established by then.
            with patch.object(cli, "_resolve_permission_state", side_effect=SystemExit(0)):
                with self.assertRaises(SystemExit):
                    cli.main()
        self.assertEqual(order[:3], ["keychain", "mdm", "pre_action"])

    def test_module_import_fires_nothing(self) -> None:
        """The import-time fire is gone: importing src.cli must not create
        prefetch singletons. Run in a pristine subprocess so this pins the
        real import-time behavior regardless of test ordering."""
        import subprocess
        import sys

        code = (
            "import src.cli, src.prefetch as p; "
            "import sys; sys.exit(0 if not p._singletons else 1)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"prefetch singletons created at import: {proc.stderr.decode()[:400]}",
        )


if __name__ == "__main__":
    unittest.main()
