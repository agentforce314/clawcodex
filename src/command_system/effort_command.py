"""effort — interactive ``/effort`` command (port of TS local-jsx).

Port of ``typescript/src/commands/effort/`` (``effort.tsx`` + ``index.ts``). A
``local-jsx`` command becomes an :class:`InteractiveCommand` (blocked remotely by
type). Like ``/theme`` (Phase 5) and **unlike** ``/export``, this is the *inverse* of
``/export`` at the TUI dispatch layer: the TUI keeps intercepting ``/effort``
(``commands.py`` → ``open_dialog="effort"``) to preserve its richer ``EffortPickerScreen``.
This command serves the surfaces that *consult the registry*: the REPL (numbered-menu
``select``), the SDK (``NullUIHost``), and the help/aggregator listings — where
``/effort`` was previously invisible because it lived only in the TUI's private
``LOCAL_BUILTINS``.

**Unlike ``/theme``, ``/effort`` has a real headless keystone.** TS ``call`` accepts
args (``/effort high``, ``current``, ``help``) that need **no UI**; only the no-args
picker path needs a surface. So the arg paths work on ``NullUIHost`` (SDK); only the
picker raises there.

**Faithfulness to TS (``effort.tsx``):**
  * Branches mirror ``call`` (``:176-190``): help (``:178-180``), ``current``/``status``
    (``:182-183``), no-args ⇒ picker (``:185-186``), else ``executeEffort`` (``:108-123``).
  * **Two distinct ``auto`` messages**, preserved verbatim: the *picker* auto path emits
    ``"Set effort level to auto: Use default effort level for your model"`` (TS ``:213``
    with ``effort=undefined``); the *arg* auto/unset path emits ``"Effort level set to
    auto"`` (TS ``unsetEffortLevel`` ``:102``).
  * Every TS ``onDone`` passes **no options** ⇒ ``createUserMessage`` (model-visible,
    ``shouldQuery ?? false``), so **all** outcomes map to ``display="user"`` — including
    ``Cancelled`` and help (this differs from ``/theme``'s ``system`` cancel). ``display``
    is behaviorally inert on today's surfaces (engine maps both to ``result_type="text"``).

**Persistence:** writes ``settings.effort`` via :func:`src.config.set_effort` (the
validated settings channel, mirroring TS ``updateSettingsForSource('userSettings',
{effortLevel})``). The persisted value IS consumed by inference on the Anthropic
first-party path: ``resolve_thinking_effort`` (src/query/query.py) reads it at the
wire boundary whenever no explicit per-session effort (``--effort`` /
``QueryParams.thinking_effort``) is set, and forwards it as ``output_config.effort``
on effort-capable models. (``CallModelOptions.effort`` in the legacy services layer
remains unwired.)

**Deliberate divergences (documented for parity review):**
  * **No ``CLAUDE_CODE_EFFORT_LEVEL`` env override, no model-default resolver.**
    Python's effort domain (``settings.constants.VALID_EFFORT_VALUES``) is
    ``""``/low/medium/high/xhigh/max — the full Claude ladder; per-model wire
    acceptance of ``xhigh`` is handled downstream by ``resolve_thinking_effort``
    (clamps to ``high`` where rejected). The TS machinery for OpenAI/env/
    model-default has no functional Python analog (``src/utils/effort.py`` is a
    separate, test-only enum), so ``current`` reports plain
    ``"Effort level: auto"`` (no TS "(currently X)").
  * **Case-insensitive args** (``a.lower()``), slightly more lenient than TS (which is
    case-sensitive for ``help``/``current``/``status``).
  * **Levels come from ``VALID_EFFORT_VALUES``** (the single settings source of truth,
    imported lazily — see :func:`_levels`), so the command and the settings validator never
    drift. The live TUI ``EffortPickerScreen`` offers ``auto/low/medium/high`` (no ``max``)
    — a pre-existing divergence, not reconciled here.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import set_effort

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
    UIOption,
)

COMMON_HELP_ARGS = frozenset({"help", "-h", "--help"})

# Verbatim from TS getEffortLevelDescription (utils/effort.ts:290-303). Used in the
# outcome messages (NOT the help text, which has its own shorter blurbs — TS effort.tsx:179).
_DESCRIPTIONS: dict[str, str] = {
    "low": "Quick, straightforward implementation with minimal overhead",
    "medium": "Balanced approach with standard implementation and testing",
    "high": "Comprehensive implementation with extensive testing and documentation",
    "xhigh": "Deeper reasoning (models that support it; else treated as high)",
    "max": "Maximum capability with deepest reasoning",
}

# TS effort.tsx:213: picker auto-pick description when effort is undefined.
_AUTO_PICKER_DESC = "Use default effort level for your model"

# workflow-engine §4.1: `/effort ultracode` enables the session-long workflow
# auto-orchestration mode (not a persisted effort level — Python's effort
# pipeline is inert, so this contributes the orchestration mode only).
_ULTRACODE_ON_MSG = (
    "Ultracode on: I'll plan and run a workflow (fan out subagents + verify) for "
    "substantive tasks this session, instead of working turn by turn. "
    "Reset with /effort high."
)


def _valid_options_str() -> str:
    base = "low, medium, high, xhigh, max, auto"
    from src.workflow.gating import is_workflows_enabled

    return f"{base}, ultracode" if is_workflows_enabled() else base


def _invalid_msg(raw: str) -> str:
    return f"Invalid argument: {raw}. Valid options are: {_valid_options_str()}"

# TS effort.tsx:179 help text. xhigh/max availability is model-dependent;
# resolve_thinking_effort clamps xhigh to high on models that reject it
# (wire-probed 2026-07-18: opus-4-8 accepts xhigh; sonnet-4-6/opus-4-6 400).
_USAGE = (
    "Usage: /effort [low|medium|high|xhigh|max|auto]\n\n"
    "Effort levels:\n"
    "- low: Quick, straightforward implementation\n"
    "- medium: Balanced approach with standard testing\n"
    "- high: Comprehensive implementation with extensive testing\n"
    "- xhigh: Deeper reasoning (models that support it; else treated as high)\n"
    "- max: Maximum capability with deepest reasoning\n"
    "- auto: Use the default effort level for your model"
)


def _levels() -> tuple[str, ...]:
    """The persistable effort levels (``low, medium, high, max``) — the single source
    of truth ``VALID_EFFORT_VALUES`` minus the empty ``""`` (which is ``auto``).

    Imported lazily so a bare ``import src.command_system`` does not pull the settings
    stack at module-import time (the discipline used by ``app.py``/the advisor hook)."""
    from src.settings.constants import VALID_EFFORT_VALUES

    return tuple(v for v in VALID_EFFORT_VALUES if v)


def _settings_effort() -> str:
    """The persisted effort (``""`` when unset/auto), read via the same ``get_settings()``
    channel the rest of the app uses. Lazy import — see :func:`_levels`."""
    from src.settings.settings import get_settings

    return get_settings().effort


def _current_effort() -> str:
    """Effort to pre-highlight in the picker — the persisted level, or ``"auto"``."""
    return _settings_effort() or "auto"


def _effort_options(current: str) -> list[UIOption]:
    """Picker options: ``auto`` plus the persistable levels, marking the option equal to
    ``current`` with ``description="current"`` (the same marker ``/theme`` uses). Labels
    are the raw values."""
    options: list[UIOption] = [
        UIOption(
            value="auto",
            label="auto",
            description="current" if current == "auto" else None,
        )
    ]
    for level in _levels():
        options.append(
            UIOption(
                value=level,
                label=level,
                description="current" if level == current else None,
            )
        )
    # Ultracode (workflow-engine §4.1) — workflow auto-orchestration mode. Only in
    # the menu when workflows are enabled (§4.8: removed when off).
    from src.workflow.gating import is_workflows_enabled
    from src.workflow.ultracode import is_ultracode_session

    if is_workflows_enabled():
        options.append(
            UIOption(
                value="ultracode",
                label="ultracode",
                description="on" if is_ultracode_session() else "workflow auto-orchestration",
            )
        )
    return options


def _show_current() -> str:
    """TS ``showCurrentEffort`` (simplified — no env override, no model-default resolver)."""
    eff = _settings_effort()
    if not eff:
        return "Effort level: auto"
    return f"Current effort level: {eff} ({_DESCRIPTIONS.get(eff, '')})"


@dataclass(frozen=True)
class EffortCommand(InteractiveCommand):
    """Set the reasoning-effort level and persist it.

    Frozen + no new fields (the ``ThemeCommand``/``ExportCommand`` pattern); behavior
    lives entirely in :meth:`run`.
    """

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        raw = (args or "").strip()
        a = raw.lower()

        # 1. help (headless — TS effort.tsx:178-180).
        if a in COMMON_HELP_ARGS:
            return InteractiveOutcome(message=_USAGE, display="user")

        # 2. current/status (headless — TS :182-183).
        if a in ("current", "status"):
            return InteractiveOutcome(message=_show_current(), display="user")

        # 3. no args ⇒ picker (TS :185-186). The only path that needs a UI surface;
        #    on NullUIHost the select below raises -> engine returns a clean error.
        if not a:
            current = _current_effort()
            picked = await context.ui.select(
                "Set reasoning effort:", _effort_options(current), current=current
            )
            if picked is None:  # TS handleCancel -> onDone('Cancelled') (no options -> user).
                return InteractiveOutcome(message="Cancelled", display="user")
            from src.workflow.ultracode import set_ultracode_session

            if picked == "ultracode":
                set_ultracode_session(True)
                return InteractiveOutcome(message=_ULTRACODE_ON_MSG, display="user")
            # Any real effort choice exits ultracode mode (spec: "reset with /effort high").
            set_ultracode_session(False)
            if picked == "auto":
                set_effort(None)
                # TS effort.tsx:213 (effort=undefined).
                return InteractiveOutcome(
                    message=f"Set effort level to auto: {_AUTO_PICKER_DESC}",
                    display="user",
                )
            set_effort(picked)
            return InteractiveOutcome(
                message=f"Set effort level to {picked}: {_DESCRIPTIONS[picked]}",
                display="user",
            )

        # 4. explicit arg (headless — TS executeEffort :108-123).
        from src.workflow.ultracode import set_ultracode_session

        if a == "ultracode":
            # workflow-engine §4.1 / §4.8: only valid when workflows are enabled.
            from src.workflow.gating import is_workflows_enabled

            if not is_workflows_enabled():
                return InteractiveOutcome(message=_invalid_msg(raw), display="user")
            set_ultracode_session(True)
            return InteractiveOutcome(message=_ULTRACODE_ON_MSG, display="user")
        if a in ("auto", "unset"):
            set_effort(None)  # TS unsetEffortLevel.
            set_ultracode_session(False)
            return InteractiveOutcome(message="Effort level set to auto", display="user")
        if a in _levels():
            set_effort(a)  # TS setEffortValue (env-override / session-only branches dropped).
            set_ultracode_session(False)  # selecting a real level exits ultracode
            return InteractiveOutcome(
                message=f"Set effort level to {a}: {_DESCRIPTIONS[a]}", display="user"
            )
        # TS :120-122 (minus xhigh). Use the trimmed original-case arg in the message.
        return InteractiveOutcome(message=_invalid_msg(raw), display="user")


EFFORT_COMMAND = EffortCommand(
    name="effort",
    description="Set effort level for model usage",  # verbatim TS index.ts
    argument_hint="[low|medium|high|xhigh|max|auto]",  # TS index.ts
)


__all__ = [
    "EFFORT_COMMAND",
    "EffortCommand",
]
