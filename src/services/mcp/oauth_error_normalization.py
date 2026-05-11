"""OAuth error-body normalization: handle vendor-quirk error responses.

Phase 4 WI-4.7 (gap-analysis §2.3). Mirrors typescript/src/services/mcp/
auth.ts:normalizeOAuthErrorBody.

Two real-world OAuth-server quirks we normalize:

1. **Slack returns HTTP 200 for OAuth errors.** RFC 6749 says OAuth
   errors come back with 4xx status; Slack returns 200 OK with the
   error in the JSON body. We rewrite 2xx + error-body responses to
   400 so caller code can pattern-match on status alone. The predicate
   uses ``body.get("error")`` (truthy check) rather than ``"error" in
   body`` so a successful response with ``error: null`` doesn't get
   spuriously promoted.

2. **Vendor-specific token-error codes.** The RFC names exactly five
   error codes for the token endpoint. Slack and others emit non-RFC
   codes for refresh-token failures (e.g. ``invalid_refresh_token``,
   ``expired_refresh_token``, ``token_expired``). We map these to the
   RFC ``invalid_grant`` so downstream retry logic doesn't have to
   special-case each vendor.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Vendor-specific codes that mean "the refresh / access token is no
# longer valid; user must re-authenticate." Mapped to the RFC-canonical
# ``invalid_grant`` so callers can handle one code, not ten.
_VENDOR_TO_RFC_CODE: dict[str, str] = {
    "invalid_refresh_token": "invalid_grant",
    "expired_refresh_token": "invalid_grant",
    "token_expired": "invalid_grant",
}


def normalize_oauth_error_body(
    status_code: int, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Normalize an OAuth response (status_code, json_body) for vendor quirks.

    Returns the (possibly rewritten) ``(status_code, body)`` pair.
    Mutates ``body`` in place (callers that want to preserve the
    original should ``copy()`` first; OAuth bodies are typically
    discarded after handling).
    """
    if not isinstance(body, dict):
        return status_code, body

    # Rule 1: 2xx + truthy error-shaped body without an access_token = OAuth
    # error masquerading as success. ``body.get("error")`` (vs ``"error"
    # in body``) correctly treats ``{"error": null}`` and ``{"error":
    # ""}`` as not-an-error.
    if (
        200 <= status_code < 300
        and body.get("error")
        and "access_token" not in body
    ):
        logger.debug(
            "OAuth error normalization: rewriting 2xx response with error "
            "body to 400 (error=%r)",
            body.get("error"),
        )
        status_code = 400

    # Rule 2: map vendor-specific error codes to the RFC canonical.
    err = body.get("error")
    if isinstance(err, str) and err in _VENDOR_TO_RFC_CODE:
        canonical = _VENDOR_TO_RFC_CODE[err]
        logger.debug(
            "OAuth error normalization: mapping vendor code %r → RFC code %r",
            err, canonical,
        )
        body["error"] = canonical

    return status_code, body
