from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from .hook_types import HookConfig, HookResult, HTTP_HOOK_TIMEOUT_MS
from .ssrf_guard import validate_hook_url

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

    safe, reason = validate_hook_url(url, resolve_dns=True)
    if not safe:
        return HookResult(
            blocking_error=f"SSRF protection blocked URL: {reason}",
            exit_code=-1,
        )

    start_time = time.monotonic()
    effective_timeout = (hook.timeout or timeout_ms) / 1000.0

    try:
        payload = json.dumps(stdin_data, default=str).encode("utf-8")
        req = Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "claude-code-hooks/1.0",
            },
            method="POST",
        )

        response = urlopen(req, timeout=effective_timeout)
        response_body = response.read().decode("utf-8", errors="replace")
        status_code = response.status
        duration_ms = int((time.monotonic() - start_time) * 1000)

        if status_code >= 400:
            return HookResult(
                blocking_error=f"HTTP hook returned status {status_code}: {response_body[:500]}",
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

    except TimeoutError:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"HTTP hook timed out after {duration_ms}ms",
            exit_code=-1,
            duration_ms=duration_ms,
        )
    except URLError as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"HTTP hook connection error: {e.reason}",
            exit_code=-1,
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"HTTP hook error: {e}",
            exit_code=-1,
            duration_ms=duration_ms,
        )
