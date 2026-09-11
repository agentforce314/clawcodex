"""`clawcodex agent-server` refuses to serve session creation to the network.

`POST /sessions` authenticates only when a token was configured
(`server/server.py`: ``if self.config.auth_token``), and ``--token`` is
optional. On the default loopback bind that is fine — reaching the port already
means being on this machine. Off it, the pair serves session creation to anyone
who can route there, and a session is a shell.

Remote binding itself stays supported: this server is written for it, so only
the undefendable combination is refused.
"""

from __future__ import annotations

import pytest

from src.entrypoints import agent_server_cli


class _Started(Exception):
    """Raised from the far side of the gate, to prove where a run reached."""


def test_a_remote_bind_without_a_token_is_refused(capsys, monkeypatch) -> None:
    # `_serve` is stubbed to raise so that a missing guard FAILS here rather
    # than hanging: without it this call starts a real server and blocks, which
    # in CI is a timed-out job instead of a red test.
    async def _stop(*_args: object, **_kwargs: object) -> int:
        raise _Started

    monkeypatch.setattr(agent_server_cli, "_serve", _stop)

    code = agent_server_cli.run_agent_server_subcommand(["--host", "0.0.0.0"])

    assert code == 2
    message = capsys.readouterr().err
    assert "refusing to bind 0.0.0.0" in message
    # Says which flag closes it, and why it is open.
    assert "--token" in message
    assert "POST /sessions" in message


def test_a_remote_bind_with_a_token_is_allowed(monkeypatch) -> None:
    """The token is the opt-out, so it has to actually get through.

    Stopped at `_serve` — the far side of the gate — rather than asserting a
    return code, which would also pass if the guard silently refused.
    """

    async def _stop(*_args: object, **_kwargs: object) -> int:
        raise _Started

    monkeypatch.setattr(agent_server_cli, "_serve", _stop)

    with pytest.raises(_Started):
        agent_server_cli.run_agent_server_subcommand(
            ["--host", "0.0.0.0", "--token", "s3cret"]
        )


def test_the_default_loopback_bind_is_untouched(monkeypatch) -> None:
    """A tokenless local run is the ordinary case and must keep working."""

    async def _stop(*_args: object, **_kwargs: object) -> int:
        raise _Started

    monkeypatch.setattr(agent_server_cli, "_serve", _stop)

    with pytest.raises(_Started):
        agent_server_cli.run_agent_server_subcommand([])


def test_stdio_is_not_a_bind_and_is_not_refused(monkeypatch) -> None:
    """`--stdio` serves over this process's pipes and never opens the port.

    `--host` is inert in that mode, so refusing the pair would block a caller
    that is not exposing anything — and the way around a guard like that is a
    throwaway token, which is worse than no guard.
    """

    async def _stop(*_args: object, **_kwargs: object) -> int:
        raise _Started

    monkeypatch.setattr(agent_server_cli, "_serve_stdio", _stop)

    with pytest.raises(_Started):
        agent_server_cli.run_agent_server_subcommand(["--stdio", "--host", "0.0.0.0"])
