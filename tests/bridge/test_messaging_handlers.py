"""Tests for ``src.bridge.messaging_handlers.handle_server_control_request``."""

from __future__ import annotations

import asyncio
import os

import pytest

from src.bridge.messaging_handlers import (
    OUTBOUND_ONLY_ERROR,
    Err,
    Ok,
    ServerControlRequestHandlers,
    handle_server_control_request,
)


class _FakeTransport:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    def write(self, message: dict) -> None:
        self.writes.append(message)


def _make_handlers(**kw) -> tuple[_FakeTransport, ServerControlRequestHandlers]:
    transport = _FakeTransport()
    handlers = ServerControlRequestHandlers(
        transport=transport,
        session_id='cse_test',
        **kw,
    )
    return transport, handlers


class TestSubtypeDispatch:
    def test_initialize_returns_minimal_capabilities(self) -> None:
        transport, handlers = _make_handlers()
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r1', 'request': {'subtype': 'initialize'}},
            handlers,
        )
        assert len(transport.writes) == 1
        env = transport.writes[0]
        assert env['type'] == 'control_response'
        assert env['session_id'] == 'cse_test'
        resp = env['response']
        assert resp['subtype'] == 'success'
        assert resp['request_id'] == 'r1'
        inner = resp['response']
        assert inner['commands'] == []
        assert inner['output_style'] == 'normal'
        assert inner['available_output_styles'] == ['normal']
        assert inner['models'] == []
        assert inner['account'] == {}
        assert inner['pid'] == os.getpid()

    def test_set_model_invokes_callback_and_returns_success(self) -> None:
        captured: list[str | None] = []
        transport, handlers = _make_handlers(on_set_model=captured.append)
        handle_server_control_request(
            {
                'type': 'control_request',
                'request_id': 'r2',
                'request': {'subtype': 'set_model', 'model': 'opus'},
            },
            handlers,
        )
        assert captured == ['opus']
        assert transport.writes[0]['response']['subtype'] == 'success'

    def test_set_model_without_callback_still_succeeds(self) -> None:
        transport, handlers = _make_handlers()
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r2', 'request': {'subtype': 'set_model', 'model': 'opus'}},
            handlers,
        )
        assert transport.writes[0]['response']['subtype'] == 'success'

    def test_set_max_thinking_tokens_dispatches(self) -> None:
        captured: list[int | None] = []
        transport, handlers = _make_handlers(on_set_max_thinking_tokens=captured.append)
        handle_server_control_request(
            {
                'type': 'control_request',
                'request_id': 'r3',
                'request': {'subtype': 'set_max_thinking_tokens', 'max_thinking_tokens': 1000},
            },
            handlers,
        )
        assert captured == [1000]
        assert transport.writes[0]['response']['subtype'] == 'success'

    def test_interrupt_dispatches(self) -> None:
        called = []
        transport, handlers = _make_handlers(on_interrupt=lambda: called.append(True))
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r4', 'request': {'subtype': 'interrupt'}},
            handlers,
        )
        assert called == [True]
        assert transport.writes[0]['response']['subtype'] == 'success'


class TestSetPermissionMode:
    def test_no_callback_returns_error_not_silent_success(self) -> None:
        """WI-3.7b chapter pattern: missing handler returns Err, NOT silent success."""
        transport, handlers = _make_handlers()
        handle_server_control_request(
            {
                'type': 'control_request',
                'request_id': 'r5',
                'request': {'subtype': 'set_permission_mode', 'mode': 'auto'},
            },
            handlers,
        )
        env = transport.writes[0]
        assert env['response']['subtype'] == 'error'
        assert 'on_set_permission_mode callback not registered' in env['response']['error']

    def test_callback_returning_ok_yields_success(self) -> None:
        transport, handlers = _make_handlers(
            on_set_permission_mode=lambda mode: Ok(),
        )
        handle_server_control_request(
            {
                'type': 'control_request',
                'request_id': 'r5',
                'request': {'subtype': 'set_permission_mode', 'mode': 'auto'},
            },
            handlers,
        )
        assert transport.writes[0]['response']['subtype'] == 'success'

    def test_callback_returning_err_yields_error_with_reason(self) -> None:
        transport, handlers = _make_handlers(
            on_set_permission_mode=lambda mode: Err(error='gate disabled'),
        )
        handle_server_control_request(
            {
                'type': 'control_request',
                'request_id': 'r5',
                'request': {'subtype': 'set_permission_mode', 'mode': 'bypass'},
            },
            handlers,
        )
        env = transport.writes[0]
        assert env['response']['subtype'] == 'error'
        assert env['response']['error'] == 'gate disabled'


