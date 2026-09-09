"""`clawcodex serve` refuses to hand its session token to the network.

`GET /` is unauthenticated by construction — it is the page that *hands out*
the token so the desktop shell and the browser client can adopt a running
backend — so the trust model holds only while the port is reachable from this
machine alone. `clawcodex web` has always gated that; the command that actually
opens the socket had not.
"""

from __future__ import annotations

import pytest

from src.entrypoints import serve_cli


class _Bound(Exception):
    """Raised from the far side of the gate, to prove where a run reached."""


def test_a_loopback_bind_is_allowed_by_default() -> None:
    for host in ("127.0.0.1", "localhost", "::1", ""):
        assert serve_cli.is_loopback(host) is True


def test_a_name_we_cannot_classify_is_treated_as_remote() -> None:
    # Not provably local is not local: a LAN name or container alias could
    # resolve anywhere.
    for host in ("0.0.0.0", "192.168.1.10", "build-box.lan"):
        assert serve_cli.is_loopback(host) is False


def test_serve_refuses_a_non_loopback_bind(capsys, monkeypatch) -> None:
    # `build_app` is stubbed to raise so that a missing guard FAILS here rather
    # than hanging: without it this call reaches uvicorn and blocks forever,
    # which in CI is a timed-out job instead of a red test.
    import src.server.desktop_serve as desktop_serve

    def _stop(_state: object) -> None:
        raise _Bound

    monkeypatch.setattr(desktop_serve, "build_app", _stop)

    code = serve_cli.run_serve_subcommand(["--host", "0.0.0.0", "--port", "0"])

    assert code == 2
    message = capsys.readouterr().err
    assert "refusing to bind 0.0.0.0" in message
    # Says why, and what to do about it.
    assert "session token" in message
    assert "--allow-remote" in message


def test_allow_remote_gets_past_the_gate(monkeypatch) -> None:
    """The opt-out must actually opt out.

    Asserting the flag parses would pass even if the guard still refused it, so
    this stops the run at `build_app` — the far side of the gate — and asserts
    it was reached rather than that a return code was avoided.
    """
    import src.server.desktop_serve as desktop_serve

    def _stop(_state: object) -> None:
        raise _Bound

    monkeypatch.setattr(desktop_serve, "build_app", _stop)

    with pytest.raises(_Bound):
        serve_cli.run_serve_subcommand(["--host", "0.0.0.0", "--allow-remote", "--port", "0"])
