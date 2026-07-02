"""ch01 round-4 WI-2 acceptance tests: PostSampling hooks fired by the loop.

TS fires ``executePostSamplingHooks`` after every completed model stream
(query.ts:1079-1089). The Python port routes the event through the
config-hook registry (``run_post_sampling_hooks``); the loop awaits it
inline right after the streaming-abort check (deviation from TS's
fire-and-forget — see the plan's WI-2 design notes).

Covers:
  * end-to-end registry lane: a real command hook configured via
    ``HookConfigManager`` writes a file once per completed stream;
  * multi-iteration: tool turn + final turn → fired twice;
  * payload: hook stdin carries hook_event/model/usage/stop_reason;
  * error-swallow: a raising runner does not kill the turn;
  * abort path: user-aborted stream → not fired;
  * fast path: no hooks configured → stream and terminal unchanged.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.hooks.config_manager import bootstrap_hook_config_manager
from src.hooks.registry import reset_global_hook_registry
from src.providers.base import ChatResponse
from src.query.query import QueryParams, run_query
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import ToolResultBlock
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _make_params(
    *,
    workspace: Path,
    provider: MagicMock,
    abort: AbortController | None = None,
    max_turns: int = 10,
    trusted: bool = True,
) -> QueryParams:
    registry = build_default_registry()
    context = ToolContext(workspace_root=workspace)
    # Both production construction sites now set this from
    # check_trust_accepted; tests default to trusted so the hook path is
    # exercised (the trust-gate class pins the untrusted behavior).
    context.workspace_trusted = trusted
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=context,
        provider=provider,
        abort_controller=abort or AbortController(),
        max_turns=max_turns,
    )


def _completion(content: str = "Done.") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="test-model",
        usage={"input_tokens": 10, "output_tokens": 5},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _tool_use(*, tool_use_id: str, workspace: Path) -> ChatResponse:
    return ChatResponse(
        content="Working...",
        model="test-model",
        usage={"input_tokens": 10, "output_tokens": 20},
        finish_reason="tool_use",
        tool_uses=[{
            "id": tool_use_id,
            "name": "Write",
            "input": {
                "file_path": str(workspace / "x.txt"),
                "content": "hi",
            },
        }],
    )


def _as_run_tools_stub(messages_factory):
    from src.services.tool_execution.orchestrator import MessageUpdate

    async def _stub(_blocks, _assistants, _can_use_tool, _ctx, *a, **k):
        for m in messages_factory():
            yield MessageUpdate(message=m, new_context=_ctx)

    return _stub


def _tool_result(tool_use_id: str) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(tool_use_id=tool_use_id, content="ok", is_error=False),
        ],
    )


class _PostSamplingHarness(unittest.TestCase):
    """Shared setup: temp workspace + a PostSampling command hook that
    appends its stdin JSON to ``hook_log`` — loaded through the real
    bootstrap so the test exercises the same lane production does."""

    def setUp(self) -> None:
        reset_global_hook_registry()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.hook_log = self.workspace / "post_sampling_log.jsonl"

    def tearDown(self) -> None:
        reset_global_hook_registry()
        self._tmp.cleanup()

    def _bootstrap_post_sampling_hook(self) -> None:
        settings = self.workspace / "settings.json"
        settings.write_text(json.dumps({
            "hooks": {
                "PostSampling": [{
                    "type": "command",
                    # Append the stdin payload as one line per invocation
                    # (command hooks run via create_subprocess_shell).
                    "command": f"cat >> {self.hook_log}; echo >> {self.hook_log}",
                }],
            },
        }), encoding="utf-8")
        manager = bootstrap_hook_config_manager(settings_path=settings)
        assert manager is not None and not manager.snapshot.is_empty

    def _log_lines(self) -> list[dict]:
        if not self.hook_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.hook_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class TestPostSamplingFires(_PostSamplingHarness):
    def test_single_turn_fires_once_with_payload(self) -> None:
        self._bootstrap_post_sampling_hook()

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()

        params = _make_params(workspace=self.workspace, provider=provider)
        _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        payload = lines[0]
        self.assertEqual(payload["hook_event"], "PostSampling")
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["usage"].get("output_tokens"), 5)

    def test_two_iterations_fire_twice(self) -> None:
        self._bootstrap_post_sampling_hook()

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            _tool_use(tool_use_id="toolu_ps1", workspace=self.workspace),
            _completion(),
        ]

        params = _make_params(workspace=self.workspace, provider=provider)
        with patch(
            "src.services.tool_execution.orchestrator.run_tools",
            new=_as_run_tools_stub(lambda: [_tool_result("toolu_ps1")]),
        ):
            _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(len(self._log_lines()), 2)

    def test_aborted_stream_does_not_fire(self) -> None:
        """Deviation pinned: unlike TS (non-blocking, fires pre-abort-check),
        the Python wire sits after the abort check so ESC stays instant."""
        self._bootstrap_post_sampling_hook()

        abort = AbortController()
        abort.abort("test_abort")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion("Should not see this")

        params = _make_params(
            workspace=self.workspace, provider=provider, abort=abort,
        )
        _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "aborted_streaming")
        self.assertEqual(self._log_lines(), [])


class TestPostSamplingTrustGate(_PostSamplingHarness):
    """The router lane must apply the same workspace-trust rule as the
    snapshot-lane executor (trust_gate WI-0.2): untrusted workspace →
    only enterprise policy hooks run."""

    def _one_turn(self, *, trusted: bool):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()
        params = _make_params(
            workspace=self.workspace, provider=provider, trusted=trusted,
        )
        return _run(run_query(params))

    def test_untrusted_workspace_skips_user_settings_hooks(self) -> None:
        self._bootstrap_post_sampling_hook()  # USER_SETTINGS source
        _, terminal = self._one_turn(trusted=False)
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(self._log_lines(), [])

    def test_trusted_workspace_runs_user_settings_hooks(self) -> None:
        self._bootstrap_post_sampling_hook()
        _, terminal = self._one_turn(trusted=True)
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(len(self._log_lines()), 1)


class TestPostSamplingIsolation(_PostSamplingHarness):
    def test_runner_exception_does_not_kill_turn(self) -> None:
        self._bootstrap_post_sampling_hook()

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()

        params = _make_params(workspace=self.workspace, provider=provider)
        with patch(
            "src.hooks.post_sampling_hooks.run_post_sampling_hooks",
            side_effect=RuntimeError("hook exploded"),
        ):
            with self.assertLogs("src.query.query", level="ERROR") as captured:
                _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertTrue(
            any("PostSampling" in line for line in captured.output),
        )

    def test_no_hooks_configured_is_a_noop(self) -> None:
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _completion()

        params = _make_params(workspace=self.workspace, provider=provider)
        messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(self._log_lines(), [])
        # The completed stream still reached the consumer untouched.
        self.assertTrue(
            any(getattr(m, "type", "") == "assistant" for m in messages),
        )


if __name__ == "__main__":
    unittest.main()
