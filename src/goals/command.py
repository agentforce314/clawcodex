"""/goal + /subgoal subcommand parsing — shared by the agent-server control
handler and the headless (-p) runner so the two surfaces cannot drift.

The driver supplies the :class:`~src.goals.goals.GoalManager` plus the gate
callables; this module owns the argument grammar and the user-facing copy.

Claude Code grammar (docs/en/goal):

    /goal                    → status
    /goal status             → status (undocumented in CC but harmless)
    /goal clear              → clear (aliases: stop, off, reset, none, cancel)
    /goal <condition>        → set + kickoff turn

Donor extras (hermes): pause, resume, done (clear alias), /subgoal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.goals.goals import GOAL_CLEAR_ALIASES, GoalManager


@dataclass
class GoalCommandResult:
    """Outcome of a /goal or /subgoal invocation.

    ``kickoff`` is set only by a successful SET: the condition text the
    driver must submit as the next user turn (CC: "Setting a goal starts a
    turn immediately, with the condition itself as the directive").
    ``notice`` is the system line to show alongside a kickoff.
    """

    ok: bool
    text: str = ""
    notice: str = ""
    kickoff: Optional[str] = None
    active: bool = False


#: Returns an error string when /goal may not be used, else None.
GoalGate = Callable[[], Optional[str]]


def run_goal_command(
    mgr: GoalManager,
    arg: str,
    *,
    set_gate: Optional[GoalGate] = None,
    baseline_tokens: int = 0,
    baseline_cost_usd: float = 0.0,
) -> GoalCommandResult:
    """Execute a ``/goal`` invocation against ``mgr``.

    ``set_gate`` runs only for the SET form (CC gates /goal on workspace
    trust + hooks enabled and "tells you why instead of silently doing
    nothing"); status/clear/pause/resume are always allowed so an existing
    goal can be inspected or stopped even when the gate closes later.
    """
    arg = (arg or "").strip()
    lower = arg.lower()

    if not arg or lower == "status":
        return GoalCommandResult(
            ok=True, text=mgr.status_text(), active=mgr.is_active()
        )

    if lower in GOAL_CLEAR_ALIASES:
        had = mgr.clear()
        return GoalCommandResult(
            ok=True,
            text="✓ Goal cleared." if had else "No active goal.",
            active=False,
        )

    if lower == "pause":
        state = mgr.pause(reason="user-paused")
        return GoalCommandResult(
            ok=True,
            text=(
                f"⏸ Goal paused: {state.goal}" if state else "No goal set."
            ),
            active=False,
        )

    if lower == "resume":
        state = mgr.resume()
        if state is None:
            return GoalCommandResult(
                ok=True, text="No goal to resume.", active=False
            )
        return GoalCommandResult(
            ok=True,
            text=(
                f"▶ Goal resumed (budget reset): {state.goal}\n"
                "I'll take the next step when the current or next turn ends — "
                "send any message to kick it off now."
            ),
            active=True,
        )

    # Otherwise: SET. Gate first (trust / hooks), then validate.
    if set_gate is not None:
        err = set_gate()
        if err:
            return GoalCommandResult(ok=False, text=err, active=mgr.is_active())

    try:
        state = mgr.set(
            arg,
            baseline_tokens=baseline_tokens,
            baseline_cost_usd=baseline_cost_usd,
        )
    except ValueError as exc:
        return GoalCommandResult(
            ok=False, text=f"Invalid goal: {exc}", active=mgr.is_active()
        )

    notice = (
        f"◎ Goal set ({state.max_turns}-turn budget): {state.goal}\n"
        "After each turn, an evaluator model checks whether the condition "
        "holds; I keep working until it does. The goal clears automatically "
        "once met. Controls: /goal · /goal clear · /goal pause · /goal resume"
    )
    return GoalCommandResult(
        ok=True, text=notice, notice=notice, kickoff=state.goal, active=True
    )


def run_subgoal_command(mgr: GoalManager, arg: str) -> GoalCommandResult:
    """Execute a ``/subgoal`` invocation against ``mgr``.

    Grammar: ``/subgoal`` (list) · ``/subgoal <text>`` (append) ·
    ``/subgoal remove <n>`` (1-based) · ``/subgoal clear``.
    """
    arg = (arg or "").strip()

    if not mgr.has_goal():
        return GoalCommandResult(
            ok=False,
            text="No active goal. Set one with /goal <condition>.",
            active=False,
        )

    if not arg:
        return GoalCommandResult(
            ok=True,
            text=f"{mgr.status_line()}\n{mgr.render_subgoals()}",
            active=mgr.is_active(),
        )

    tokens = arg.split(None, 1)
    verb = tokens[0].lower()
    rest = tokens[1].strip() if len(tokens) > 1 else ""

    if verb == "remove":
        if not rest:
            return GoalCommandResult(ok=False, text="Usage: /subgoal remove <n>")
        try:
            idx = int(rest.split()[0])
        except ValueError:
            return GoalCommandResult(
                ok=False,
                text="/subgoal remove: <n> must be an integer (1-based index).",
            )
        try:
            removed = mgr.remove_subgoal(idx)
        except (IndexError, RuntimeError) as exc:
            return GoalCommandResult(ok=False, text=f"/subgoal remove: {exc}")
        return GoalCommandResult(
            ok=True, text=f"✓ Removed subgoal {idx}: {removed}",
            active=mgr.is_active(),
        )

    if verb == "clear" and not rest:
        try:
            prev = mgr.clear_subgoals()
        except RuntimeError as exc:
            return GoalCommandResult(ok=False, text=f"/subgoal clear: {exc}")
        return GoalCommandResult(
            ok=True,
            text=(
                f"✓ Cleared {prev} subgoal{'s' if prev != 1 else ''}."
                if prev else "No subgoals to clear."
            ),
            active=mgr.is_active(),
        )

    try:
        added = mgr.add_subgoal(arg)
    except (RuntimeError, ValueError) as exc:
        return GoalCommandResult(ok=False, text=f"/subgoal: {exc}")
    count = len(mgr.state.subgoals) if mgr.state else 0
    return GoalCommandResult(
        ok=True,
        text=(
            f"✓ Added subgoal {count}: {added}\n"
            "The evaluator now requires evidence for it before calling the "
            "goal done."
        ),
        active=mgr.is_active(),
    )


__all__ = ["GoalCommandResult", "run_goal_command", "run_subgoal_command"]
