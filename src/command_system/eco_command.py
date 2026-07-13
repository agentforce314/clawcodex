"""/eco — toggle RTK-style token compression of Bash tool output.

Session-scoped switch over :mod:`src.eco` (the same process-global session
state shape as ``/effort ultracode``). When on, the wire rendering of Bash
results is compressed by deterministic filters (test-runner failure focus,
noise stripping, log dedup, recoverable head caps) with the raw output teed
per session. The TUI transcript renders the same compact string the model
sees (there is no separate raw display path for Bash); the raw
stdout/stderr fields still ride the output dict and the tee files. Exit
codes and error semantics are untouched.

Grammar: bare ``/eco`` toggles; ``on`` / ``off`` are explicit;
``status``/``stats`` reports the switch plus session savings. Unknown args →
usage. All paths are headless-safe (no UI surface needed).
"""

from __future__ import annotations

from .types import CommandContext, LocalCommand, LocalCommandResult

_USAGE = (
    "Usage: /eco [on|off|status]\n\n"
    "Compresses Bash tool output before it reaches the model (60-90% fewer\n"
    "tokens on test runs, installs, and noisy logs). The transcript shows\n"
    "the same compact rendering; whenever content is capped or summarized,\n"
    "the full raw output is saved per session and referenced via\n"
    "[full output: ...] hints, so nothing is unrecoverable."
)

_ON_MSG = (
    "Eco mode on: Bash output is compressed before reaching the model "
    "(test failures kept, noise stripped, long output capped) — the "
    "transcript shows the same compact rendering. Capped or summarized "
    "content is saved per session and linked via [full output: ...] hints. "
    "/eco off to disable."
)

_OFF_MSG = "Eco mode off: Bash output reaches the model unmodified."


def _status_text() -> str:
    from src.eco import eco_stats, is_eco_session

    stats = eco_stats()
    state = "on" if is_eco_session() else "off"
    lines = [f"Eco mode: {state}"]
    if stats.commands:
        lines.append(
            f"  Compressed {stats.commands} command output(s): "
            f"~{stats.baseline_tokens:,} → ~{stats.eco_tokens:,} tokens "
            f"(saved ~{stats.saved_tokens:,}, {stats.savings_pct:.0f}%)"
        )
        for name, (uses, saved) in sorted(
            stats.by_filter.items(), key=lambda kv: -kv[1][1]
        ):
            lines.append(f"    {name}: {uses} use(s), ~{saved:,} tokens saved")
    else:
        lines.append("  No compressions recorded this session yet.")
    return "\n".join(lines)


def eco_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """Handle /eco — toggle, explicit on/off, or status."""
    from src.eco import is_eco_session, set_eco_session

    arg = (args or "").strip().lower()

    if arg in ("status", "stats"):
        return LocalCommandResult(type="text", value=_status_text())
    if arg in ("help", "-h", "--help"):
        return LocalCommandResult(type="text", value=_USAGE)

    if arg == "":
        target = not is_eco_session()
    elif arg in ("on", "enable", "true", "1"):
        target = True
    elif arg in ("off", "disable", "false", "0"):
        target = False
    else:
        return LocalCommandResult(
            type="text", value=f"Unknown argument: {args.strip()}\n\n{_USAGE}"
        )

    set_eco_session(target)
    if target:
        return LocalCommandResult(type="text", value=_ON_MSG)
    # Turning off keeps the stats (still reportable via /eco status).
    return LocalCommandResult(type="text", value=f"{_OFF_MSG}\n{_status_text()}")


ECO_COMMAND = LocalCommand(
    name="eco",
    description="Toggle Bash-output token compression (RTK-style)",
    argument_hint="[on|off|status]",
    supports_non_interactive=True,
)
ECO_COMMAND.set_call(eco_command_call)


__all__ = ["ECO_COMMAND", "eco_command_call"]
