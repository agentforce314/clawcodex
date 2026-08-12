"""Failure-detection and accounting guards for the pi harbor eval adapter.

Runs ONLY in the dedicated "Harbor adapter (3.13)" CI job, which installs
harbor explicitly. ``eval/harbor/pi_agent.py`` imports ``harbor`` at module
scope and uses ``typing.override`` (3.12+), so under the main ``test (3.11)``
job the ``importorskip`` below fires and every assertion here skips silently.
A file left out of that job's file list therefore never runs at all — add new
``tests/test_harbor_*`` files to it.

Both failure modes pinned here are SILENT ones, which is why they need tests
rather than a careful reader:

1. ``pi --mode json`` exits 0 on every model/API failure. ``runPrintMode``
   only assigns ``exitCode = 1`` inside its ``mode === "text"`` branch, and
   pi's ``StreamFn`` contract forbids throwing for request failures — they
   arrive as an assistant message with ``stopReason: "error"``. Without the
   adapter's own check, an expired key or a 429 storm midway through a 74-task
   sweep is indistinguishable from tasks the agent legitimately failed: reward
   0.0, zero exceptions, plausible token counts, and harbor's
   ``--retry-include ApiRateLimitError`` never firing.

2. Compaction issues its own LLM calls and reports usage on ``compaction_end``,
   never as a ``message_end``. Counting only ``message_end`` undercounts by an
   amount that GROWS with task length — exactly the long tasks where cost
   matters — which would flatter pi against clawcodex, whose adapter takes a
   run-level total that already includes its compaction.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("harbor", reason="harbor is an eval-only tool dependency")

from harbor.agents.installed.base import (  # noqa: E402
    ApiRateLimitError,
    ApiUsageLimitError,
    NonZeroAgentExitCodeError,
)
from harbor.models.agent.context import AgentContext  # noqa: E402

_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "eval" / "harbor"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from pi_agent import Pi  # noqa: E402


def _agent(tmp_path: Path, **kwargs) -> Pi:
    return Pi(logs_dir=tmp_path, model_name="deepseek/deepseek-v4-flash", **kwargs)


def _write_log(tmp_path: Path, events: list) -> None:
    """Write a pi.jsonl. Plain strings are emitted verbatim (non-JSON noise)."""
    tmp_path.joinpath("pi.jsonl").write_text(
        "\n".join(e if isinstance(e, str) else json.dumps(e) for e in events),
        encoding="utf-8",
    )


def _usage(inp: int, out: int, cache_read: int, cache_write: int, cost: float) -> dict:
    return {
        "input": inp,
        "output": out,
        "cacheRead": cache_read,
        "cacheWrite": cache_write,
        "cost": {"total": cost},
    }


def _assistant_end(usage: dict | None = None, stop_reason: str = "stop") -> dict:
    message: dict = {"role": "assistant", "stopReason": stop_reason}
    if usage is not None:
        message["usage"] = usage
    return {"type": "message_end", "message": message}


# --------------------------------------------------------------------------- #
# Usage accounting
# --------------------------------------------------------------------------- #


def test_usage_counts_compaction_and_ignores_non_assistant_events(tmp_path):
    """Sum assistant message_end AND compaction_end; ignore everything else."""
    _write_log(
        tmp_path,
        [
            "pi startup diagnostic on stderr, not JSON",
            # message_update repeats the streaming message — must NOT be summed.
            {
                "type": "message_update",
                "message": {"role": "assistant", "usage": _usage(999, 999, 999, 999, 9.9)},
            },
            # pi emits message_end for user prompts and tool results too.
            {
                "type": "message_end",
                "message": {"role": "user", "usage": _usage(500, 0, 0, 0, 5.0)},
            },
            _assistant_end(_usage(100, 10, 20, 5, 0.001)),
            {"type": "compaction_end", "result": {"usage": _usage(1000, 50, 0, 0, 0.01)}},
            # An aborted compaction carries result: null.
            {"type": "compaction_end", "result": None, "aborted": True},
            _assistant_end(_usage(200, 20, 30, 0, 0.002)),
            # A torn final line (concurrent trials, 2>&1 interleaving).
            '{"type":"message_end","message":{"role":"assist',
        ],
    )

    context = AgentContext()
    _agent(tmp_path).populate_context_post_run(context)

    # input side is uncached + cacheRead + cacheWrite; pi's `input` excludes both.
    assert context.n_input_tokens == (100 + 200 + 1000) + (20 + 30) + 5
    assert context.n_cache_tokens == 50
    assert context.n_output_tokens == 80
    assert context.cost_usd == pytest.approx(0.013)


def test_usage_absent_leaves_context_untouched(tmp_path):
    """No usage at all must not write zeros that read as a real measurement."""
    _write_log(tmp_path, [{"type": "agent_start"}])
    context = AgentContext()
    _agent(tmp_path).populate_context_post_run(context)
    assert context.n_input_tokens is None
    assert context.n_output_tokens is None


def test_missing_log_is_not_an_error(tmp_path):
    """A trial killed before pi wrote anything must not be reported as usage."""
    context = AgentContext()
    _agent(tmp_path).populate_context_post_run(context)
    assert context.n_input_tokens is None


# --------------------------------------------------------------------------- #
# Failure detection (pi --mode json always exits 0)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("error_message", "expected"),
    [
        ("API Error: 429 rate limit exceeded", ApiRateLimitError),
        ("too many requests", ApiRateLimitError),
        ("Quota exceeded.", ApiUsageLimitError),
        ("connection reset by peer", NonZeroAgentExitCodeError),
    ],
)
def test_terminal_error_raises_classified(tmp_path, error_message, expected):
    _write_log(
        tmp_path,
        [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": error_message,
                },
            }
        ],
    )
    with pytest.raises(expected):
        _agent(tmp_path)._raise_on_agent_error("pi --print --mode json")


def test_aborted_also_raises(tmp_path):
    _write_log(tmp_path, [_assistant_end(stop_reason="aborted")])
    with pytest.raises(NonZeroAgentExitCodeError):
        _agent(tmp_path)._raise_on_agent_error("cmd")


def test_successful_run_does_not_raise(tmp_path):
    _write_log(tmp_path, [_assistant_end(_usage(10, 1, 0, 0, 0.0))])
    _agent(tmp_path)._raise_on_agent_error("cmd")


def test_recovered_error_does_not_raise(tmp_path):
    """Only the LAST assistant message decides: pi retried and then succeeded."""
    _write_log(
        tmp_path,
        [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "API Error: transient",
                },
            },
            _assistant_end(_usage(10, 1, 0, 0, 0.0)),
        ],
    )
    _agent(tmp_path)._raise_on_agent_error("cmd")


def test_missing_log_does_not_invent_a_failure(tmp_path):
    _agent(tmp_path)._raise_on_agent_error("cmd")


# --------------------------------------------------------------------------- #
# Command construction
# --------------------------------------------------------------------------- #


def _rendered_command(tmp_path: Path, **kwargs) -> str:
    agent = _agent(tmp_path, **kwargs)
    captured: dict = {}

    async def fake_exec(environment, command=None, env=None):
        captured["command"] = command
        captured["env"] = env

    agent.exec_as_agent = fake_exec  # type: ignore[method-assign]
    _write_log(tmp_path, [_assistant_end(_usage(1, 1, 0, 0, 0.0))])
    asyncio.run(Pi.run(agent, "instruction", object(), None))
    return captured["command"]


def test_command_aborts_when_setup_fails(tmp_path):
    """`set -eu` or an unwritten models.json leaves pi running UNCAPPED.

    Harbor's _exec prepends only `set -o pipefail`, not `set -e`, so without
    this the trial looks clean while measuring something else.
    """
    assert _rendered_command(tmp_path, output_cap=40960).startswith("set -eu;")


def test_output_cap_pins_the_field_deepseek_actually_honours(tmp_path):
    """DeepSeek honours max_tokens and silently ignores max_completion_tokens."""
    command = _rendered_command(tmp_path, output_cap=40960)
    assert '"maxTokensField": "max_tokens"' in command
    assert '"maxTokens": 40960' in command
    # modelOverrides, not models: `models` needs a full definition and would
    # drop the catalogue's deepseek compat flags.
    assert "modelOverrides" in command


def test_stock_run_writes_no_models_json(tmp_path):
    assert "models.json" not in _rendered_command(tmp_path)


def test_project_trust_is_explicit_in_both_directions(tmp_path):
    """Omitting the flag means "consult the trust store", not "untrusted"."""
    assert "--no-approve" in _rendered_command(tmp_path)
    assert "--approve" in _rendered_command(tmp_path, trust_project=True)
    assert "--no-approve" not in _rendered_command(tmp_path, trust_project=True)


def test_prompt_goes_over_stdin_not_argv(tmp_path):
    """pi has no `--` separator and reads bare `@foo` argv as a file reference."""
    agent = _agent(tmp_path)
    captured: dict = {}

    async def fake_exec(environment, command=None, env=None):
        captured["command"] = command

    agent.exec_as_agent = fake_exec  # type: ignore[method-assign]
    _write_log(tmp_path, [_assistant_end(_usage(1, 1, 0, 0, 0.0))])
    hostile = "@notafile.py --print /etc/passwd `id` $HOME"
    asyncio.run(Pi.run(agent, hostile, object(), None))

    command = captured["command"]
    # Quoted once, piped in — never a bare argv token.
    assert f"printf '%s' '{hostile}'" in command
    assert command.index("printf") < command.index("| pi")


def test_tools_off_drops_the_extension(tmp_path):
    assert "--extension" not in _rendered_command(tmp_path, tools="off")
    assert "--extension" in _rendered_command(tmp_path)


def test_vision_credentials_reach_the_container(tmp_path):
    """A vision tool advertised without its key dies mid-task, quietly."""
    agent = _agent(tmp_path)
    agent._get_env = lambda key: f"{key}-VALUE"  # type: ignore[method-assign]
    env = agent._build_env()
    assert env["OPENAI_API_KEY"] == "OPENAI_API_KEY-VALUE"
    assert env["TAVILY_API_KEY"] == "TAVILY_API_KEY-VALUE"
    assert env["DEEPSEEK_API_KEY"] == "DEEPSEEK_API_KEY-VALUE"
    assert env["PI_VISION_MODEL"]


def test_tools_off_does_not_forward_tool_credentials(tmp_path):
    agent = _agent(tmp_path, tools="off")
    agent._get_env = lambda key: f"{key}-VALUE"  # type: ignore[method-assign]
    env = agent._build_env()
    assert "TAVILY_API_KEY" not in env
    assert "PI_VISION_MODEL" not in env


def test_output_cap_rejects_nonsense(tmp_path):
    with pytest.raises(ValueError):
        _agent(tmp_path, output_cap=-1)
    with pytest.raises(ValueError):
        _agent(tmp_path, output_cap="banana")
    with pytest.raises(ValueError):
        _agent(tmp_path, tools="maybe")
