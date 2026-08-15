"""``clawcodex web`` — the browser-client launcher.

The properties worth pinning are the ones a mistake would make dangerous or
confusing rather than merely broken: the loopback guard (``GET /`` hands out
the session token), the URL a browser can actually reach, and the argv
translation that makes ``web`` a thin wrapper over ``serve`` rather than a
second server with its own drifting flags.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from src.entrypoints import web_cli


# ── loopback classification ──────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.5.5.5", ""])
def test_local_hosts_are_loopback(host: str) -> None:
    assert web_cli.is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.3",
                                  "example.com", "my-laptop.local"])
def test_reachable_hosts_are_not_loopback(host: str) -> None:
    """Anything we cannot prove is machine-local counts as remote — including a
    hostname we cannot classify."""
    assert web_cli.is_loopback(host) is False


def test_remote_bind_is_refused_without_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    assert web_cli.run_web_subcommand(["--host", "0.0.0.0"]) == 2
    assert "refusing to bind" in capsys.readouterr().err


def test_remote_bind_opt_in_gets_past_the_guard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--allow-remote clears the guard; the run then fails on the NEXT check
    (no bundle) rather than on the bind address."""
    monkeypatch.setattr("src.server.web_assets.web_dist_dir", lambda: None)
    assert web_cli.run_web_subcommand(["--host", "0.0.0.0", "--allow-remote"]) == 1
    assert "refusing to bind" not in capsys.readouterr().err


# ── browser URL ──────────────────────────────────────────────────────────────


def test_browser_url_carries_the_token() -> None:
    assert web_cli.browser_url("127.0.0.1", 8317, "abc") == "http://127.0.0.1:8317/?token=abc"


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", ""])
def test_wildcard_bind_becomes_a_connectable_host(bind: str) -> None:
    """`0.0.0.0` means "every interface" — it is not an address a browser can
    open, so the printed URL points at loopback instead."""
    assert web_cli.browser_url(bind, 9000, "t").startswith("http://127.0.0.1:9000/")


def test_ipv6_host_is_bracketed() -> None:
    assert web_cli.browser_url("::1", 9000, "t") == "http://[::1]:9000/?token=t"


# ── argv translation ─────────────────────────────────────────────────────────


def _args(**overrides: object) -> argparse.Namespace:
    base = dict(
        host="127.0.0.1", port=0, token=None, workspace=None, provider=None, model=None,
        effort=None, permission_mode=None, dangerously_skip_permissions=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_serve_argv_is_minimal_by_default() -> None:
    # _serve_argv always receives a RESOLVED port (run_web_subcommand resolves
    # before delegating), so the translation itself has no default to apply.
    assert web_cli._serve_argv(_args(port=8081)) == ["--host", "127.0.0.1", "--port", "8081"]


def test_serve_argv_forwards_every_agent_flag() -> None:
    argv = web_cli._serve_argv(
        _args(
            port=8317, token="t", workspace="/w", provider="deepseek",
            model="deepseek-v4-pro", effort="high", permission_mode="plan",
            dangerously_skip_permissions=True,
        )
    )
    assert argv == [
        "--host", "127.0.0.1", "--port", "8317", "--token", "t", "--workspace", "/w",
        "--provider", "deepseek", "--model", "deepseek-v4-pro", "--effort", "high",
        "--permission-mode", "plan", "--dangerously-skip-permissions",
    ]


def test_web_delegates_to_serve_with_a_ready_hook(monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """The hook is what makes `web` a wrapper: serve owns the server, and this
    entry only decides what happens once the socket is bound."""
    monkeypatch.setattr("src.server.web_assets.web_dist_dir", lambda: Path("/tmp/dist"))
    seen: dict[str, object] = {}

    def fake_serve(argv, *, on_ready=None):
        seen["argv"] = argv
        assert on_ready is not None
        on_ready("127.0.0.1", 4321, "tok")
        return 0

    monkeypatch.setattr("src.entrypoints.serve_cli.run_serve_subcommand", fake_serve)
    monkeypatch.setattr(web_cli, "_port_is_free", lambda host, port: True)

    assert web_cli.run_web_subcommand(["--no-open", "--port", "4321"]) == 0
    assert seen["argv"] == ["--host", "127.0.0.1", "--port", "4321"]
    assert "http://127.0.0.1:4321/?token=tok" in capsys.readouterr().out


def test_missing_bundle_reports_how_to_build(monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("src.server.web_assets.web_dist_dir", lambda: None)
    assert web_cli.run_web_subcommand(["--no-open"]) == 1
    err = capsys.readouterr().err
    assert "not built yet" in err
    assert "--build" in err


def test_cli_routes_web_to_the_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    """`clawcodex web` must reach this entry from argv[0], like the other
    fast-path subcommands — not fall through to the prompt parser."""
    import sys

    import src.cli as cli

    calls: list[list[str]] = []
    monkeypatch.setattr(web_cli, "run_web_subcommand", lambda rest: calls.append(rest) or 0)
    monkeypatch.setattr(sys, "argv", ["clawcodex", "web", "--no-open"])

    assert cli.main() == 0
    assert calls == [["--no-open"]]


# ── port resolution ──────────────────────────────────────────────────────────


def test_no_flag_lands_on_the_stable_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A default you can type from memory: `clawcodex web` always tries 8081
    first, instead of the OS-assigned port that changed every launch."""
    monkeypatch.setattr(web_cli, "_port_is_free", lambda host, port: True)

    assert web_cli._resolve_port("127.0.0.1", None) == web_cli.DEFAULT_WEB_PORT


def test_a_busy_default_scans_upward(monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    """A second server coexists on the next port instead of dying on
    "address already in use"."""
    monkeypatch.setattr(web_cli, "_port_is_free",
                        lambda host, port: port != web_cli.DEFAULT_WEB_PORT)

    assert web_cli._resolve_port("127.0.0.1", None) == web_cli.DEFAULT_WEB_PORT + 1
    assert f"using {web_cli.DEFAULT_WEB_PORT + 1}" in capsys.readouterr().out


def test_an_explicit_busy_port_is_refused_not_substituted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Naming a port means it — serving a neighbour would hand the user a URL
    that does not match what they asked for."""
    monkeypatch.setattr(web_cli, "_port_is_free", lambda host, port: False)

    assert web_cli._resolve_port("127.0.0.1", 9013) is None
    assert "9013 is already in use" in capsys.readouterr().err


def test_port_zero_stays_os_assigned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_cli, "_port_is_free", lambda host, port: False)

    assert web_cli._resolve_port("127.0.0.1", 0) == 0


def test_no_flag_delegates_the_default_to_serve(monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("src.server.web_assets.web_dist_dir", lambda: Path("/tmp/dist"))
    monkeypatch.setattr(web_cli, "_port_is_free", lambda host, port: True)
    seen: dict[str, object] = {}

    def fake_serve(argv, *, on_ready=None):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("src.entrypoints.serve_cli.run_serve_subcommand", fake_serve)

    assert web_cli.run_web_subcommand(["--no-open"]) == 0
    assert seen["argv"] == ["--host", "127.0.0.1", "--port", str(web_cli.DEFAULT_WEB_PORT)]
