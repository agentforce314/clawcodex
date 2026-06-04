"""Tests for ``src.server.url_scheme``."""

from __future__ import annotations

import pytest

from src.server.url_scheme import parse_cc_url


class TestCcTcp:
    def test_basic(self) -> None:
        addr = parse_cc_url('cc://127.0.0.1:8765/sess_abc')
        assert addr.scheme == 'cc'
        assert addr.host_or_socket == '127.0.0.1'
        assert addr.port == 8765
        assert addr.session_id == 'sess_abc'
        assert addr.query == {}

    def test_no_port(self) -> None:
        addr = parse_cc_url('cc://localhost/sess_abc')
        assert addr.host_or_socket == 'localhost'
        assert addr.port is None

    def test_with_query(self) -> None:
        addr = parse_cc_url('cc://h:1/sid?token=tk&foo=bar')
        assert addr.query == {'token': 'tk', 'foo': 'bar'}

    def test_invalid_port(self) -> None:
        with pytest.raises(ValueError, match='invalid port'):
            parse_cc_url('cc://h:abc/sid')

    def test_port_out_of_range(self) -> None:
        with pytest.raises(ValueError, match='port out of range'):
            parse_cc_url('cc://h:99999/sid')

    def test_missing_session(self) -> None:
        with pytest.raises(ValueError, match='session ID'):
            parse_cc_url('cc://h:1/')

    def test_missing_authority(self) -> None:
        with pytest.raises(ValueError):
            parse_cc_url('cc:///sid')


class TestCcUnix:
    def test_basic(self) -> None:
        addr = parse_cc_url('cc+unix:///var/run/claude/sock/sess_abc')
        assert addr.scheme == 'cc+unix'
        assert addr.host_or_socket == '/var/run/claude/sock'
        assert addr.port is None
        assert addr.session_id == 'sess_abc'

    def test_socket_path_with_query(self) -> None:
        addr = parse_cc_url('cc+unix:///tmp/x.sock/sid?token=tk')
        assert addr.host_or_socket == '/tmp/x.sock'
        assert addr.session_id == 'sid'
        assert addr.query == {'token': 'tk'}

    def test_deep_socket_path(self) -> None:
        addr = parse_cc_url('cc+unix:///a/b/c/d/e/sid')
        assert addr.host_or_socket == '/a/b/c/d/e'
        assert addr.session_id == 'sid'


class TestSchemeDispatch:
    def test_unsupported_scheme(self) -> None:
        with pytest.raises(ValueError, match='unsupported scheme'):
            parse_cc_url('http://example.com/foo')

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            parse_cc_url('')
