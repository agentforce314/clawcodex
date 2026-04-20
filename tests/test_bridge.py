"""Tests for Bridge/Remote subsystem."""
from __future__ import annotations

import asyncio
import time
import unittest

from src.services.bridge.auth import BridgeAuth, BridgeToken
from src.services.bridge.session import (
    BridgeSession,
    BridgeSessionConfig,
    BridgeSessionState,
)
from src.services.bridge.transport import BridgeMessage, WebSocketTransport


class TestBridgeSession(unittest.TestCase):
    def test_default_state(self) -> None:
        session = BridgeSession()
        self.assertEqual(session.state, BridgeSessionState.INITIALIZING)
        self.assertFalse(session.is_connected)

    def test_mark_connected(self) -> None:
        session = BridgeSession()
        session.mark_connected()
        self.assertTrue(session.is_connected)
        self.assertIsNotNone(session.connected_at)
        self.assertIsNone(session.error)

    def test_mark_disconnected(self) -> None:
        session = BridgeSession()
        session.mark_connected()
        session.mark_disconnected()
        self.assertEqual(session.state, BridgeSessionState.DISCONNECTED)

    def test_mark_error(self) -> None:
        session = BridgeSession()
        session.mark_disconnected(error="Connection lost")
        self.assertEqual(session.state, BridgeSessionState.ERROR)
        self.assertEqual(session.error, "Connection lost")

    def test_heartbeat(self) -> None:
        session = BridgeSession()
        session.mark_connected()
        old_hb = session.last_heartbeat
        time.sleep(0.01)
        session.heartbeat()
        self.assertGreater(session.last_heartbeat, old_hb)

    def test_config(self) -> None:
        config = BridgeSessionConfig(
            server_url="wss://bridge.example.com",
            auth_token="secret",
            reconnect_attempts=5,
        )
        session = BridgeSession(config=config)
        self.assertEqual(session.config.server_url, "wss://bridge.example.com")
        self.assertIsNotNone(session.session_id)


class TestBridgeAuth(unittest.TestCase):
    def test_no_token(self) -> None:
        auth = BridgeAuth()
        self.assertFalse(auth.is_authenticated)
        self.assertEqual(auth.get_auth_headers(), {})

    def test_set_token(self) -> None:
        auth = BridgeAuth()
        token = auth.set_token("my-secret-token")
        self.assertTrue(auth.is_authenticated)
        self.assertEqual(token.token, "my-secret-token")

    def test_auth_headers(self) -> None:
        auth = BridgeAuth()
        auth.set_token("tok123")
        headers = auth.get_auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer tok123")

    def test_expired_token(self) -> None:
        auth = BridgeAuth()
        auth.set_token("expired", expires_at=time.time() - 100)
        self.assertFalse(auth.is_authenticated)
        self.assertIsNone(auth.current_token)

    def test_clear_token(self) -> None:
        auth = BridgeAuth()
        auth.set_token("tok")
        auth.clear()
        self.assertFalse(auth.is_authenticated)

    def test_token_no_expiry(self) -> None:
        token = BridgeToken(token="permanent", expires_at=0)
        self.assertFalse(token.is_expired)
        self.assertTrue(token.is_valid)


class TestBridgeTransport(unittest.TestCase):
    def test_websocket_transport_lifecycle(self) -> None:
        async def run():
            transport = WebSocketTransport()
            self.assertFalse(transport.is_connected)

            await transport.connect("wss://example.com")
            self.assertTrue(transport.is_connected)

            await transport.close()
            self.assertFalse(transport.is_connected)

        asyncio.run(run())

    def test_send_requires_connection(self) -> None:
        async def run():
            transport = WebSocketTransport()
            with self.assertRaises(ConnectionError):
                await transport.send(BridgeMessage(type="test", payload={}))

        asyncio.run(run())

    def test_bridge_message(self) -> None:
        msg = BridgeMessage(type="query", payload={"text": "hello"}, id="m1")
        self.assertEqual(msg.type, "query")
        self.assertEqual(msg.payload["text"], "hello")


if __name__ == "__main__":
    unittest.main()
