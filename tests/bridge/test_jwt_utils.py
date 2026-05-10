"""Tests for the JWT decoder half of ``src.bridge.jwt_utils``."""

from __future__ import annotations

import base64
import time

from src.bridge.jwt_utils import decode_jwt_expiry, decode_jwt_payload

from .conftest import make_jwt


def test_decode_payload_valid_jwt() -> None:
    expiry = int(time.time()) + 3600
    token = make_jwt({'exp': expiry, 'sub': 'me'})
    payload = decode_jwt_payload(token)
    assert payload is not None
    assert payload['exp'] == expiry
    assert payload['sub'] == 'me'


def test_decode_expiry_returns_exp_seconds() -> None:
    expiry = int(time.time()) + 100
    token = make_jwt({'exp': expiry})
    assert decode_jwt_expiry(token) == expiry


def test_decode_payload_strips_session_ingress_prefix() -> None:
    expiry = int(time.time()) + 1
    token = 'sk-ant-si-' + make_jwt({'exp': expiry})
    assert decode_jwt_expiry(token) == expiry


def test_decode_payload_returns_none_for_malformed_token() -> None:
    # Not 3 parts.
    assert decode_jwt_payload('a.b') is None
    assert decode_jwt_payload('') is None
    # Empty payload segment.
    assert decode_jwt_payload('header..sig') is None
    # Non-base64url payload.
    assert decode_jwt_payload('header.!!!.sig') is None


def test_decode_payload_returns_none_for_non_json_payload() -> None:
    body = base64.urlsafe_b64encode(b'not json').rstrip(b'=').decode('ascii')
    assert decode_jwt_payload(f'header.{body}.sig') is None


def test_decode_payload_returns_none_for_non_object_payload() -> None:
    """JWT spec allows only objects; arrays should be rejected."""
    body = base64.urlsafe_b64encode(b'[1,2,3]').rstrip(b'=').decode('ascii')
    assert decode_jwt_payload(f'header.{body}.sig') is None


def test_decode_expiry_returns_none_when_exp_missing() -> None:
    token = make_jwt({'sub': 'me'})  # no ``exp``
    assert decode_jwt_expiry(token) is None


def test_decode_expiry_returns_none_when_exp_not_int() -> None:
    token = make_jwt({'exp': '3600'})  # string, not int — must reject
    assert decode_jwt_expiry(token) is None
