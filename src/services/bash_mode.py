"""Bash-mode (`!` prefix) execution (components C4).

Port of TS ``utils/processUserInput/processBashCommand.tsx``: the user's
``!command`` runs DIRECTLY through the Bash tool's call function —
structurally bypassing the registry's permission flow (user-typed
commands are not model-initiated actions; ``bash_tool.py``'s
defense-in-depth dangerous-pattern guard still applies) — produces NO
agent turn (TS ``shouldQuery: false``), and feeds TWO user messages into
the conversation so the model sees what happened on its next turn:

    <bash-input>command</bash-input>
    <bash-stdout>…</bash-stdout><bash-stderr>…</bash-stderr>

Divergences (documented): the TS synthetic caveat message
(``createSyntheticUserCaveatMessage``) has no Python analog and is
skipped; stdout AND stderr are both XML-escaped (TS escapes stderr
always and stdout only on some paths); the PowerShell branch is not
ported (no PowerShell tool registration in Python); commands run
SEQUENTIALLY and are refused while one is in flight (TS queues); no
live progress streaming or ESC cancel yet — the echo row shows
"running…" until completion (follow-up); texts arriving while an agent
run is in flight DEFER via ``AgentBridge.append_user_texts`` and land
after the run (conversation-integrity guard).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

BASH_INPUT_TAG = "bash-input"  # TS constants/xml.ts:8-10
BASH_STDOUT_TAG = "bash-stdout"
BASH_STDERR_TAG = "bash-stderr"


@dataclass(frozen=True)
class BashModeOutcome:
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    ok: bool = True
    error: str | None = None
    # The user messages to append to the conversation (TS parity).
    conversation_texts: tuple[str, ...] = field(default_factory=tuple)


def run_bash_mode_command(command: str, tool_context: Any) -> BashModeOutcome:
    """Execute ``command`` via the direct Bash path; never raises."""

    command = (command or "").strip()
    if not command:
        return BashModeOutcome(
            command="", ok=False, error="Usage: !<shell command>"
        )

    input_text = f"<{BASH_INPUT_TAG}>{command}</{BASH_INPUT_TAG}>"
    try:
        from src.tool_system.tools.bash.bash_tool import _bash_call

        result = _bash_call({"command": command}, tool_context)
        output = result.output if isinstance(result.output, dict) else {}
        stdout = str(output.get("stdout", "") or "")
        stderr = str(output.get("stderr", "") or "")
        exit_code = int(output.get("exit_code", 0) or 0)
        ok = not bool(result.is_error) and exit_code == 0
        out_text = (
            f"<{BASH_STDOUT_TAG}>{escape(stdout)}</{BASH_STDOUT_TAG}>"
            f"<{BASH_STDERR_TAG}>{escape(stderr)}</{BASH_STDERR_TAG}>"
        )
        return BashModeOutcome(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            ok=ok,
            conversation_texts=(input_text, out_text),
        )
    except Exception as exc:
        # TS error path: `<bash-stderr>Command failed: …</bash-stderr>`.
        message = f"Command failed: {exc}"
        out_text = (
            f"<{BASH_STDERR_TAG}>{escape(message)}</{BASH_STDERR_TAG}>"
        )
        return BashModeOutcome(
            command=command,
            stderr=message,
            exit_code=-1,
            ok=False,
            error=str(exc),
            conversation_texts=(input_text, out_text),
        )


__all__ = [
    "BASH_INPUT_TAG",
    "BASH_STDERR_TAG",
    "BASH_STDOUT_TAG",
    "BashModeOutcome",
    "run_bash_mode_command",
]
