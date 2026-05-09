"""Permission-decision aggregation across multiple hook results.

Phase-4 / WI-4.1. Per the I2 contract split (plan §1):
  * Phase 3 owns "collect" — merge snapshot + session-registry hooks.
  * Phase 4 owns "aggregate" — fold N ``HookResult``s into a single
    ``AggregatedHookResult`` with deny > ask > allow precedence.

Pre-Phase-4, ``hook_executor._run_hooks_for_event`` yielded each hook's
decision independently — a per-hook stream. With multiple hooks for the
same ``(event, tool_name)`` (e.g., user-tier ``allow`` + project-tier
``deny``), a downstream consumer that read only the first yield would get
the wrong answer. The chapter is explicit (``ch12-extensibility.md``
§"Permission Decision"):

    > If any hook denies, the tool call is denied. If any hook asks, the
    > tool call goes through the ask path. Otherwise, allow wins.

Aggregation rules:
  * **Deny dominates.** Any ``deny`` short-circuits the result to deny.
    Reason carries the first denying hook's reason (most authoritative
    for telemetry); subsequent denies are still recorded in the
    ``contributing`` list for transparency.
  * **Ask trumps allow.** Among non-deny results, any ``ask`` produces
    ask. Reason carries the first asking hook's reason.
  * **Allow only if explicitly allowed.** A single ``allow`` produces
    allow; multiple allows still produce allow with merged reasons.
  * **No opinion is no opinion.** Empty input or all-None decisions
    produce ``permission_behavior=None`` — the caller falls back to the
    rule-based permission system.

Field-level aggregation for non-decision payloads:
  * ``blocking_error`` — first non-None wins (early errors are most
    actionable).
  * ``updated_input`` — last non-None wins (later hooks may legitimately
    refine an earlier hook's input modification; the chapter is silent
    on ordering, so last-write-wins is the practical default).
  * ``additional_contexts`` — concatenated across all hooks.
  * ``prevent_continuation`` — any True wins; first ``stop_reason`` carries.
  * ``updated_mcp_tool_output`` — last non-None wins (same reasoning as
    ``updated_input``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .hook_types import HookResult


@dataclass
class AggregatedHookResult:
    """Single decision payload aggregated from a list of ``HookResult``s.

    Field shapes mirror ``HookResult`` so the executor can drop in the
    aggregated result without restructuring downstream consumers.
    ``contributing_reasons`` is the new aggregation-only field — a flat
    list of ``(behavior, reason, source)`` tuples for telemetry/UI to
    show the user *why* a deny/ask landed.
    """
    permission_behavior: Literal["allow", "ask", "deny"] | None = None
    hook_permission_decision_reason: str | None = None
    blocking_error: str | None = None
    updated_input: dict | None = None
    additional_contexts: list[str] = field(default_factory=list)
    prevent_continuation: bool = False
    stop_reason: str | None = None
    updated_mcp_tool_output: object | None = None
    # Per-hook attribution for telemetry: list of
    # ``(behavior, reason, command)`` triples in firing order. Allows the
    # UI to render "denied by hook A (reason X), also denied by hook B
    # (reason Y)" without re-deriving from individual results.
    contributing_reasons: list[tuple[str | None, str | None, str | None]] = field(
        default_factory=list
    )


def aggregate_hook_results(results: list[HookResult]) -> AggregatedHookResult:
    """Fold a list of ``HookResult`` into a single ``AggregatedHookResult``.

    Order: empty list → no-opinion result; otherwise apply the deny > ask
    > allow precedence and merge the non-decision payloads per the
    field-level rules in this module's docstring.

    Pure function — no I/O, no side effects.
    """
    agg = AggregatedHookResult()
    if not results:
        return agg

    # First pass: classify decisions, build contributing_reasons, and pick
    # the dominant behavior. We iterate twice: once to find the dominant
    # decision (so we attribute the *first* deny/ask reason rather than
    # the last one), once to merge non-decision fields.
    saw_deny = False
    saw_ask = False
    saw_allow = False
    first_deny_reason: str | None = None
    first_ask_reason: str | None = None
    first_allow_reason: str | None = None

    for r in results:
        agg.contributing_reasons.append((
            r.permission_behavior,
            r.hook_permission_decision_reason,
            r.command,
        ))
        if r.permission_behavior == "deny":
            if not saw_deny:
                first_deny_reason = r.hook_permission_decision_reason
            saw_deny = True
        elif r.permission_behavior == "ask":
            if not saw_ask:
                first_ask_reason = r.hook_permission_decision_reason
            saw_ask = True
        elif r.permission_behavior == "allow":
            if not saw_allow:
                first_allow_reason = r.hook_permission_decision_reason
            saw_allow = True

    if saw_deny:
        agg.permission_behavior = "deny"
        agg.hook_permission_decision_reason = first_deny_reason
    elif saw_ask:
        agg.permission_behavior = "ask"
        agg.hook_permission_decision_reason = first_ask_reason
    elif saw_allow:
        agg.permission_behavior = "allow"
        agg.hook_permission_decision_reason = first_allow_reason
    # else: leave permission_behavior=None (no opinion).

    # Second pass: non-decision fields.
    for r in results:
        if agg.blocking_error is None and r.blocking_error:
            agg.blocking_error = r.blocking_error
        if r.updated_input is not None:
            agg.updated_input = r.updated_input  # last-wins
        if r.additional_contexts:
            agg.additional_contexts.extend(r.additional_contexts)
        if r.prevent_continuation:
            if not agg.prevent_continuation:
                agg.stop_reason = r.stop_reason
            agg.prevent_continuation = True
        if r.updated_mcp_tool_output is not None:
            agg.updated_mcp_tool_output = r.updated_mcp_tool_output  # last-wins

    return agg
