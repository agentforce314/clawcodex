import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.mcp.client import (
    McpClient,
    MAX_RECONNECT_ATTEMPTS,
    _cache_key_for,
    _unwrap_exception_group_message,
)
from src.services.mcp.types import (
    ConnectedMCPServer,
    FailedMCPServer,
    McpHTTPServerConfig,
    McpStdioServerConfig,
    ScopedMcpServerConfig,
    ServerCapabilities,
)


class TestMcpClientReconnection:
    def test_initial_state(self):
        client = McpClient()
        assert client.is_connected is False
        assert client._reconnect_attempt == 0

    def test_resource_cache(self):
        client = McpClient()
        assert client._resource_cache == {}
        client.clear_resource_cache()
        assert client._resource_cache == {}


class TestMcpClientProperties:
    def test_capabilities(self):
        client = McpClient()
        caps = client.capabilities
        assert isinstance(caps, ServerCapabilities)
        assert caps.tools is False

    def test_server_info(self):
        client = McpClient()
        assert client.server_info is None

    def test_instructions(self):
        client = McpClient()
        assert client.instructions is None


class TestMcpClientConstants:
    def test_max_reconnect_attempts(self):
        assert MAX_RECONNECT_ATTEMPTS == 5


class TestMcpClientClose:
    @pytest.mark.asyncio
    async def test_close_no_transport(self):
        client = McpClient()
        await client.close()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_close_sets_disconnected(self):
        client = McpClient()
        client._connected = True
        await client.close()
        assert client.is_connected is False


class TestConnectionCacheKey:
    """Regression coverage for WI-2.5: content-based cache key.

    Two equivalent configs from different dataclass instances must hit the
    same cache entry; materially different configs must not. The previous
    keying (``id(config)``) missed on every config-object reconstruction.
    """

    def test_equivalent_stdio_configs_share_key(self):
        config_a = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server", args=["--port", "9999"]),
            scope="project",
        )
        config_b = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server", args=["--port", "9999"]),
            scope="project",
        )
        assert _cache_key_for("srv", config_a) == _cache_key_for("srv", config_b)

    def test_different_stdio_args_distinct_keys(self):
        config_a = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server", args=["--port", "9999"]),
            scope="project",
        )
        config_b = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server", args=["--port", "8888"]),
            scope="project",
        )
        assert _cache_key_for("srv", config_a) != _cache_key_for("srv", config_b)

    def test_equivalent_http_configs_share_key(self):
        config_a = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(url="https://mcp.example.com/v1"),
            scope="project",
        )
        config_b = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(url="https://mcp.example.com/v1"),
            scope="project",
        )
        assert _cache_key_for("srv", config_a) == _cache_key_for("srv", config_b)

    def test_different_names_distinct_keys(self):
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server"),
            scope="project",
        )
        assert _cache_key_for("a", config) != _cache_key_for("b", config)

    def test_different_env_distinct_keys_credential_isolation(self):
        """Credential-isolation regression: two stdio configs with the same
        command/args but different env (e.g. different API keys) MUST produce
        distinct cache keys. Otherwise the second registration would silently
        reuse the first server's authenticated connection.

        This is the failure mode the prior ``get_mcp_server_signature``-based
        keying allowed; mirrors TS' full-config-serialization keying at
        ``typescript/src/services/mcp/client.ts:600-606``.
        """
        config_a = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command="my-server", args=["--p", "9999"], env={"API_KEY": "k1"}
            ),
            scope="project",
        )
        config_b = ScopedMcpServerConfig(
            config=McpStdioServerConfig(
                command="my-server", args=["--p", "9999"], env={"API_KEY": "k2"}
            ),
            scope="project",
        )
        assert _cache_key_for("srv", config_a) != _cache_key_for("srv", config_b)

    def test_different_http_headers_distinct_keys_credential_isolation(self):
        """Same credential-isolation contract for HTTP transports: two HTTP
        configs with the same URL but different ``Authorization`` headers MUST
        produce distinct cache keys."""
        config_a = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(
                url="https://mcp.example.com/v1",
                headers={"Authorization": "Bearer t1"},
            ),
            scope="project",
        )
        config_b = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(
                url="https://mcp.example.com/v1",
                headers={"Authorization": "Bearer t2"},
            ),
            scope="project",
        )
        assert _cache_key_for("srv", config_a) != _cache_key_for("srv", config_b)

    def test_different_scope_distinct_keys(self):
        """``project`` vs ``user`` scope must not share cache entries even with
        otherwise-identical configs (different policy lifecycles)."""
        config_proj = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server"),
            scope="project",
        )
        config_user = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="my-server"),
            scope="user",
        )
        assert _cache_key_for("srv", config_proj) != _cache_key_for("srv", config_user)


