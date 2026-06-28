"""C3a tests: token-warning math, statusline command runner, UI wiring."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.status_line_command import (
    build_status_line_input,
    execute_status_line_command,
    read_status_line_config,
)
from src.services.token_warning import (
    calculate_token_warning_state,
    context_low_message,
)


class TestTokenWarningMath:
    """The adapter must agree EXACTLY with the canonical engine port
    (services/compact/autocompact) — the C3a review's F3: a forked
    threshold made the UI warn at the engine's compact point."""

    MODEL = "claude-opus-4-7"

    def _canonical(self):
        from src.models import (
            get_context_window_for_model,
            get_model_max_output_tokens,
        )
        from src.services.compact.autocompact import (
            WARNING_THRESHOLD_BUFFER_TOKENS,
            get_auto_compact_threshold,
        )

        window = get_context_window_for_model(self.MODEL)
        max_out = get_model_max_output_tokens(self.MODEL) or None
        compact_at = get_auto_compact_threshold(window, max_out)
        return window, compact_at, compact_at - WARNING_THRESHOLD_BUFFER_TOKENS

    def test_adapter_agrees_with_engine_thresholds(self) -> None:
        window, compact_at, warn_at = self._canonical()
        # The warning must lead the engine's compact point (the 20k
        # advance buffer the TS design provides).
        assert warn_at < compact_at < window

        below = calculate_token_warning_state(warn_at - 1, self.MODEL)
        assert below.context_window == window
        assert not below.is_above_warning

        at = calculate_token_warning_state(warn_at, self.MODEL)
        assert at.is_above_warning
        assert at.is_above_error  # equal buffers in TS today
        assert not at.is_above_auto_compact

        compact = calculate_token_warning_state(compact_at, self.MODEL)
        assert compact.is_above_auto_compact

    def test_percent_left_uses_raw_window(self) -> None:
        window, _, _ = self._canonical()
        state = calculate_token_warning_state(window // 4 * 3, self.MODEL)
        assert state.percent_left == 25

    def test_context_low_message_branches_on_autocompact(
        self, monkeypatch
    ) -> None:
        state = calculate_token_warning_state(150_000, self.MODEL)
        # Default: auto-compact enabled → the dim advance notice.
        monkeypatch.delenv("DISABLE_AUTO_COMPACT", raising=False)
        monkeypatch.delenv("DISABLE_COMPACT", raising=False)
        assert "until auto-compact" in context_low_message(state)
        # Disabled → the "Context low … /compact" call to action.
        monkeypatch.setenv("DISABLE_AUTO_COMPACT", "1")
        msg = context_low_message(state)
        assert "Context low" in msg and "/compact" in msg

    def test_unknown_window_never_warns(self) -> None:
        from src.services import token_warning as tw_mod

        state = tw_mod.TokenWarningState(
            token_usage=10,
            context_window=0,
            percent_left=100,
            is_above_warning=False,
            is_above_error=False,
            is_above_auto_compact=False,
        )
        assert not state.is_above_warning


class TestStatusLineConfig:
    def test_merge_order_local_wins(self, tmp_path, monkeypatch) -> None:
        from src.permissions import settings_paths

        user = tmp_path / "user.json"
        user.write_text(
            json.dumps({"statusLine": {"type": "command", "command": "echo user"}})
        )
        proj_dir = tmp_path / "proj" / ".clawcodex"
        proj_dir.mkdir(parents=True)
        (proj_dir / "settings.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "echo proj"}})
        )
        (proj_dir / "settings.local.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "echo local"}})
        )
        monkeypatch.setattr(
            settings_paths, "user_settings_path", lambda: str(user)
        )
        cfg = read_status_line_config(str(tmp_path / "proj"))
        assert cfg == {"type": "command", "command": "echo local"}

    def test_absent_everywhere_is_none(self, tmp_path) -> None:
        assert read_status_line_config(str(tmp_path)) is None


class TestExecuteStatusLineCommand:
    def _input(self) -> dict:
        return build_status_line_input(
            model_id="m1",
            cwd="/w",
            session_id="sid",
            total_input_tokens=10,
            total_output_tokens=5,
            last_turn_input_tokens=50_000,
            context_window_size=200_000,
        )

    def test_first_stdout_line_returned(self) -> None:
        out = execute_status_line_command(
            self._input(),
            config={"type": "command", "command": "printf 'line1\\nline2\\n'"},
        )
        assert out == "line1"

    def test_command_reads_json_stdin(self) -> None:
        import sys

        out = execute_status_line_command(
            self._input(),
            config={
                "type": "command",
                "command": (
                    f"{sys.executable} -c \"import json,sys;"
                    "d=json.load(sys.stdin);"
                    "print(d['model']['id'], d['context_window']['used_percentage'])\""
                ),
            },
        )
        assert out == "m1 25"

    def test_nonzero_exit_is_none(self) -> None:
        assert (
            execute_status_line_command(
                self._input(), config={"type": "command", "command": "exit 3"}
            )
            is None
        )

    def test_timeout_is_none(self) -> None:
        assert (
            execute_status_line_command(
                self._input(),
                config={"type": "command", "command": "sleep 5"},
                timeout=0.2,
            )
            is None
        )

    def test_non_command_type_is_none(self) -> None:
        assert (
            execute_status_line_command(
                self._input(), config={"type": "static", "text": "x"}
            )
            is None
        )

    def test_input_payload_shape(self) -> None:
        payload = self._input()
        assert payload["hook_event_name"] == "StatusLine"
        # TS shapes: rounded ints, and current_usage is the usage OBJECT
        # (third-party scripts index into it) — utils/context.ts:162-188.
        assert payload["context_window"]["used_percentage"] == 25
        assert payload["context_window"]["remaining_percentage"] == 75
        assert payload["context_window"]["current_usage"][
            "input_tokens"
        ] == 50_000
        assert "vim" not in payload
        with_vim = build_status_line_input(model_id="m", vim_mode="NORMAL")
        assert with_vim["vim"] == {"mode": "NORMAL"}
        # No usage yet → null, not a zeroed object (TS getCurrentUsage).
        assert (
            build_status_line_input(model_id="m")["context_window"][
                "current_usage"
            ]
            is None
        )
