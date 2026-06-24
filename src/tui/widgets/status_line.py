"""Status line widget — parity with ``StatusLine.tsx`` + ``SpinnerWithVerb``.

The ink reference renders a single line at the bottom of the transcript
that shows:

* On the left: provider · model.
* In the middle: the current "verb" (``Synthesizing…``, ``Gathering…``,
  ``Running bash…``) paired with an animated spinner while the agent is
  busy, plus an elapsed-time indicator.
* On the right: turn count, queued prompts pill, and usage (total
  tokens) once a run has completed.

Phase 1 renders this as a static-looking Rich ``Text`` updated by an
interval timer. The animation is strictly cosmetic — the authoritative
"am I busy?" signal comes from :class:`AppState.is_thinking`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..state import AppState

if TYPE_CHECKING:
    from .prompt_input_footer import PromptInputFooter


# The signature Claude Code "sparkle" spinner: a density ramp of the six
# glyphs (darwin set; other platforms swap ✽→*), animated as a ping-pong —
# base frames followed by their reverse, advanced one step per tick. This
# replaces the old generic braille spinner with the recognizable ink look.
_SPINNER_BASE = ["·", "✢", "✳", "✶", "✻", "✽"]
_SPINNER_FRAMES = _SPINNER_BASE + list(reversed(_SPINNER_BASE))


def _format_elapsed(secs: int) -> str:
    """``Ns`` under a minute, else ``Nm Ns`` (TS ``formatDuration``)."""
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _format_token_count(n: int) -> str:
    """Compact count approximating TS ``formatNumber`` (``1321`` → ``1.3k``).

    A deliberate approximation of ``Intl`` compact notation, not a faithful
    port. The ``< 999_950`` bound keeps the ``k`` branch from rounding up to
    ``1000.0k`` at one decimal — that boundary rolls into ``m`` instead.
    """
    if n < 1000:
        return str(n)
    if n < 999_950:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}m"


class StatusLine(Static):
    """Single-line status footer."""

    DEFAULT_CSS = """
    StatusLine {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    turns: reactive[int] = reactive(0)
    is_thinking: reactive[bool] = reactive(False)
    queued: reactive[int] = reactive(0)
    workflows: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        workspace_root: Path,
        app_state: AppState | None = None,
        provider_instance: object | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._workspace_root = Path(workspace_root)
        self._app_state = app_state
        # Live provider instance (BaseProvider) — used ONLY for the
        # advisor status segment so ``format_advisor_status`` can call
        # ``is_advisor_enabled(provider)`` and pick the right mode
        # label. Optional: when omitted, the advisor segment shows
        # "(client)" as the conservative default for any configured
        # advisor (since SERVER_SIDE requires the instance to verify
        # first-party).
        self._provider_instance = provider_instance
        self._frame = 0
        self._timer = None
        # Optional hint footer driven off the same is_thinking signal, so
        # "esc to interrupt" appears exactly while the spinner animates
        # (single source of truth — see bind_footer / watch_is_thinking).
        self._footer: "PromptInputFooter | None" = None
        self._footer_loading = False
        # C3a: output of the user's settings ``statusLine.command`` hook
        # (TS StatusLine.tsx). None = unconfigured/failed → default row.
        self._custom_status: str | None = None
        self._custom_timer = None
        initial = Text(f"{provider} · {model}    ready    turn 0")
        super().__init__(initial, markup=False)

    # ---- lifecycle ----
    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / 10, self._tick)
        # Custom statusline refresh. Divergence from TS (300ms debounce on
        # message/mode/model changes): Python refreshes on AgentRunFinished
        # (repl.py calls refresh_custom_status) plus a SLOW 30s keepalive
        # for settings hot-reload — not a hot poll; the user's command
        # shouldn't run hundreds of times an hour in an idle session.
        self._custom_timer = self.set_interval(
            30.0, self.refresh_custom_status
        )
        self.refresh_custom_status()
        self._redraw()

    def refresh_custom_status(self) -> None:
        """Kick the statusline-command worker (event-driven + keepalive)."""

        try:
            self.run_worker(
                self._run_custom_status_command,
                thread=True,
                exclusive=True,
                exit_on_error=False,
                group="statusline-command",
            )
        except Exception:
            pass

    def _run_custom_status_command(self) -> None:
        # Blanket-guarded: Textual workers default to exit_on_error=True,
        # and ANY escaped exception here (codec errors, app teardown
        # races) would otherwise take down the whole TUI — the one thing
        # a status bar must never do.
        try:
            text = self._compute_custom_status()
        except Exception:
            text = None
        try:
            self.app.call_from_thread(self._set_custom_status, text)
        except Exception:
            pass  # app shutting down mid-flight

    def _compute_custom_status(self) -> str | None:
        from src.services.status_line_command import (
            build_status_line_input,
            execute_status_line_command,
            read_status_line_config,
        )

        cwd = str(self._workspace_root)
        config = read_status_line_config(cwd)
        if config is None:
            text = None
        else:
            state = self._app_state
            usage = state.usage if state else {}
            try:
                from src.bootstrap.state import get_session_id

                session_id = str(get_session_id())
            except Exception:
                session_id = ""
            try:
                from src.models import get_context_window_for_model

                window = int(get_context_window_for_model(self._model) or 0)
            except Exception:
                window = 0
            text = execute_status_line_command(
                build_status_line_input(
                    model_id=self._model,
                    cwd=cwd,
                    session_id=session_id,
                    total_input_tokens=int(usage.get("input_tokens", 0) or 0),
                    total_output_tokens=int(usage.get("output_tokens", 0) or 0),
                    last_turn_input_tokens=int(
                        getattr(state, "last_turn_input_tokens", 0) or 0
                    ),
                    context_window_size=window,
                ),
                cwd=cwd,
                config=config,
            )
        return text

    def _set_custom_status(self, text: str | None) -> None:
        if text != self._custom_status:
            self._custom_status = text
            self._redraw()

    def _tick(self) -> None:
        if self._app_state is not None:
            self.is_thinking = self._app_state.is_thinking
            self.queued = len(self._app_state.queued_prompts)
        self.workflows = self._count_workflows()
        if self.is_thinking:
            self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self._redraw()

    def _count_workflows(self) -> int:
        """Number of running/pending workflow runs (for the footer pill)."""
        try:
            tool_context = getattr(self.app, "tool_context", None)
            if tool_context is None:
                return 0
            return sum(
                1
                for t in tool_context.runtime_tasks.all()
                if getattr(t, "type", None) == "local_workflow"
                and getattr(t, "status", "") in ("running", "pending")
            )
        except Exception:
            return 0

    # ---- public API ----
    def bind_state(self, state: AppState) -> None:
        self._app_state = state
        self._redraw()

    def bind_footer(self, footer: "PromptInputFooter") -> None:
        """Attach the prompt hint footer so it tracks ``is_thinking``.

        The footer's "esc to interrupt" hint must appear exactly while the
        agent is busy. Rather than re-derive busy in the REPL screen, the
        footer rides the same reactive the spinner does (``watch_is_thinking``
        fires from the tick that polls ``AppState.is_thinking`` and from the
        explicit ``set_busy``/``set_idle`` setters).
        """

        self._footer = footer
        self._sync_footer_loading()

    def _sync_footer_loading(self) -> None:
        if self._footer is None:
            return
        if self.is_thinking == self._footer_loading:
            return
        self._footer_loading = self.is_thinking
        try:
            self._footer.set_loading(self.is_thinking)
        except Exception:
            pass

    def refresh_identity(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        if provider is not None:
            self._provider = provider
        if model is not None:
            self._model = model
        self._redraw()

    def bump_turn(self) -> None:
        self.turns += 1

    def set_busy(self, verb: str = "") -> None:
        # Empty verb → AppState.set_thinking samples a random SPINNER_VERBS
        # entry (TS Spinner.tsx:166), instead of a fixed "Synthesizing".
        self.is_thinking = True
        if self._app_state is not None:
            self._app_state.set_thinking(True, verb=verb)

    def set_idle(self) -> None:
        self.is_thinking = False
        if self._app_state is not None:
            self._app_state.set_thinking(False)

    # ---- render ----
    def watch_is_thinking(self, _: bool) -> None:
        self._sync_footer_loading()
        self._redraw()

    def watch_turns(self, _: int) -> None:
        self._redraw()

    def watch_queued(self, _: int) -> None:
        self._redraw()

    def watch_workflows(self, _: int) -> None:
        self._redraw()

    def _redraw(self) -> None:
        spinner = (
            _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
            if self.is_thinking
            else " "
        )
        self.update(self._compose_text(spinner=spinner))

    def _busy_middle(self, spinner: str, verb: str) -> str:
        """Render the working indicator: ``✶ Synthesizing… (5s · ↓ 1.2k tokens)``.

        Ports the ink ``SpinnerAnimationRow`` look: glyph + verb + ``…`` +
        a parenthesized status group with elapsed time and a live token
        estimate (``round(chars/4)`` from the streamed response, the same
        ``leaderTokens`` heuristic TS uses). Divergence from TS
        ``SHOW_TOKENS_AFTER_MS`` (30s gate): TS hides this group on its
        separate floating spinner row for the first 30s; the Python
        status-line middle is the ONLY spinner surface and already showed
        elapsed from 1s, so we keep live feedback from the start (tokens
        appear once output streams). Token feedback is naturally absent
        when ``/stream`` is off (no deltas arrive).
        """

        state = self._app_state
        parts: list[str] = []
        if state and state.verb_started_at:
            secs = int(time.time() - state.verb_started_at)
            if secs >= 1:
                parts.append(_format_elapsed(secs))
        if state and state.streaming_text:
            # TS leaderTokens = round(responseChars / 4) (SpinnerAnimationRow).
            tok = round(len(state.streaming_text) / 4)
            if tok > 0:
                parts.append(f"↓ {_format_token_count(tok)} tokens")
        tail = f" ({' · '.join(parts)})" if parts else ""
        return f"{spinner} {verb}…{tail}"

    def _compose_text(self, *, spinner: str) -> Text:
        state = self._app_state
        verb = state.verb if state else ("thinking" if self.is_thinking else "ready")

        # TS parity: a configured statusLine command REPLACES the default
        # row content; activity feedback (spinner + verb) is kept so the
        # user still sees that the agent is working.
        if self._custom_status:
            if self.is_thinking:
                return Text(f"{self._busy_middle(spinner, verb)}    {self._custom_status}")
            return Text(self._custom_status)

        left_parts = [f"{self._provider} · {self._model}"]
        # Optional advisor segment — appears next to provider/model
        # when ``/advisor`` is configured. Mode label reflects what
        # the NEXT request will actually do (server/client/inactive)
        # so a stale config under an unsupported provider doesn't
        # silently lie. Shared formatter with the legacy REPL
        # bottom_toolbar so both surfaces render identically.
        try:
            from src.utils.advisor import format_advisor_status
            # Pass the live provider instance when available so the
            # mode label (server/client/inactive) reflects what the
            # next request will actually do. Falls back to None (=
            # "client" default) when the instance isn't plumbed.
            advisor_seg = format_advisor_status(
                self._provider_instance, self._model,
            )
        except Exception:
            advisor_seg = None
        if advisor_seg:
            left_parts.append(advisor_seg)
        left = " · ".join(left_parts)
        cwd = self._display_cwd()
        middle = self._busy_middle(spinner, verb) if self.is_thinking else verb
        right_bits: list[str] = [f"turn {self.turns}"]
        if self.queued:
            right_bits.append(f"queued {self.queued}")
        if self.workflows:
            right_bits.append(
                "1 background workflow" if self.workflows == 1 else f"{self.workflows} background workflows"
            )
        if state and state.usage:
            in_t = state.usage.get("input_tokens", 0)
            out_t = state.usage.get("output_tokens", 0)
            total = in_t + out_t
            if total:
                right_bits.append(f"tokens {total}")
            # C3a context-% segment: live context = LAST response's
            # prompt-side tokens vs the model's window (TS StatusLine
            # context_window.used_percentage). Hidden until the first
            # response lands.
            last_ctx = getattr(state, "last_turn_input_tokens", 0)
            if last_ctx:
                try:
                    from src.services.token_warning import (
                        calculate_token_warning_state,
                    )

                    tw = calculate_token_warning_state(last_ctx, self._model)
                    if tw.context_window > 0:
                        seg = f"ctx {100 - tw.percent_left}%"
                        if tw.is_above_warning:
                            seg += " ⚠"
                        right_bits.append(seg)
                except Exception:
                    pass
            # Advisor token segment — appears next to worker tokens
            # whenever the advisor has been consulted this session.
            # ``state.usage["advisor_*"]`` is mirrored from
            # ``tool_context.advisor_*`` by ``agent_bridge.py`` after
            # each run; the underlying ctx counter is accumulated by
            # ``AdvisorTool._advisor_call`` on every consultation.
            # Hidden when zero so the bar stays compact for users who
            # haven't enabled the advisor yet.
            adv_in = state.usage.get("advisor_input_tokens", 0)
            adv_out = state.usage.get("advisor_output_tokens", 0)
            if adv_in or adv_out:
                right_bits.append(f"advisor {adv_in}/{adv_out}")
            # USD cost segment — uses the shared compute_session_cost
            # helper so REPL and TUI render identical numbers for the
            # same usage. Directional estimate based on upstream model
            # prices; proxies (litellm/openrouter/bedrock) may bill
            # differently. Hidden when zero.
            try:
                from src.services.pricing import (
                    compute_session_cost,
                    format_cost_usd,
                )
                from src.settings.settings import get_settings
                _adv_model = (
                    getattr(get_settings(), "advisor_model", "") or ""
                ).strip()
                _, _, total_cost = compute_session_cost(
                    worker_model=self._model,
                    worker_input_tokens=in_t,
                    worker_output_tokens=out_t,
                    advisor_model=_adv_model,
                    advisor_input_tokens=adv_in,
                    advisor_output_tokens=adv_out,
                )
                if total_cost > 0:
                    right_bits.append(f"cost {format_cost_usd(total_cost)}")
            except Exception:
                pass
        right = " · ".join(right_bits)
        return Text(f"{left}    {middle}    {cwd}    {right}")

    def _display_cwd(self) -> str:
        try:
            home = Path.home()
            rel = self._workspace_root.relative_to(home)
            return f"~/{rel}" if str(rel) != "." else "~"
        except Exception:
            return str(self._workspace_root)