class TestExceptionGroupUnwrap:
    """Regression: anyio task groups in the SDK wrap real connection errors
    in BaseExceptionGroup, whose ``str()`` is "unhandled errors in a TaskGroup
    (1 sub-exception)" — useless for diagnosing an unreachable server. The
    unwrapper walks the group tree and returns the leaf's message."""

    def test_plain_exception_passthrough(self):
        exc = ConnectionRefusedError("port 1 closed")
        assert _unwrap_exception_group_message(exc) == "port 1 closed"

    def test_unwraps_single_subexception(self):
        try:
            inner_exc = ConnectionRefusedError("nobody home")
            raise BaseExceptionGroup("unhandled errors", [inner_exc])
        except BaseExceptionGroup as eg:
            assert _unwrap_exception_group_message(eg) == "nobody home"

    def test_recurses_through_nested_groups(self):
        try:
            inner = TimeoutError("connect timed out")
            mid = BaseExceptionGroup("inner group", [inner])
            raise BaseExceptionGroup("outer group", [mid])
        except BaseExceptionGroup as eg:
            assert _unwrap_exception_group_message(eg) == "connect timed out"

    def test_falls_back_to_class_name_when_str_is_empty(self):
        class MyError(Exception):
            pass

        # __str__ returns "" if no args
        assert _unwrap_exception_group_message(MyError()) == "MyError"


