"""HTTP hooks — POST event JSON to a configured URL with SSRF guard.

Phase-7 / WI-7.3 update: switched from sync ``urllib.request`` to
async ``httpx.AsyncClient`` with a custom ``SsrfGuardedTransport``
that validates resolved IPs at DNS-time. Pre-Phase-7 the hook had a
TOCTOU rebinding window between ``validate_hook_url``'s pre-flight
check and the actual TCP connect — closed in Phase 7.

The pre-flight ``validate_hook_url`` check is preserved as an
early-rejection optimization (catches obviously bad URLs without
spawning a transport / making a connection attempt). The load-bearing
security check is now ``SsrfGuardedTransport``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .hook_types import HTTP_HOOK_TIMEOUT_MS, HookConfig, HookResult
from .ssrf_guard import validate_hook_url
from .ssrf_transport import SsrfRebindingError, get_guarded_client

logger = logging.getLogger(__name__)


async def execute_http_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    *,
    timeout_ms: int = HTTP_HOOK_TIMEOUT_MS,
) -> HookResult:
    url = hook.url
    if not url:
        return HookResult(blocking_error="HTTP hook has no URL configured")

    # Pre-flight: cheap rejection for obviously-bad URLs (private IPs,
    # blocked hostnames, malformed). Keeps the transport from being
    # spawned on a guaranteed-fail. Skips DNS resolution (resolve_dns=False)
    # because the load-bearing DNS check now lives in the transport.
    safe, reason = validate_hook_url(url, resolve_dns=False)
    if not safe:
        return HookResult(
            blocking_error=f"SSRF protection blocked URL: {reason}",
            exit_code=-1,
        )

    start_time = time.monotonic()
    effective_timeout = (hook.timeout or timeout_ms) / 1000.0
    payload = json.dumps(stdin_data, default=str)

    try:
        async with get_guarded_client(timeout=effective_timeout) as client:
            response = await client.post(
                url,
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "claude-code-hooks/1.0",
                },
            )
    except SsrfRebindingError as exc:
        # DNS-time guard rejected the connection (rebinding-style
        # attack or misconfigured DNS pointing at a blocked range).
        return HookResult(
            blocking_error=f"SSRF protection blocked URL: {exc}",
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
    except httpx.ConnectTimeout:
        return HookResult(
            blocking_error=(
                f"HTTP hook timed out after "
                f"{int((time.monotonic() - start_time) * 1000)}ms"
            ),
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
    except httpx.TimeoutException:
        return HookResult(
            blocking_error=(
                f"HTTP hook timed out after "
                f"{int((time.monotonic() - start_time) * 1000)}ms"
            ),
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
    except httpx.ConnectError as exc:
        return HookResult(
            blocking_error=f"HTTP hook connection error: {exc}",
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
    except Exception as exc:
        return HookResult(
            blocking_error=f"HTTP hook error: {exc}",
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)
    response_body = response.text
    status_code = response.status_code

    if status_code >= 400:
        return HookResult(
            blocking_error=(
                f"HTTP hook returned status {status_code}: {response_body[:500]}"
            ),
            exit_code=status_code,
            stdout=response_body,
            duration_ms=duration_ms,
        )

    result = HookResult(
        exit_code=0,
        stdout=response_body,
        duration_ms=duration_ms,
    )

    if response_body.strip():
        try:
            parsed = json.loads(response_body)
            if isinstance(parsed, dict):
                decision = parsed.get("decision")
                if decision in ("allow", "deny", "ask"):
                    result.permission_behavior = decision
                    result.hook_permission_decision_reason = parsed.get("reason")
                if parsed.get("updatedInput"):
                    result.updated_input = parsed["updatedInput"]
                if parsed.get("preventContinuation"):
                    result.prevent_continuation = True
                    result.stop_reason = parsed.get("stopReason")
                if parsed.get("additionalContexts"):
                    result.additional_contexts = parsed["additionalContexts"]
        except json.JSONDecodeError:
            pass

    return result
