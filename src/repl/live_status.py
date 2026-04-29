"""Live spinner + active input field for the REPL's ``chat()`` body.

This is the Python analogue of the bottom region of the TS Ink reference UI:
two always-visible rows pinned to the bottom of the terminal —

* a spinner row showing the current status message ("Thinking…", queued
  count, etc.), and
* a real, editable input row so the user can keep typing while the agent
  works. Submitting a message during agent work *queues* it for the REPL
  to pick up after the current ``chat()`` call returns; ESC (and Ctrl+C)
  cancel the in-flight ``AbortController`` at the next safe boundary.

Architecture:

* ``prompt_toolkit.Application(full_screen=False)`` renders only the bottom
  rows and leaves prior stdout in scrollback — exactly what we want.
* The Application runs in its own background thread with a private asyncio
  event loop, so the synchronous chat body in :class:`ClawcodexREPL.chat`
  can keep using ``loop.run_until_complete(_run_query())`` without giving
  up the main thread.
* ``patch_stdout()`` (applied by the caller) keeps ``rich.console.print``
  output flowing above the live rows without tearing.

The cancel callback is invoked synchronously from the key handler; the
target (typically :meth:`QueryEngine.interrupt`) is responsible for
signaling the existing :class:`src.utils.abort_controller.AbortController`
so the in-flight tool loop and HTTP stream tear down cleanly. The submit
callback is invoked with the buffer text whenever the user presses Enter
on a non-empty line.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from src.utils.format import format_duration, format_number

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import (
        ConditionalContainer,
        Float,
        FloatContainer,
        HSplit,
        VSplit,
        Window,
    )
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.filters import has_completions
    from prompt_toolkit.styles import Style

    _HAS_PROMPT_TOOLKIT = True
except ModuleNotFoundError:  # pragma: no cover - guarded by REPL bootstrap
    _HAS_PROMPT_TOOLKIT = False


# Braille spinner frames matching ``rich``'s ``dots`` spinner. Hard-coding
# avoids pulling rich's spinner internals across a process boundary and
# keeps the live region self-contained.
_SPINNER_FRAMES: tuple[str, ...] = (
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
)
_FRAME_INTERVAL = 0.08
# Mirrors ``SHOW_TOKENS_AFTER_MS`` in
# ``typescript/src/components/Spinner/SpinnerAnimationRow.tsx``.
_SHOW_TIMER_AFTER_MS = 30_000


class LiveStatus:
    """Bottom-row spinner + editable input field for ``chat()``.

    Use as a context manager::

        def _cancel() -> None:
            try:
                engine.interrupt()
            except Exception:
                pass

        def _submit(text: str) -> None:
            queued.append(text)

        with patch_stdout():
            with LiveStatus("Thinking…", on_cancel=_cancel, on_submit=_submit):
                response_text = run_query()

    ``on_submit`` is invoked from the prompt_toolkit thread whenever the
    user presses Enter on a non-empty line; the buffer is cleared after
    each submit so the field is ready for the next message. Calling
    :meth:`update` from any thread changes the visible status message on
    the next frame.
    """

    def __init__(
        self,
        message: str,
        *,
        on_cancel: Callable[[], None] | None = None,
        on_submit: Callable[[str], None] | None = None,
        on_expand: Callable[[], None] | None = None,
        completer=None,
        verbose: bool = False,
    ) -> None:
        if not _HAS_PROMPT_TOOLKIT:
            raise RuntimeError(
                "prompt_toolkit is required for LiveStatus; install it or "
                "fall back to console.status",
            )
        self._message = message
        self._on_cancel = on_cancel
        self._on_submit = on_submit
        self._on_expand = on_expand
        # Optional ``prompt_toolkit.completion.Completer``. When set,
        # the live input buffer surfaces completions (e.g. ``@`` file
        # mentions, slash commands) in a popup above the input row —
        # parity with the foreground ``PromptSession``.
        self._completer = completer
        self._frame_index = 0
        self._app: Application | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._input_buffer: Buffer | None = None
        # Tracks the wall-clock origin for the spinner's elapsed-time
        # readout (mirrors ``loadingStartTimeRef`` in the TS spinner).
        # Set in ``__enter__`` / ``paused.__exit__`` and cleared in
        # ``_stop`` so a paused-and-resumed cycle restarts the timer.
        self._started_at: float | None = None
        # Latest token total surfaced via :meth:`set_tokens`. Mirrors
        # ``responseLengthRef.current / 4`` + teammate sum from
        # ``SpinnerAnimationRow.tsx``.
        self._tokens: int = 0
        # Force-show the elapsed/token suffix before the 30s threshold.
        # Maps to the TS ``verbose`` prop in ``SpinnerWithVerb``.
        self._verbose = verbose

    # ---- public API ----
    def update(self, message: str) -> None:
        """Change the visible status text. Safe to call from any thread."""

        with self._lock:
            self._message = message
        self._invalidate()

    def set_tokens(self, n: int) -> None:
        """Update the token count shown in the spinner suffix.

        Safe to call from any thread. Pass the running per-turn total
        (input + output tokens) — the spinner re-renders on the next
        frame tick. Mirrors how the TS spinner reads
        ``responseLengthRef.current / 4`` each frame.
        """

        with self._lock:
            if n == self._tokens:
                return
            self._tokens = max(0, int(n))
        self._invalidate()

    def __enter__(self) -> "LiveStatus":
        self._started_at = time.monotonic()
        self._tokens = 0
        self._thread = threading.Thread(
            target=self._run_thread,
            name="clawcodex-live-status",
            daemon=True,
        )
        self._thread.start()
        # Block briefly so callers can rely on the spinner being mounted by
        # the time chat() starts streaming output.
        self._ready.wait(timeout=1.0)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop()

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Temporarily release the terminal so other ``prompt_toolkit`` code
        can run.

        Two ``prompt_toolkit.Application`` instances cannot share a TTY —
        when ``LiveStatus`` is mounted in its background thread and a
        synchronous caller (e.g. the permission prompt) tries to launch
        ``prompt(...)`` from the foreground, the inputs interleave and the
        screen tears (the spinner keeps overwriting the user's keystrokes).
        Wrap the foreground prompt in ``with status.paused(): ...`` so the
        live region tears down cleanly first and is restored after.
        """

        message = self._message
        on_cancel = self._on_cancel
        on_submit = self._on_submit
        on_expand = self._on_expand
        completer = self._completer
        # Preserve the timer / token counter across the pause so the
        # spinner picks up where it left off after the foreground prompt
        # finishes. ``_stop`` clears ``_started_at``; capture first.
        started_at = self._started_at
        tokens = self._tokens
        self._stop()
        try:
            yield
        finally:
            self._message = message
            self._on_cancel = on_cancel
            self._on_submit = on_submit
            self._on_expand = on_expand
            self._completer = completer
            self._frame_index = 0
            self._started_at = started_at
            self._tokens = tokens
            self._ready = threading.Event()
            self._thread = threading.Thread(
                target=self._run_thread,
                name="clawcodex-live-status",
                daemon=True,
            )
            self._thread.start()
            self._ready.wait(timeout=1.0)

    # ---- internals ----
    def _run_thread(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        bindings = KeyBindings()

        @bindings.add("escape", eager=True)
        @bindings.add("c-c")
        def _on_cancel(event):  # type: ignore[no-untyped-def]
            cb = self._on_cancel
            if cb is None:
                return
            try:
                cb()
            except Exception:
                pass

        @bindings.add("c-m")
        def _enter(event):  # type: ignore[no-untyped-def]
            """Enter: accept a highlighted completion, else submit.

            Mirrors the foreground PromptSession's behavior so the
            completion popup feels uniform across the live and idle
            input rows. ``current_completion`` is set when the user
            has navigated into the menu via Tab / Up / Down; if it's
            None we fall through to the buffer's ``accept_handler``.
            """

            buf = event.current_buffer
            state = buf.complete_state
            if state is not None and state.current_completion is not None:
                buf.apply_completion(state.current_completion)
                return
            if state is not None:
                buf.complete_state = None
            buf.validate_and_handle()

        @bindings.add("c-o")
        def _on_expand(event):  # type: ignore[no-untyped-def]
            cb = self._on_expand
            if cb is None:
                return
            # ``run_in_terminal`` schedules the print outside the
            # rendering loop so the expansion lands above the live
            # region instead of fighting the spinner row's redraw.
            try:
                from prompt_toolkit.application import run_in_terminal
                run_in_terminal(cb)
            except Exception:
                try:
                    cb()
                except Exception:
                    pass

        # Editable input field — accepts keystrokes during agent work and
        # queues submissions back to the REPL via ``on_submit``.
        def _accept(buf: "Buffer") -> bool:
            text = buf.text
            if not text.strip():
                # Stay on the same line; clearing here would feel like the
                # input was eaten.
                return False
            cb = self._on_submit
            if cb is not None:
                try:
                    cb(text)
                except Exception:
                    pass
            buf.text = ""
            buf.cursor_position = 0
            self._invalidate()
            # ``True`` would close the application; we want the field to
            # stay open so the user can queue further messages.
            return False

        self._input_buffer = Buffer(
            multiline=False,
            accept_handler=_accept,
            completer=self._completer,
            complete_while_typing=self._completer is not None,
        )

        spinner_control = FormattedTextControl(
            text=self._render_spinner_text,
            focusable=False,
            show_cursor=False,
        )
        prompt_marker_control = FormattedTextControl(
            text=lambda: FormattedText([("class:prompt", "❯ ")]),
            focusable=False,
            show_cursor=False,
        )
        input_control = BufferControl(buffer=self._input_buffer)

        # The prompt marker + buffer share a ``class:input-row`` style
        # so the dim background fills the full terminal width — that
        # subtle highlight is what visually marks the row as the user
        # input field, replacing the previous explicit divider lines
        # (which left a horizontal rule in scrollback after every
        # prompt). Matches the input background Claude Code uses.
        #
        # Wrapping the layout in a ``FloatContainer`` lets us anchor a
        # ``CompletionsMenu`` above the input row when the user types
        # ``@`` (or ``/``) — the menu floats over the spinner row
        # without changing the row layout.
        body = HSplit(
            [
                Window(content=spinner_control, height=Dimension.exact(1)),
                VSplit(
                    [
                        Window(
                            content=prompt_marker_control,
                            width=Dimension.exact(2),
                            style="class:input-row",
                        ),
                        Window(
                            content=input_control,
                            height=Dimension.exact(1),
                            style="class:input-row",
                        ),
                    ]
                ),
            ]
        )
        floats: list[Float] = []
        if self._completer is not None:
            floats.append(
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=ConditionalContainer(
                        content=CompletionsMenu(max_height=12, scroll_offset=1),
                        filter=has_completions,
                    ),
                )
            )
        layout = Layout(
            container=FloatContainer(content=body, floats=floats),
            focused_element=input_control,
        )

        style = Style.from_dict(
            {
                # ``input-row`` is the dim slab behind the prompt
                # marker + editable buffer; the matching background on
                # ``prompt`` keeps the ``❯`` arrow tonally consistent
                # with its row instead of looking like a floating
                # foreground glyph.
                "input-row": "bg:#262626",
                "prompt": "bold fg:ansiblue bg:#262626",
                "spinner": "fg:ansicyan",
                "status": "",
                "hint": "fg:#888888",
            }
        )

        self._app = Application(
            layout=layout,
            key_bindings=bindings,
            style=style,
            full_screen=False,
            mouse_support=False,
            erase_when_done=True,
            refresh_interval=_FRAME_INTERVAL,
        )

        self._ready.set()
        try:
            loop.run_until_complete(self._app.run_async())
        except Exception:
            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def _render_spinner_text(self) -> "FormattedText":
        with self._lock:
            message = self._message
            started_at = self._started_at
            tokens = self._tokens
            verbose = self._verbose
        frame = _SPINNER_FRAMES[self._frame_index % len(_SPINNER_FRAMES)]
        self._frame_index += 1

        # Match ``SpinnerAnimationRow.tsx``'s suffix:
        # ``(esc to interrupt · 12s · ↓ 1.2k tokens)``.
        # Timer + token suffix gated by 30s elapsed (or ``verbose``),
        # tokens additionally require a non-zero count.
        elapsed_ms = (time.monotonic() - started_at) * 1000 if started_at else 0.0
        wants_timer = verbose or elapsed_ms > _SHOW_TIMER_AFTER_MS
        suffix = "  (esc to interrupt"
        if wants_timer:
            suffix += f" · {format_duration(elapsed_ms)}"
            if tokens > 0:
                suffix += f" · ↓ {format_number(tokens)} tokens"
        suffix += ")"

        return FormattedText(
            [
                ("class:spinner", frame),
                ("", " "),
                ("class:status", message),
                ("class:hint", suffix),
            ]
        )

    def _invalidate(self) -> None:
        app = self._app
        if app is None:
            return
        try:
            app.invalidate()
        except Exception:
            pass

    def _stop(self) -> None:
        app = self._app
        loop = self._loop
        if app is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(app.exit)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._app = None
        self._loop = None
        self._thread = None
        self._input_buffer = None
        # Cleared so a fresh ``__enter__`` after a full teardown starts
        # the elapsed timer from zero. ``paused()`` snapshots and
        # restores this so its pause/resume cycle preserves the timer.
        self._started_at = None
        self._tokens = 0
