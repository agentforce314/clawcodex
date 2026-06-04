"""Shared test helpers for ``tests/bridge/``."""

from __future__ import annotations

import base64
import json
from typing import Any


def make_jwt(payload: dict[str, Any]) -> str:
    """Build a synthetic JWT for tests.

    Used by ``test_jwt_utils`` and ``test_token_refresh_scheduler``. No
    signature verification is performed by the production code, so a
    fake signature segment is fine.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b'=').decode('ascii')
    body = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).rstrip(b'=').decode('ascii')
    return f'{header}.{body}.signature'


__all__ = ['make_jwt']
