"""Headless (non-interactive) entrypoint.

Port of ``typescript/src/cli/print.ts``, scoped to the slice that matters for
Phase 1: run a single prompt (or a stream of prompts via stream-json stdin)
through the agent loop and emit the response in the requested output format.

The heavy lifting lives in :mod:`src.query.query` (the canonical agent
loop), driven via the sync wrapper
:func:`src.query.agent_loop_compat.run_query_as_agent_loop`. That loop
already understands Anthropic + OpenAI-compatible providers and emits
structured tool events; this module adapts those events to the CLI
protocol in :mod:`src.cli_core`.

Design notes
------------
* No Rich / prompt_toolkit imports — headless mode must run on plain pipes
  (CI, SDK clients, tests) without a TTY.
* Tool permission handling is driven by ``--dangerously-skip-permissions``:
  when set, tools run without gating; otherwise the default ``ToolContext``
  mode (``bypassPermissions``) still applies but *interactive* permission
  prompts auto-deny — we never ``input()`` in headless mode.
* The agent loop is synchronous; we call it inside ``run_headless`` and
  translate events to NDJSON on the fly.
"""

from __future__ import annotations

import io
import os
import signal as _signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Iterable, Optional

from src.agent import Session
from src.cli_core import (
    AssistantEvent,
    PartialTextEvent,
    ResultEvent,
    StreamJsonReader,
    StreamJsonWriter,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserInputMessage,
    cli_error,
    ndjson_safe_dumps,
)
from src.config import get_default_provider, get_provider_config
from src.providers import (
    get_provider_class,
    resolve_api_key,
)
from src.tool_system.renderers import AgentLoopResult, ToolEvent
from src.query.agent_loop_compat import (
    build_effective_system_prompt,
    run_query_as_agent_loop,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.utils.abort_controller import AbortController, AbortError


OUTPUT_FORMATS = ("text", "json", "stream-json")
INPUT_FORMATS = ("text", "stream-json")


@dataclass
class HeadlessOptions:
    """Options accepted by :func:`run_headless`.

    Kept as a plain dataclass (no Click/argparse coupling) so the CLI layer
    and tests can construct it independently.
    """

    prompt: str | None = None
    output_format: str = "text"
    input_format: str = "text"
    provider_name: str | None = None
    model: str | None = None
    # ch04 round-4 GAP B — `--fallback-model` (TS cli/print.ts:473):
    # capacity-relief switch after repeated 529s; session-sticky.
    fallback_model: str | None = None
    # ``--effort`` (TS main.tsx:995) — explicit per-session effort level
    # ("low" | "medium" | "high" | "xhigh" | "max"). None = auto
    # (settings.effort; when that's unset too the wire parameter is
    # omitted and the API applies its model default). Resolved per request
    # by ``resolve_thinking_effort`` (which also degrades xhigh to high on
    # models that reject it). Main-loop scope; subagents follow settings.
    effort: str | None = None
    max_turns: int = 50
    # ``skip_permissions`` is a backward-compat alias for the boolean form
    # of ``--dangerously-skip-permissions``. ``permission_mode`` and
    # ``is_bypass_permissions_mode_available`` were added in round 5 to
    # mirror the TS reference's resolved state. When ``skip_permissions``
    # is True we treat it as ``permission_mode='bypassPermissions'`` and
    # ``is_bypass_permissions_mode_available=True``.
    skip_permissions: bool = False
    permission_mode: str = "default"
    is_bypass_permissions_mode_available: bool = False
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    include_partial_messages: bool = False
    verbose: bool = False

    # Mostly for tests: override streams so we can capture output.
    stdin: IO[str] | None = None
    stdout: IO[str] | None = None
    stderr: IO[str] | None = None

    # Workspace root override (default: cwd).
    workspace_root: Path | None = None


def run_headless(options: HeadlessOptions) -> int:
    """Run one or more prompts in headless mode. Returns the exit code."""

    if options.output_format not in OUTPUT_FORMATS:
        cli_error(
            f"error: --output-format must be one of {', '.join(OUTPUT_FORMATS)}", 2
        )
    if options.input_format not in INPUT_FORMATS:
        cli_error(
            f"error: --input-format must be one of {', '.join(INPUT_FORMATS)}", 2
        )
    if options.input_format == "stream-json" and options.output_format != "stream-json":
        cli_error(
            "error: --input-format stream-json requires --output-format stream-json",
            2,
        )

    stdout = options.stdout or sys.stdout
    stderr = options.stderr or sys.stderr
    stdin = options.stdin or sys.stdin

    # ch02 round-3 GAP B: warm the user/system context memos now so the
    # CLAWCODEX.md walk and git probes overlap with provider + registry
    # construction below instead of running inside the first turn.
    # Mirrors TS main.tsx:1973-1990 (non-interactive early kicks; trust
    # is implicit in -p mode and was granted by run_pre_action).
    # MUST use the resolved workspace_root, not the process cwd — the
    # memos are key-less and first-writer pins the content the query
    # path (which passes workspace_root) will read.
    workspace_root = options.workspace_root or Path.cwd()
    from src.deferred_init import start_deferred_prefetches

    start_deferred_prefetches(cwd=str(workspace_root))

    provider_name = options.provider_name or get_default_provider()
    # ENTRY-2: the config-load + key checks moved into the SHARED startup
    # validator (src/entrypoints/provider_validation.py) so this path, the
    # bare-interactive path, and `clawcodex tui` can never drift on message
    # or behavior. Headless is non-interactive → the helper exits(2) on
    # failure with the exact messages this block used to print inline.
    # (cli.main already validated for the `-p` route — this repeat is
    # idempotent defense-in-depth for direct run_headless callers.)
    from src.entrypoints.provider_validation import validate_provider_at_startup

    validate_provider_at_startup(options.provider_name, interactive=False, exit_code=2)
    provider_cfg = get_provider_config(provider_name)  # validated above
    api_key = resolve_api_key(provider_name, provider_cfg)

    provider_cls = get_provider_class(provider_name)
    model = options.model or provider_cfg.get("default_model")
    provider = provider_cls(
        api_key=api_key,
        base_url=provider_cfg.get("base_url"),
        model=model,
    )

    session = Session.create(provider_name, getattr(provider, "model", model or ""))

    tool_registry = build_default_registry(provider=provider)
    if options.allowed_tools:
        allow = {name.lower() for name in options.allowed_tools}
        _filter_registry(tool_registry, keep=lambda n: n.lower() in allow)
    if options.disallowed_tools:
        deny = {name.lower() for name in options.disallowed_tools}
        _filter_registry(tool_registry, keep=lambda n: n.lower() not in deny)

    # (workspace_root already resolved above, before the prefetch kick.)

    # Compute the effective permission context. ``skip_permissions=True`` is
    # the legacy alias and means "user passed --dangerously-skip-permissions";
    # ``permission_mode`` / ``is_bypass_permissions_mode_available`` are the
    # round-5 fields. When skip_permissions wins, force bypass mode + bypass
    # availability so the registry's ``has_permissions_to_use_tool`` check
    # short-circuits to ``allow``.
    from src.permissions.modes import is_bypass_permissions_mode_disabled

    if options.skip_permissions and not is_bypass_permissions_mode_disabled():
        effective_mode: str = "bypassPermissions"
        bypass_available = True
    elif options.skip_permissions:
        # C12: a disableBypassPermissionsMode lockdown overrides
        # --dangerously-skip-permissions — do NOT force bypass mode/availability
        # (TS refuses/skips), fall through to the configured mode + no bypass.
        effective_mode = options.permission_mode or "default"
        bypass_available = False
    else:
        effective_mode = options.permission_mode or "default"
        bypass_available = bool(options.is_bypass_permissions_mode_available)

    # Per-session abort controller. SIGINT trips this so the running
    # tool (Bash supervisor, Agent subagent) unwinds immediately rather
    # than waiting for the next safe interpreter bytecode boundary.
    # Without this wiring, Ctrl-C only fires ``KeyboardInterrupt`` at
    # the next safe boundary — which can be several minutes for a
    # subprocess.wait() or an in-flight subagent.
    abort_controller = AbortController()
    # C1: load persisted permission rules (settings files) at startup so
    # "always allow" rules saved in interactive sessions auto-allow here
    # too. Setup warnings intentionally unsurfaced until phase C6.
    from src.permissions.settings_paths import default_setup_paths
    from src.permissions.setup import setup_permissions

    _perm_setup = setup_permissions(
        cwd=str(workspace_root),
        mode=effective_mode,  # type: ignore[arg-type]
        is_bypass_available=bypass_available,
        **default_setup_paths(str(workspace_root)),
    )
    tool_context = ToolContext(
        workspace_root=workspace_root,
        permission_context=_perm_setup.context,
        abort_controller=abort_controller,
    )
    tool_context.options.is_non_interactive_session = True
    # PLUGINS-1 — initBuiltinPlugins (main.tsx:1926 analog), idempotent.
    try:
        from src.plugins.init_builtin import init_builtin_plugins

        init_builtin_plugins()
    except Exception:  # noqa: BLE001
        pass

    # OS-1 G1 — settings-configured output style applies headless too.
    from src.outputStyles import output_style_from_settings

    _settings_style = output_style_from_settings(cwd=str(workspace_root))
    if _settings_style:
        tool_context.output_style_name = _settings_style
    # ch01 round-4 WI-1 — load settings hooks into the executor-visible
    # snapshot + global registry. Safe here: run_headless is sync and its
    # asyncio.run happens later. Never raises.
    from src.hooks.config_manager import bootstrap_hook_config_manager

    tool_context.hook_config_manager = bootstrap_hook_config_manager(
        cwd=str(workspace_root),
    )
    # workspace_trusted feeds the hook trust gate (trust_gate WI-0.2); it
    # defaulted False with no production setter, so even configured hooks
    # were silently skipped. Same source of truth as the CLI startup gate.
    try:
        from src.services.startup_gates import check_trust_accepted

        tool_context.workspace_trusted = check_trust_accepted(workspace_root)
    except Exception:  # noqa: BLE001 — unknown trust stays untrusted
        import logging

        logging.getLogger(__name__).debug(
            "headless trust check failed", exc_info=True,
        )
    try:
        from src.services.compact.autocompact import AutoCompactTracking

        _run_compact_tracking = AutoCompactTracking()
    except Exception:  # noqa: BLE001 — pipeline wiring must not block startup
        _run_compact_tracking = None

    def _build_turn_pipeline_config():
        """ch05 round-4 GAP A — per-turn config (fresh fingerprints),
        run-scoped tracking (the 3-consecutive-failures breaker)."""
        if _run_compact_tracking is None:
            return None
        try:
            from src.services.compact.pipeline import (
                build_production_pipeline_config,
            )

            return build_production_pipeline_config(
                provider, tool_context, _run_compact_tracking,
            )
        except Exception:  # noqa: BLE001 — pipeline is best-effort
            return None

    if options.skip_permissions or effective_mode == "bypassPermissions":
        tool_context.allow_docs = True
        tool_context.permission_handler = None
    else:
        # Never block a pipe on stdin. Auto-deny any permission request.
        tool_context.permission_handler = _auto_deny_permission_handler(stderr)
    # AskUserQuestion has no terminal to read from in headless mode.
    tool_context.ask_user = _noop_ask_user

    # Build the input iterator.
    goal_mgr = None  # /goal loop state (single-prompt mode only)
    if options.input_format == "stream-json":
        inputs: Iterable[UserInputMessage] = StreamJsonReader(stdin)
    else:
        prompt_text = options.prompt
        if prompt_text is None or prompt_text == "-":
            prompt_text = stdin.read()
        prompt_text = (prompt_text or "").strip()
        if not prompt_text:
            cli_error("error: no prompt provided (pass an argument or pipe stdin)", 2)

        # /goal in -p mode (CC docs/en/goal §Run non-interactively): setting
        # a goal runs the evaluate-continue loop to completion in this one
        # invocation. Bare "/goal" and "/goal clear" print and exit —
        # there's no persisted goal in a fresh -p process to inspect, but
        # the forms must not be misread as conversation prompts.
        if prompt_text.startswith("/goal"):
            goal_arg = prompt_text[len("/goal"):].strip()
            from src.goals import GoalManager, build_judge_callable
            from src.goals.command import run_goal_command
            from src.settings.settings import get_settings, load_settings

            def _goal_set_gate() -> str | None:
                # CC §Requirements: trusted workspace + hooks enabled, with
                # the reason stated. Same gates as the agent-server.
                if not getattr(tool_context, "workspace_trusted", False):
                    return (
                        "/goal requires a trusted workspace (the evaluator "
                        "is part of the hooks system). Run clawcodex "
                        "interactively once to accept the trust dialog."
                    )
                try:
                    if not load_settings(cwd=str(workspace_root)).hooks.enabled:
                        return (
                            "/goal is unavailable because hooks are disabled "
                            "(settings hooks.enabled=false)."
                        )
                except Exception:  # noqa: BLE001 — unreadable settings fail open
                    pass
                return None

            try:
                goal_max_turns = int(
                    getattr(get_settings(), "goal_max_turns", 0) or 0
                )
            except Exception:  # noqa: BLE001
                goal_max_turns = 0
            _mgr = GoalManager(
                session.session_id,
                **({"default_max_turns": goal_max_turns} if goal_max_turns > 0 else {}),
                judge=build_judge_callable(provider),
            )
            goal_result = run_goal_command(
                _mgr, goal_arg, set_gate=_goal_set_gate,
            )
            if goal_result.kickoff:
                if goal_result.notice:
                    print(goal_result.notice, file=stderr)
                goal_mgr = _mgr
                prompt_text = goal_result.kickoff
            else:
                # status / clear / pause / resume / gate-refusal — print and
                # exit without running a conversation turn.
                out = goal_result.text or ""
                print(out, file=stdout if goal_result.ok else stderr)
                return 0 if goal_result.ok else 1

        inputs = [UserInputMessage(text=prompt_text, raw={"prompt": prompt_text})]

    # /goal continuations are interleaved after the input that spawned them:
    # the generator drains ``goal_continuations`` before pulling the next
    # stdin item, so the loop below stays a single linear ``for``.
    from collections import deque

    goal_continuations: deque[str] = deque()

    def _with_goal_continuations(
        source: Iterable[UserInputMessage],
    ) -> Iterable[UserInputMessage]:
        for item in source:
            yield item
            while goal_continuations:
                yield UserInputMessage(
                    text=goal_continuations.popleft(),
                    raw={"goal_continuation": True},
                )

    if goal_mgr is not None:
        inputs = _with_goal_continuations(inputs)

    writer: StreamJsonWriter | None = None
    if options.output_format == "stream-json":
        writer = StreamJsonWriter(stdout)
        # Coordinator-filtered view (identity when the mode is off) — the
        # init event must list what the main loop actually gets. Mirrors the
        # headless filter application at main.tsx:1871-1879.
        from src.coordinator.mode import coordinator_main_loop_registry

        tools = [
            tool.name
            for tool in coordinator_main_loop_registry(tool_registry).list_tools()
        ]
        writer.write(
            SystemEvent(
                subtype="init",
                session_id=session.session_id,
                model=getattr(provider, "model", None),
                provider=provider_name,
                cwd=str(workspace_root),
                tools=tools,
                permission_mode=effective_mode,
            )
        )

    aggregate_text: list[str] = []
    aggregate_tool_events: list[dict] = []
    num_turns_total = 0
    usage_total: dict[str, int] = {}
    exit_code = 0
    start = time.monotonic()

    # Two-mode SIGINT handler:
    # * Idle (waiting on stdin for the next stream-json input) → raise
    #   ``KeyboardInterrupt`` immediately so the blocking read returns.
    # * In-flight ``run_agent_loop`` → first strike trips the controller
    #   (cooperative unwind), second strike force-quits via
    #   ``KeyboardInterrupt``. Both map to exit 130.
    # See ``_install_sigint_handler`` for the full handler logic; the
    # for-loop's outer ``except (AbortError, KeyboardInterrupt)`` is the
    # single chokepoint that catches whatever the handler raises.
    # ``restore_sigint`` runs in the ``finally`` so we don't leak global
    # signal state to embedders.
    in_agent_loop = _InAgentLoopFlag()
    restore_sigint = _install_sigint_handler(
        abort_controller, in_agent_loop, stderr
    )
    try:
        # Cancellation is caught at the for-loop level (not per-iteration)
        # so that a SIGINT landing on ANY cancellation point unwinds to one
        # place that emits the cancelled ResultEvent: the iterator step
        # (``StreamJsonReader``'s blocking stdin read in idle mode), the
        # agent loop itself, or the post-success accounting between them.
        # The inner per-iteration ``except Exception`` keeps per-turn
        # tool/provider error handling local — it must NOT catch
        # ``AbortError``/``KeyboardInterrupt`` (Python catches them via
        # ``Exception`` only when they inherit from it; ``AbortError`` does
        # but ``KeyboardInterrupt`` does not, so we exclude AbortError
        # explicitly).
        try:
            for user_msg in inputs:
                session.conversation.add_user_message(user_msg.text)

                on_event = _build_event_bridge(writer, aggregate_tool_events)
                on_text_chunk = None
                if writer is not None and options.include_partial_messages:
                    def _emit_partial(chunk: str) -> None:
                        writer.write(PartialTextEvent(text=chunk))

                    on_text_chunk = _emit_partial

                try:
                    in_agent_loop.value = True
                    try:
                        # Ch5/F.2 cutover: route headless through the
                        # canonical query() loop via the F.1 adapter.
                        # Headless is single-shot per prompt and starts
                        # its own event loop, so ``asyncio.run`` is the
                        # right pattern. Pre-build the effective system
                        # prompt (CLAWCODEX.md + git status + style) so the
                        # cold-start context reaches query() unchanged
                        # — the legacy run_agent_loop did this inside
                        # the loop; the adapter doesn't.
                        import asyncio as _asyncio
                        from src.outputStyles import resolve_output_style
                        _style_prompt = resolve_output_style(
                            getattr(tool_context, "output_style_name", None),
                            getattr(tool_context, "output_style_dir", None),
                        ).prompt
                        effective_system_prompt = (
                            build_effective_system_prompt(
                                _style_prompt, tool_context, provider=provider,
                            )
                        )

                        def _persist(msg: Any) -> None:
                            # BLOCKING #2 fix: persist FULL message
                            # (including tool_use/tool_result blocks)
                            # so the next turn can pair tool_use IDs to
                            # results. Plain add_assistant_message
                            # loses the structure.
                            # Critic S3: log + re-raise on failure
                            # rather than swallow; a persist error
                            # means the conversation is corrupted and
                            # the next API call will reject it. Better
                            # to surface now than to debug a 400 later.
                            try:
                                # Preserve the turn's token usage (assistant
                                # messages carry it) so the persisted session
                                # — and the Harbor trajectory's per-step
                                # Metrics — can attribute tokens per turn.
                                session.conversation.add_message(
                                    msg.role,
                                    msg.content,
                                    usage=getattr(msg, "usage", None),
                                )
                            except Exception:
                                import logging
                                logging.getLogger(__name__).exception(
                                    "Failed to persist message into "
                                    "conversation: role=%s",
                                    getattr(msg, "role", "?"),
                                )
                                raise

                        # Coordinator mode: main loop on the filtered view;
                        # subagents keep the Agent tool's captured full
                        # registry (see coordinator_main_loop_registry).
                        from src.coordinator.mode import (
                            coordinator_main_loop_registry,
                        )

                        compat_result = _asyncio.run(run_query_as_agent_loop(
                            initial_messages=list(session.conversation.messages),
                            provider=provider,
                            tool_registry=coordinator_main_loop_registry(tool_registry),
                            tool_context=tool_context,
                            system_prompt=effective_system_prompt,
                            max_turns=options.max_turns,
                            fallback_model=options.fallback_model,
                            thinking_effort=options.effort,
                            # ch05 round-4 GAP A — the production pipeline:
                            # config rebuilt per turn (fresh read-file
                            # fingerprints), tracking RUN-scoped (breaker
                            # persists across stream-json turns); TS 'sdk'
                            # label for the print path.
                            pipeline_config=_build_turn_pipeline_config(),
                            query_source="sdk",
                            on_event=on_event,
                            on_text_chunk=on_text_chunk,
                            on_message=_persist,
                            # Plan-mode attachments persist into the run's
                            # conversation (same seam as _persist) so the
                            # cadence scan + later turns see them; unlike
                            # on_message there is no SDK envelope to emit
                            # here, but keep the paths distinct anyway.
                            on_attachment=lambda m: session.conversation.add_message(
                                m.role, m.content
                            ),
                            # Critic C2: pass the OWNING controller so
                            # the provider's chat_stream_response listens
                            # on the same signal the SIGINT handler trips.
                            # Passing only ``cancel_signal=signal`` would
                            # force the adapter to mint a fresh controller
                            # and break the mid-stream tear-down path.
                            abort_controller=abort_controller,
                        ))
                        # Re-wrap into legacy AgentLoopResult shape so
                        # downstream usage/num_turns/response_text code
                        # stays untouched. ``usage if num_turns > 0
                        # else None`` preserves the dict|None contract.
                        result = AgentLoopResult(
                            response_text=compat_result.response_text,
                            usage=(
                                compat_result.usage
                                if compat_result.num_turns > 0
                                else None
                            ),
                            num_turns=compat_result.num_turns,
                        )
                    finally:
                        # Flip BEFORE the outer except block can run so a
                        # SIGINT landing between ``run_agent_loop`` returning
                        # and the next iterator step is correctly classified
                        # as idle. (``AbortError`` is a subclass of
                        # ``Exception`` and would otherwise be re-raised
                        # through this finally too — so we set the flag
                        # back to False regardless of how we leave.)
                        in_agent_loop.value = False
                except AbortError:
                    # Re-raise to the outer ``except`` so the cancelled
                    # ResultEvent is emitted in exactly one place.
                    raise
                except Exception as exc:
                    exit_code = 1
                    if writer is not None:
                        writer.write(
                            ResultEvent(
                                subtype="error",
                                session_id=session.session_id,
                                num_turns=num_turns_total,
                                result=str(exc),
                                duration_ms=int((time.monotonic() - start) * 1000),
                                is_error=True,
                                error=str(exc),
                            )
                        )
                    else:
                        print(f"error: {exc}", file=stderr)
                    break

                num_turns_total += result.num_turns
                if result.usage:
                    for key, value in result.usage.items():
                        usage_total[key] = usage_total.get(key, 0) + int(value)

                if writer is not None:
                    writer.write(AssistantEvent(text=result.response_text))
                aggregate_text.append(result.response_text)

                # ── /goal post-turn evaluation (single-prompt mode) ──────
                # Single-threaded here, so the composed evaluate_after_turn
                # is safe (no locking split needed). Progress goes to stderr
                # so stdout stays the final answer.
                if goal_mgr is not None and goal_mgr.is_active():
                    from src.goals import collect_turn_evidence

                    evidence = collect_turn_evidence(
                        list(session.conversation.messages)
                    ) or (result.response_text or "")
                    tokens_now = int(
                        usage_total.get("input_tokens", 0)
                        + usage_total.get("output_tokens", 0)
                    )
                    decision = goal_mgr.evaluate_after_turn(
                        evidence, tokens_now=tokens_now,
                    )
                    if decision.get("message"):
                        print(f"[goal] {decision['message']}", file=stderr)
                    if decision.get("should_continue") and decision.get(
                        "continuation_prompt"
                    ):
                        goal_continuations.append(
                            decision["continuation_prompt"]
                        )
        except (AbortError, KeyboardInterrupt):
            # Cancellation from ANY point in the loop body lands here:
            # * ``AbortError`` from a cooperative unwind inside
            #   ``run_agent_loop`` (first SIGINT, in-flight mode).
            # * ``KeyboardInterrupt`` from the SIGINT handler's idle
            #   branch (raised mid-``inputs.__iter__()`` while blocked on
            #   stdin), or from the in-flight second-strike force-quit.
            # All map to exit 130 for shell parity. ``error`` is left
            # unset — ``subtype: "cancelled"`` already carries the
            # signal, and pairing ``is_error=False`` with a populated
            # ``error`` field would confuse consumers.
            exit_code = 130
            if writer is not None:
                writer.write(
                    ResultEvent(
                        subtype="cancelled",
                        session_id=session.session_id,
                        num_turns=num_turns_total,
                        result="",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        is_error=False,
                    )
                )
    finally:
        restore_sigint()

    # Persist the full session (conversation + cost) to disk at the end of a
    # headless run. Interactive/TUI runs already persist; the print path did
    # not, so `-p` sessions could not be `--resume`d and left no structured
    # transcript. Best-effort: a save failure must never change the exit
    # code or output. Enables downstream trajectory reconstruction (the
    # Harbor adapter builds an ATIF trajectory.json from this).
    try:
        session.save()
    except Exception:  # noqa: BLE001 — persistence is best-effort
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "headless session save failed", exc_info=True
        )

    # A -p goal run that ends without achieving the condition (budget pause,
    # evaluator-timeout park, interrupt) exits non-zero so scripts can tell
    # "loop finished" from "condition met". Achieved goals keep exit 0.
    if goal_mgr is not None and exit_code == 0:
        _goal_state = goal_mgr.state
        if _goal_state is not None and _goal_state.status != "done":
            print(f"[goal] not achieved: {goal_mgr.status_line()}", file=stderr)
            exit_code = 1

    duration_ms = int((time.monotonic() - start) * 1000)
    final_text = "\n\n".join(t for t in aggregate_text if t).strip()

    if options.output_format == "text":
        if final_text:
            stdout.write(final_text + "\n")
            stdout.flush()
    elif options.output_format == "json":
        if exit_code == 0:
            json_subtype = "success"
        elif exit_code == 130:
            json_subtype = "cancelled"
        else:
            json_subtype = "error"
        payload = {
            "type": "result",
            "subtype": json_subtype,
            "session_id": session.session_id,
            "provider": provider_name,
            "model": getattr(provider, "model", None),
            "num_turns": num_turns_total,
            "result": final_text,
            "duration_ms": duration_ms,
            "usage": usage_total or None,
            "tool_events": aggregate_tool_events,
            "is_error": exit_code not in (0, 130),
        }
        stdout.write(ndjson_safe_dumps(payload) + "\n")
        stdout.flush()
    elif options.output_format == "stream-json" and writer is not None and exit_code == 0:
        writer.write(
            ResultEvent(
                subtype="success",
                session_id=session.session_id,
                num_turns=num_turns_total,
                result=final_text,
                duration_ms=duration_ms,
                usage=usage_total or None,
            )
        )

    return exit_code