class TestSessionExpiryRetry:
    """Phase 6a WI-6.1 (gap #8): when a Streamable-HTTP server restarts and
    returns a session-terminated error, ``call_tool`` must clear the cache,
    reconnect, and retry the call once. A second session-expired error on
    retry surfaces (no infinite loop)."""

    @pytest.mark.asyncio
    async def test_session_expired_triggers_reconnect_and_retry_succeeds(self):
        from src.services.mcp.errors import McpToolCallError
        from src.services.mcp.types import McpStdioServerConfig, ScopedMcpServerConfig

        client = McpClient()
        client._name = "test-server"
        client._config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="dummy"),
            scope="project",
        )

        # First call raises session-expired; second call (after reconnect)
        # returns success. Tracker captures the call sequence.
        calls = []

        async def fake_send_request(method, params=None):
            calls.append(method)
            if len(calls) == 1:
                raise McpToolCallError(
                    '{"code":32600,"message":"Session terminated"}',
                    "Session terminated",
                )
            return {
                "content": [{"type": "text", "text": "ok after retry"}],
                "isError": False,
            }

        # Stub reconnect() to return a Connected state without doing real I/O.
        async def fake_reconnect():
            return ConnectedMCPServer(name="test-server", config=client._config)

        with patch.object(client, "_send_request", side_effect=fake_send_request):
            with patch.object(client, "reconnect", side_effect=fake_reconnect):
                result = await client.call_tool("greet", {"name": "world"})

        assert result.is_error is False
        assert result.content[0]["text"] == "ok after retry"
        # Confirm exactly two send_request invocations: original + one retry.
        assert calls == ["tools/call", "tools/call"]

    @pytest.mark.asyncio
    async def test_session_expired_retry_propagates_second_failure(self):
        """If the retry also fails with session-expired, surface the error
        instead of looping (no infinite recovery storm)."""
        from src.services.mcp.errors import McpToolCallError
        from src.services.mcp.types import McpStdioServerConfig, ScopedMcpServerConfig

        client = McpClient()
        client._name = "test-server"
        client._config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="dummy"),
            scope="project",
        )

        calls = []

        async def fake_send_request(method, params=None):
            calls.append(method)
            raise McpToolCallError(
                '{"code":32600,"message":"Session terminated"}',
                "Session terminated",
            )

        async def fake_reconnect():
            return ConnectedMCPServer(name="test-server", config=client._config)

        with patch.object(client, "_send_request", side_effect=fake_send_request):
            with patch.object(client, "reconnect", side_effect=fake_reconnect):
                with pytest.raises(McpToolCallError, match="Session terminated"):
                    await client.call_tool("greet", {"name": "world"})

        assert len(calls) == 2  # original + one retry, then raise

    @pytest.mark.asyncio
    async def test_non_session_expired_error_propagates_without_retry(self):
        """A non-session-expired McpToolCallError must NOT trigger reconnect.
        E.g., a tool returning a regular invalid-input error should fail
        immediately with no extra connect overhead."""
        from src.services.mcp.errors import McpToolCallError
        from src.services.mcp.types import McpStdioServerConfig, ScopedMcpServerConfig

        client = McpClient()
        client._name = "test-server"
        client._config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="dummy"),
            scope="project",
        )

        calls = []
        reconnect_called = []

        async def fake_send_request(method, params=None):
            calls.append(method)
            raise McpToolCallError(
                '{"code":-32602,"message":"Invalid params"}',
                "Invalid params",
            )

        async def fake_reconnect():
            reconnect_called.append(True)
            return ConnectedMCPServer(name="test-server", config=client._config)

        with patch.object(client, "_send_request", side_effect=fake_send_request):
            with patch.object(client, "reconnect", side_effect=fake_reconnect):
                with pytest.raises(McpToolCallError, match="Invalid params"):
                    await client.call_tool("greet", {"name": "world"})

        assert len(calls) == 1, "non-session-expired error should not retry"
        assert len(reconnect_called) == 0, "reconnect must not be called"

    @pytest.mark.asyncio
    async def test_session_expired_with_failed_reconnect_propagates_original(self):
        """If reconnect fails after a session-expired signal, the SAME original
        error instance must propagate (not a fresh one with the same message).
        Identity assertion guards against a regression where the recovery path
        might lose the original exception's traceback / context.
        """
        from src.services.mcp.errors import McpToolCallError
        from src.services.mcp.types import (
            FailedMCPServer,
            McpStdioServerConfig,
            ScopedMcpServerConfig,
        )

        client = McpClient()
        client._name = "test-server"
        client._config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="dummy"),
            scope="project",
        )

        original = McpToolCallError(
            '{"code":-32001,"message":"Session expired"}',
            "Session expired",
        )

        async def fake_send_request(method, params=None):
            raise original

        async def fake_reconnect():
            return FailedMCPServer(name="test-server", error="connection refused")

        with patch.object(client, "_send_request", side_effect=fake_send_request):
            with patch.object(client, "reconnect", side_effect=fake_reconnect):
                with pytest.raises(McpToolCallError) as excinfo:
                    await client.call_tool("greet", {"name": "world"})
        # Identity check, not just message match.
        assert excinfo.value is original

    @pytest.mark.asyncio
    async def test_concurrent_session_expired_calls_share_one_reconnect(self):
        """Concurrent call_tool invocations that all observe an expired
        session must NOT trigger N reconnects — one reconnect serves all.
        Verified by counting reconnect invocations under N=10 parallel
        callers, where the first call to each invokes session-expired."""
        from src.services.mcp.errors import McpToolCallError
        from src.services.mcp.types import McpStdioServerConfig, ScopedMcpServerConfig

        client = McpClient()
        client._name = "test-server"
        client._config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="dummy"),
            scope="project",
        )

        # First N calls (the original tries by all parallel callers) all
        # raise session-expired; calls N+1..2N (the retries after recovery)
        # all succeed. With N=10 concurrent callers, that's 20 total
        # _send_request invocations. Without the lock + epoch, the recovery
        # path would call reconnect 10 times instead of once.
        N_CALLERS = 10
        call_history: list[str] = []

        async def fake_send_request(method, params=None):
            idx = len(call_history)
            call_history.append(method)
            if idx < N_CALLERS:
                raise McpToolCallError(
                    '{"code":32600,"message":"Session terminated"}',
                    "Session terminated",
                )
            return {"content": [{"type": "text", "text": f"ok-{idx}"}], "isError": False}

        reconnect_calls: list[int] = []

        async def fake_reconnect():
            reconnect_calls.append(1)
            # Slow reconnect so all 10 callers pile up on the lock.
            await asyncio.sleep(0.05)
            return ConnectedMCPServer(name="test-server", config=client._config)

        with patch.object(client, "_send_request", side_effect=fake_send_request):
            with patch.object(client, "reconnect", side_effect=fake_reconnect):
                results = await asyncio.gather(
                    *(client.call_tool("greet", {"i": i}) for i in range(N_CALLERS)),
                    return_exceptions=True,
                )

        # All N_CALLERS succeeded.
        assert all(not isinstance(r, Exception) for r in results), results
        # But reconnect was called exactly once — the lock + epoch suppressed
        # the stampede.
        assert len(reconnect_calls) == 1, (
            f"expected 1 reconnect, got {len(reconnect_calls)}"
        )