class TestUnknownSubtype:
    def test_unknown_subtype_returns_error_response_not_silence(self) -> None:
        """Chapter explicit pattern: unknown subtypes get an error response,
        not silence — server kills WS in 10-14s otherwise.
        """
        transport, handlers = _make_handlers()
        handle_server_control_request(
            {
                'type': 'control_request',
                'request_id': 'r99',
                'request': {'subtype': 'set_quantum_flux'},
            },
            handlers,
        )
        env = transport.writes[0]
        assert env['response']['subtype'] == 'error'
        assert 'set_quantum_flux' in env['response']['error']
        assert env['response']['request_id'] == 'r99'


class TestOutboundOnly:
    def test_initialize_succeeds_in_outbound_only(self) -> None:
        """initialize MUST succeed even in outbound-only — server kills WS otherwise."""
        transport, handlers = _make_handlers(outbound_only=True)
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r1', 'request': {'subtype': 'initialize'}},
            handlers,
        )
        assert transport.writes[0]['response']['subtype'] == 'success'

    def test_mutable_subtype_returns_outbound_only_error(self) -> None:
        transport, handlers = _make_handlers(outbound_only=True)
        for subtype in ('set_model', 'set_max_thinking_tokens', 'set_permission_mode', 'interrupt'):
            transport.writes.clear()
            handle_server_control_request(
                {'type': 'control_request', 'request_id': 'r1', 'request': {'subtype': subtype}},
                handlers,
            )
            env = transport.writes[0]
            assert env['response']['subtype'] == 'error', f'subtype {subtype}'
            assert env['response']['error'] == OUTBOUND_ONLY_ERROR


class TestSessionIdAttachment:
    def test_session_id_in_every_response(self) -> None:
        transport, handlers = _make_handlers()
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r1', 'request': {'subtype': 'initialize'}},
            handlers,
        )
        assert transport.writes[0]['session_id'] == 'cse_test'


class TestMalformedRequest:
    def test_request_inner_not_a_dict_logged_and_dropped(self) -> None:
        transport, handlers = _make_handlers()
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r1', 'request': 'not a dict'},
            handlers,
        )
        assert transport.writes == []


class _AsyncWriteTransport:
    """Test double for the Phase 3 ``ReplBridgeTransport.write`` async surface.

    Returns a coroutine from ``write`` (matching the TS
    ``Promise<void>`` contract). ``handle_server_control_request`` must
    schedule that coroutine on the running loop or the response is never
    sent and the server kills the WS in 10-14 s.
    """

    def __init__(self) -> None:
        self.writes: list[dict] = []
        self.write_completed = asyncio.Event()

    def write(self, message: dict) -> object:
        async def _do_write() -> None:
            # Simulate any async work (e.g., HTTP POST).
            await asyncio.sleep(0)
            self.writes.append(message)
            self.write_completed.set()

        return _do_write()


class TestAsyncWriteContract:
    """Per critic #2: handle_server_control_request must support async write
    transports without losing the response.
    """

    @pytest.mark.asyncio
    async def test_async_write_is_scheduled_and_completes(self) -> None:
        transport = _AsyncWriteTransport()
        handlers = ServerControlRequestHandlers(
            transport=transport,
            session_id='cse_async',
        )
        handle_server_control_request(
            {'type': 'control_request', 'request_id': 'r1', 'request': {'subtype': 'initialize'}},
            handlers,
        )
        # Before yielding, the write hasn't run yet — the coroutine is
        # scheduled but not awaited.
        assert transport.writes == []
        # Yield once to let the scheduled task run.
        await transport.write_completed.wait()
        assert len(transport.writes) == 1
        env = transport.writes[0]
        assert env['type'] == 'control_response'
        assert env['session_id'] == 'cse_async'
        assert env['response']['subtype'] == 'success'
