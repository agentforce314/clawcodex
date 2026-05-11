"""OAuth URL redaction: strip security-sensitive query/fragment params for logging.

Phase 4 WI-4.6 (gap-analysis §2.3). Mirrors typescript/src/services/mcp/
auth.ts:redactSensitiveUrlParams + SENSITIVE_OAUTH_PARAMS.

Use this anywhere an OAuth URL might end up in a log line, telemetry
event, error message, or stack trace. Both query AND fragment are
redacted — the OAuth implicit grant flow returns ``access_token`` /
``id_token`` in the URL fragment (RFC 6749 §4.2.2); failing to redact
the fragment silently leaks credentials in logs.

Matching is case-insensitive: a non-spec-compliant server emitting
``State=...`` or ``CODE=...`` is still redacted.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Mirrors TS' SENSITIVE_OAUTH_PARAMS (state, nonce, code_challenge,
# code_verifier, code) plus credential-bearing extras Python may
# encounter across grant types:
#   - implicit: access_token, id_token (in fragment)
#   - auth-code: code, code_verifier (PKCE), code_challenge
#   - refresh: refresh_token
#   - client_credentials / private_key_jwt: client_secret, client_assertion
#   - RFC 7523 JWT bearer: assertion
#   - RFC 8693 token exchange: subject_token, actor_token
#   - resource-owner password (deprecated but real): password
SENSITIVE_OAUTH_PARAMS: tuple[str, ...] = (
    "state",
    "nonce",
    "code",
    "code_challenge",
    "code_verifier",
    "id_token",
    "access_token",
    "refresh_token",
    "client_secret",
    "client_assertion",
    "assertion",
    "subject_token",
    "actor_token",
    "password",
)
_SENSITIVE_LOWER: frozenset[str] = frozenset(p.lower() for p in SENSITIVE_OAUTH_PARAMS)

# URL-safe (no brackets/spaces) so it survives urlencode round-trips
# without percent-escaping into ``%5BREDACTED%5D``.
REDACTION_MARKER = "REDACTED"


def _redact_params(query_string: str) -> str:
    """Redact sensitive params in a query- or fragment-style string."""
    if not query_string:
        return query_string
    params = parse_qsl(query_string, keep_blank_values=True)
    if not params:
        return query_string
    redacted = [
        (k, REDACTION_MARKER if k.lower() in _SENSITIVE_LOWER else v)
        for k, v in params
    ]
    return urlencode(redacted)


def redact_sensitive_params(url: str) -> str:
    """Return ``url`` with sensitive query AND fragment params replaced
    by ``REDACTED``.

    Preserves the rest of the URL structure (host, path, non-sensitive
    params). On parse failure returns the input unchanged — callers are
    likely to log the URL in either case, and we'd rather log a malformed
    URL than crash the logging path.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        new_query = _redact_params(parsed.query)
        new_fragment = (
            _redact_params(parsed.fragment) if parsed.fragment else parsed.fragment
        )
        return urlunparse(parsed._replace(query=new_query, fragment=new_fragment))
    except (ValueError, TypeError):
        return url
