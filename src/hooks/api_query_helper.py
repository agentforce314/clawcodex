"""API-query hook helper.

Phase-8 / WI-8.3. Closes part of gap analysis #19.

Wraps API queries (provider chat calls) with hook events so subscribers
can:

  * Inject context refresh before each query (e.g., a hook that
    reads the latest git status and prepends it).
  * Observe each query's outcome for telemetry (latency, token use,
    decisions).
  * Modify the query input via the existing hook ``updated_input``
    contract.

Mirrors TS' apiQueryHookHelper pattern. Two hook events fire per
wrapped query:

  * ``UserPromptSubmit`` — fires before the query. Aggregated decision
    from this event can carry ``additional_contexts`` that get
    concatenated to the user's prompt before the API call.
  * ``PostSampling`` — fires after the query. Subscribers see the
    response + usage metadata; permission_behavior here can ``deny``
    (in which case the response is dropped and the agent loop sees a
    blocking_error) or surface ``additionalContexts`` for the next
    turn.

**Why a helper rather than inlining at every call site?** Three
reasons:

  1. Consistency: every API query goes through the same pre/post
     hook flow, so subscribers don't need to track call-site-specific
     contracts.
  2. Testability: the helper is one module to test with a mocked
     hook executor and a stub query callable.
  3. Future SDK exposure: the SDK can wrap session-level queries by
     subscribing to these events without patching the agent loop.

**Composition with the executor.** This helper does NOT re-implement
hook dispatch. It calls ``_run_hooks_for_event`` for the two events,
collects the aggregated decisions, and applies them to the query.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Type alias for the wrapped query callable. Takes the prompt string +
# any provider-specific kwargs; returns the response (provider-specific
# shape; we don't constrain it). The helper is a transparent wrapper —
# it passes the response through unchanged unless ``post_sampling``
# yielded a deny decision.
ApiQueryFn = Callable[..., Awaitable[Any]]


class ApiQueryHookHelper:
    """Wrap an API query with ``UserPromptSubmit`` (pre) and
    ``PostSampling`` (post) hook events.

    Usage:
        helper = ApiQueryHookHelper(tool_use_context=ctx)
        response = await helper.run(
            query_fn=provider.chat_async,
            prompt="...",
            messages=[...],
            model="...",
        )

    The helper drives ``_run_hooks_for_event`` for the two events,
    applying any ``additional_contexts`` from the pre-event to the
    prompt and any ``deny`` decision from either event to abort the
    query.
    """

    def __init__(self, *, tool_use_context: Any) -> None:
        self._ctx = tool_use_context

    async def run(
        self,
        *,
        query_fn: ApiQueryFn,
        prompt: str,
        **query_kwargs: Any,
    ) -> Any:
        """Drive ``query_fn(prompt=prompt, **query_kwargs)`` with hook
        events bracketing.

        Returns whatever the query function returns, or raises
        ``ApiQueryHookDenied`` if a hook denied the query.
        """
        from .hook_executor import _run_hooks_for_event

        # ---------- Pre-query: UserPromptSubmit ----------
        pre_yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "UserPromptSubmit",
            None,  # no tool_name for non-tool events
            {"hook_event": "UserPromptSubmit", "prompt": prompt},
            self._ctx,
        ):
            pre_yields.append(item)

        pre_agg = _extract_aggregated(pre_yields)
        if pre_agg is not None and pre_agg.permission_behavior == "deny":
            raise ApiQueryHookDenied(
                f"UserPromptSubmit hook denied query: "
                f"{pre_agg.hook_permission_decision_reason or '(no reason)'}"
            )

        # Pre-event additional_contexts get concatenated to the prompt
        # so subscribers can inject context refresh before the API call.
        effective_prompt = prompt
        if pre_agg is not None and pre_agg.additional_contexts:
            extra = "\n\n".join(pre_agg.additional_contexts)
            effective_prompt = f"{prompt}\n\n{extra}"

        # ---------- The actual query ----------
        response = await query_fn(prompt=effective_prompt, **query_kwargs)

        # ---------- Post-query: PostSampling ----------
        post_yields: list[dict[str, Any]] = []
        post_stdin = {
            "hook_event": "PostSampling",
            "prompt": effective_prompt,
            "response": _summarize_response(response),
        }
        async for item in _run_hooks_for_event(
            "PostSampling", None, post_stdin, self._ctx,
        ):
            post_yields.append(item)

        post_agg = _extract_aggregated(post_yields)
        if post_agg is not None and post_agg.permission_behavior == "deny":
            raise ApiQueryHookDenied(
                f"PostSampling hook denied response: "
                f"{post_agg.hook_permission_decision_reason or '(no reason)'}"
            )

        return response


class ApiQueryHookDenied(Exception):
    """Raised when an API-query hook returned ``deny``.

    Distinct exception so callers can distinguish hook-driven query
    rejection from network / API errors.
    """


def _extract_aggregated(yields: list[dict[str, Any]]) -> Any | None:
    """Pull the ``aggregated_hook_decision`` payload out of an executor
    yield list. Returns the AggregatedHookResult or None if no hooks
    fired.
    """
    for y in yields:
        agg = y.get("aggregated_hook_decision")
        if agg is not None:
            return agg
    return None


def _summarize_response(response: Any) -> str:
    """Best-effort string summary of a provider response for the
    PostSampling hook's stdin. We don't pass the full response (which
    might be huge) to keep hook stdin small; just the textual content.
    """
    if hasattr(response, "content") and response.content:
        c = response.content
        if isinstance(c, str):
            return c[:2000]
        return str(c)[:2000]
    return str(response)[:2000]
