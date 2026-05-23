"""Debug logging utilities for the bridge subsystem.

Ports ``typescript/src/bridge/debugUtils.ts``.

Helpers for redacting secrets, truncating debug bodies, extracting error
messages from HTTP errors, and emitting analytics events for skipped bridge
init. The TS file uses axios-specific error types; Python uses ``httpx`` or
plain ``Exception`` — the API is generalized as ``describe_http_error``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEBUG_MSG_LIMIT = 2000

_SECRET_FIELD_NAMES = (
    'session_ingress_token',
    'environment_secret',
    'access_token',
    'secret',
    'token',
)

_SECRET_PATTERN = re.compile(
    r'"(' + '|'.join(_SECRET_FIELD_NAMES) + r')"\s*:\s*"([^"]*)"',
)

_REDACT_MIN_LENGTH = 16


def redact_secrets(s: str) -> str:
    """Replace secret-field values in a JSON-like string with redacted forms.

    Mirrors TS ``redactSecrets`` on ``debugUtils.ts:26-34``. Short tokens
    (<16 chars) are fully redacted; longer ones keep the first 8 + last 4
    chars so debug logs are still triagable while not leaking the secret.
    """

    def _replace(match: re.Match[str]) -> str:
        field = match.group(1)
        value = match.group(2)
        if len(value) < _REDACT_MIN_LENGTH:
            return f'"{field}":"[REDACTED]"'
        return f'"{field}":"{value[:8]}...{value[-4:]}"'

    return _SECRET_PATTERN.sub(_replace, s)


def debug_truncate(s: str) -> str:
    """Truncate a string for debug logging, collapsing newlines.

    Mirrors TS ``debugTruncate`` on ``debugUtils.ts:37-43``.
    """
    flat = s.replace('\n', '\\n')
    if len(flat) <= DEBUG_MSG_LIMIT:
        return flat
    return flat[:DEBUG_MSG_LIMIT] + f'... ({len(flat)} chars)'


def debug_body(data: Any) -> str:
    """Truncate a JSON-serializable value for debug logging, with redaction.

    Mirrors TS ``debugBody`` on ``debugUtils.ts:46-53``. Strings pass through
    as-is; everything else is JSON-encoded first.

    **Behavioral divergence from TS** (intentional): the Python port passes
    ``default=str`` to ``json.dumps`` so non-JSON-serializable values
    (sets, datetimes, custom classes) render as their ``str()`` repr instead
    of throwing. TS ``jsonStringify`` throws on non-serializable values.
    The Python behavior is strictly safer for debug logging — no caller
    should rely on the throw.
    """
    if isinstance(data, str):
        raw = data
    else:
        try:
            raw = json.dumps(data, default=str)
        except (TypeError, ValueError):
            raw = repr(data)
    s = redact_secrets(raw)
    if len(s) <= DEBUG_MSG_LIMIT:
        return s
    return s[:DEBUG_MSG_LIMIT] + f'... ({len(s)} chars)'


def describe_http_error(err: object) -> str:
    """Extract a descriptive error message from an HTTP error.

    Mirrors TS ``describeAxiosError`` on ``debugUtils.ts:60-82``. Renamed
    for Python (httpx, not axios). For responses with a structured error
    body, appends the server's ``message`` / ``error.message`` field.

    Works with httpx ``HTTPStatusError`` (has ``.response.json()``),
    ``RequestError`` (no response), and plain ``Exception``. Best-effort —
    returns just ``str(err)`` when the response isn't structured.
    """
    msg = str(err)
    response = getattr(err, 'response', None)
    if response is None:
        return msg
    try:
        data = response.json() if hasattr(response, 'json') else None
    except Exception:
        return msg
    detail = extract_error_detail(data)
    if detail:
        return f'{msg}: {detail}'
    return msg


def extract_http_status(err: object) -> int | None:
    """Extract the HTTP status code from an error, if present.

    Mirrors TS ``extractHttpStatus`` on ``debugUtils.ts:88-100``. Returns
    ``None`` for non-HTTP errors (network failures, timeouts without a
    response).
    """
    response = getattr(err, 'response', None)
    if response is None:
        return None
    status = getattr(response, 'status_code', None)
    if isinstance(status, int):
        return status
    # httpx uses ``status_code``; some libraries use ``status``.
    status = getattr(response, 'status', None)
    if isinstance(status, int):
        return status
    return None


def extract_error_detail(data: Any) -> str | None:
    """Pull a human-readable message out of an API error response body.

    Mirrors TS ``extractErrorDetail`` on ``debugUtils.ts:106-121``. Checks
    ``data['message']`` first, then ``data['error']['message']``.
    """
    if not isinstance(data, dict):
        return None
    message = data.get('message')
    if isinstance(message, str):
        return message
    error = data.get('error')
    if isinstance(error, dict):
        inner = error.get('message')
        if isinstance(inner, str):
            return inner
    return None


def log_bridge_skip(
    reason: str,
    debug_msg: str | None = None,
    v2: bool | None = None,
) -> None:
    """Log a bridge init skip — debug message + analytics event.

    Mirrors TS ``logBridgeSkip`` on ``debugUtils.ts:128-141``. The TS version
    sends a ``tengu_bridge_repl_skipped`` analytics event via ``logEvent``;
    the Python port logs at INFO level since no GrowthBook analytics client
    exists yet (per refactoring-plan §0.1 Q2 — analytics stubbed).
    """
    if debug_msg:
        logger.debug(debug_msg)
    extra: dict[str, Any] = {'reason': reason}
    if v2 is not None:
        extra['v2'] = v2
    logger.info('bridge_skip reason=%s%s', reason,
                f' v2={v2}' if v2 is not None else '')


__all__ = [
    'DEBUG_MSG_LIMIT',
    'debug_body',
    'debug_truncate',
    'describe_http_error',
    'extract_error_detail',
    'extract_http_status',
    'log_bridge_skip',
    'redact_secrets',
]