# ---------------------------------------------------------------------------
# Helpers


class _InAgentLoopFlag:
    """Mutable shared flag indicating whether ``run_agent_loop`` is in flight.

    Read by the SIGINT handler to decide between cooperative abort
    (in-flight: trip the controller, let the loop unwind at the next
    safe boundary) and immediate raise (idle, e.g. blocked on
    ``StreamJsonReader``'s stdin read: the only way to make the read
    return is to actually raise ``KeyboardInterrupt`` on the same
    thread — Python 3 auto-retries EINTR'd reads when the handler
    didn't raise, per PEP 475).
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = False


def _install_sigint_handler(
    controller: AbortController,
    in_agent_loop: _InAgentLoopFlag,
    stderr: IO[str],
) -> Callable[[], None]:
    """Install a context-aware SIGINT handler.

    - **Idle** (``in_agent_loop.value`` is False, e.g. blocked on
      stdin reading the next stream-json input): raise
      ``KeyboardInterrupt`` immediately. Python 3 PEP 475 retries
      EINTR'd ``read()`` calls when the signal handler did NOT raise,
      so a cooperative abort here would *hang the stdin read* until
      the user hit Ctrl-C a second time — a UX regression vs. the
      pre-fix behaviour where the first Ctrl-C raised at the next
      bytecode boundary and exited the program. Raising on the first
      strike restores parity with that pre-fix path.

    - **Cooperative** (in-flight ``run_agent_loop``, first strike):
      trip ``controller``. Every abort-aware site — the agent loop's
      ``_check_cancel`` boundaries, the Bash supervisor's poll loop,
      the subagent query loop, the streaming executor's per-tool
      controller, hook gates — sees the signal and unwinds gracefully
      with a partial result that's appended to the conversation. A
      message is printed to stderr so the user knows the request was
      received but unwind may take a moment.

    - **Cooperative** (in-flight, second strike): re-install the
      platform default handler (defense-in-depth against a possible
      third strike landing during unwind) and raise
      ``KeyboardInterrupt`` directly. This is the force-quit escape
      hatch for the rare case where a tool doesn't honour the abort.

    Returns a callable that restores whatever handler was installed
    before us, so embedders that drive ``run_headless`` from inside a
    larger program don't have their global signal state mutated.

    ``signal.signal`` is only callable from the main thread; if we are
    not the main thread (e.g. an SDK harness that runs headless in a
    worker thread), the install is skipped and the returned restore is
    a no-op. Cancellation in that case falls back to the agent loop's
    natural turn-boundary checks via ``KeyboardInterrupt`` propagation
    from whatever signal facility the embedder is using.
    """

    previous = _signal.getsignal(_signal.SIGINT)

    def _handler(signum, frame):
        if not in_agent_loop.value:
            # Idle on input — raise so the blocking stdin read returns.
            # No need to swap to ``default_int_handler``: there's no
            # cooperative-unwind escalation state to escalate from, and
            # ``restore_sigint()`` in the ``finally`` block will revert
            # the user's pre-existing handler shortly after the raise
            # unwinds out of ``run_headless``. A second SIGINT before
            # that finally runs would just re-enter this handler and
            # raise again — fine.
            raise KeyboardInterrupt
        if controller.signal.aborted:
            # Second strike during cooperative unwind: re-arm the
            # platform default handler (so any third strike terminates
            # the process the usual way) and raise the force-quit.
            _signal.signal(_signal.SIGINT, _signal.default_int_handler)
            raise KeyboardInterrupt
        controller.abort("user_interrupt")
        try:
            # Plain ASCII for portability — some legacy Windows code
            # pages can't encode U+2026 and would silently drop the
            # message via the outer except.
            stderr.write("\nCancelling... (Ctrl-C again to force quit)\n")
            stderr.flush()
        except Exception:
            # A broken stderr (closed pipe etc.) must not stop the
            # cancellation from propagating — the controller is already
            # tripped, the agent loop will unwind regardless.
            pass

    try:
        _signal.signal(_signal.SIGINT, _handler)
    except (ValueError, OSError):
        # Not in main thread (ValueError) or SIGINT not supported on
        # this platform (OSError on some Windows configurations).
        # Fall back to the agent loop's natural turn-boundary cancel
        # checks via ``KeyboardInterrupt`` — the pre-fix behaviour.
        return lambda: None

    def _restore() -> None:
        try:
            _signal.signal(_signal.SIGINT, previous)
        except (ValueError, OSError):
            pass

    return _restore


def _filter_registry(registry, *, keep) -> None:
    """In-place best-effort filter of a ToolRegistry.

    Drops every tool for which ``keep(name)`` is False so that
    ``--allowedTools`` / ``--disallowedTools`` remove the tool from the pool
    the model sees (schemas are emitted from ``registry.list_tools()``), not
    just block it at execution time.
    """

    try:
        entries = list(registry.list_tools())
    except Exception:
        return
    for tool in entries:
        name = getattr(tool, "name", "")
        if not keep(name):
            try:
                registry.remove_tool(name)
            except Exception:
                # Best-effort: a registry that cannot drop the tool leaves it
                # in the pool rather than aborting the whole filter.
                continue


def _auto_deny_permission_handler(stderr: IO[str]):
    from src.permissions.types import PermissionAskReply, PermissionAskRequest

    def handler(request: PermissionAskRequest) -> PermissionAskReply:
        stderr.write(
            f"[headless] denying permission for {request.tool_name}: "
            f"{request.message}"
            " (pass --dangerously-skip-permissions to bypass)\n"
        )
        try:
            stderr.flush()
        except Exception:
            pass
        return PermissionAskReply(behavior="deny")

    return handler


_NON_INTERACTIVE_ANSWER = (
    "No interactive user is available (running headless/non-interactive). "
    "Proceed autonomously with your best judgment and reasonable default "
    "assumptions; do not ask again."
)


def _noop_ask_user(questions):  # type: ignore[override]
    # Non-interactive mode: there is no user to answer. Returning bare
    # empty strings left the model with no signal about WHY the answer was
    # empty — observed live (terminal-bench raman-fitting) to make it flail,
    # re-asking / retrying instead of committing to an approach. Return an
    # explicit "proceed autonomously" answer so the model moves on
    # decisively. (The interactive TUI still shows the real dialog; only the
    # headless surface — which cannot collect input — substitutes this.)
    answers: dict = {}
    for q in questions or []:
        if isinstance(q, dict) and isinstance(q.get("question"), str):
            answers[q["question"]] = _NON_INTERACTIVE_ANSWER
    return answers


def _build_event_bridge(writer: StreamJsonWriter | None, sink: list[dict]):
    def on_event(event: ToolEvent) -> None:
        if event.kind == "tool_use":
            record = {
                "type": "tool_use",
                "tool_use_id": event.tool_use_id,
                "name": event.tool_name,
                "input": event.tool_input or {},
            }
            sink.append(record)
            if writer is not None:
                writer.write(
                    ToolUseEvent(
                        tool_use_id=event.tool_use_id,
                        name=event.tool_name,
                        input=dict(event.tool_input or {}),
                    )
                )
        elif event.kind in ("tool_result", "tool_error"):
            record = {
                "type": "tool_result",
                "tool_use_id": event.tool_use_id,
                "name": event.tool_name,
                "output": _jsonable(event.tool_output),
                "is_error": bool(event.is_error),
            }
            if event.error:
                record["error"] = event.error
            sink.append(record)
            if writer is not None:
                writer.write(
                    ToolResultEvent(
                        tool_use_id=event.tool_use_id,
                        name=event.tool_name,
                        output=_jsonable(event.tool_output),
                        is_error=bool(event.is_error),
                    )
                )

    return on_event


def _jsonable(value):
    """Coerce arbitrary tool output into a JSON-safe shape."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    try:
        return str(value)
    except Exception:
        return repr(value)
