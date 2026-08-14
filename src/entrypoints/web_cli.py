"""``clawcodex web`` — open the ClawCodex browser client.

The web client is not a separate server: it is the ``clawcodex serve`` gateway
with a built ``ui-web/dist`` mounted on it, so the browser talks to the same
in-process agent the TUI and the desktop app do, over the same JSON-RPC socket
(``/api/ws``). This entry is the convenience wrapper around that — it resolves
the bundle, starts the gateway, prints the tokened URL and opens a browser.

Usage::

    clawcodex web [--host H] [--port P] [--no-open] [--build]
                  [--workspace DIR] [--provider NAME] [--model M] [--effort E]
                  [--permission-mode MODE] [--allow-remote]

Binding
-------
``GET /`` serves the app with the session token inlined, because a browser has
no other way to learn it. That page is therefore unauthenticated *by
construction*, which is safe only while the server is reachable from this
machine alone — so a non-loopback ``--host`` is refused unless the caller
passes ``--allow-remote`` and thereby takes responsibility for the network in
front of it.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

# Hosts that only accept connections originating on this machine.
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", ""})


def repo_root() -> Path:
    """The clawcodex checkout this module runs from."""
    return Path(__file__).resolve().parents[2]


def web_app_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "ui-web"


def is_loopback(host: str) -> bool:
    """True when ``host`` binds to this machine only."""
    normalized = (host or "").strip().lower()
    if normalized in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # A hostname we cannot classify (a LAN name, a container alias) is not
        # provably local, so it is treated as remote.
        return False


def browser_url(host: str, port: int, token: str) -> str:
    """The URL to open: the token rides the query so a fresh tab can adopt it.

    The client strips it from the address bar after reading it, so it does not
    linger in history or in a copied link.
    """
    # A bind address of 0.0.0.0 / :: means "every interface"; it is not an
    # address a browser can connect to.
    display = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    if ":" in display and not display.startswith("["):
        display = f"[{display}]"
    return f"http://{display}:{port}/?token={token}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawcodex web",
        description="Serve the ClawCodex web client and open it in a browser.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1 — this machine only).")
    parser.add_argument("--port", type=int, default=0,
                        help="Port (default: 0 — OS-assigned; the URL is printed).")
    parser.add_argument("--token", default=None, help="Session token (default: generated).")
    parser.add_argument("--workspace", default=None,
                        help="Default workspace for new sessions (default: cwd).")
    parser.add_argument("--provider", default=None, help="Provider name override.")
    parser.add_argument("--model", default=None, help="Model override.")
    parser.add_argument("--effort", default=None,
                        choices=("low", "medium", "high", "xhigh", "max"),
                        help="Reasoning effort seed for sessions.")
    parser.add_argument("--permission-mode", default=None, dest="permission_mode",
                        help="default | acceptEdits | bypassPermissions | plan | auto")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        dest="dangerously_skip_permissions",
                        help="Bypass all permission checks (start in bypassPermissions).")
    parser.add_argument("--no-open", action="store_true", dest="no_open",
                        help="Print the URL without launching a browser.")
    parser.add_argument("--build", action="store_true",
                        help="Build ui-web before serving (source checkouts).")
    parser.add_argument("--allow-remote", action="store_true", dest="allow_remote",
                        help="Permit a non-loopback --host. The page hands out the "
                             "session token, so only do this behind your own auth.")
    return parser


def build_web_app(app_dir: Path) -> int:
    """Run ``npm install`` (when needed) then ``npm run build`` in ``ui-web``."""
    if not (app_dir / "package.json").is_file():
        print(f"web: no web app at {app_dir}", file=sys.stderr)
        return 1

    npm = "npm.cmd" if os.name == "nt" else "npm"
    plan: list[list[str]] = []
    if not (app_dir / "node_modules").is_dir():
        plan.append([npm, "ci", "--no-audit", "--no-fund"])
    plan.append([npm, "run", "build"])

    for command in plan:
        print(f"web: {' '.join(command)}", file=sys.stderr)
        try:
            result = subprocess.run(command, cwd=str(app_dir), check=False)
        except FileNotFoundError:
            print("web: npm not found — install Node.js to build the web client",
                  file=sys.stderr)
            return 1
        if result.returncode != 0:
            return result.returncode
    return 0


def _missing_bundle_message(app_dir: Path) -> str:
    if (app_dir / "package.json").is_file():
        return (
            "web: the browser client is not built yet.\n"
            f"     Run `clawcodex web --build`, or build it directly:\n"
            f"       cd {app_dir} && npm install && npm run build"
        )
    return (
        "web: no web client bundle found. This install does not ship one; "
        "run `clawcodex web` from a source checkout, or set "
        "CLAWCODEX_WEB_DIST to a built dist/ directory."
    )


def _open_browser_soon(url: str) -> None:
    """Open the browser off the event loop, a beat after the socket is bound.

    `webbrowser.open` can block for seconds while a browser cold-starts; doing
    that inline would stall the server's first request — its own page load.
    """
    threading.Timer(0.25, lambda: webbrowser.open(url)).start()


def _serve_argv(args: argparse.Namespace) -> list[str]:
    """Translate the web flags into the ``clawcodex serve`` argv it delegates to."""
    argv = ["--host", args.host, "--port", str(args.port)]
    if args.token:
        argv += ["--token", args.token]
    if args.workspace:
        argv += ["--workspace", args.workspace]
    if args.provider:
        argv += ["--provider", args.provider]
    if args.model:
        argv += ["--model", args.model]
    if args.effort:
        argv += ["--effort", args.effort]
    if args.permission_mode:
        argv += ["--permission-mode", args.permission_mode]
    if args.dangerously_skip_permissions:
        argv.append("--dangerously-skip-permissions")
    return argv


def run_web_subcommand(argv: list[str]) -> int:
    """Entry point for ``clawcodex web``."""
    args = _build_parser().parse_args(argv)

    if not is_loopback(args.host) and not args.allow_remote:
        print(
            f"web: refusing to bind {args.host}: the page hands out this server's "
            "session token, so it must not be reachable from the network.\n"
            "     Pass --allow-remote if you are putting your own auth in front of it.",
            file=sys.stderr,
        )
        return 2

    app_dir = web_app_dir()
    if args.build:
        code = build_web_app(app_dir)
        if code != 0:
            return code

    # Import late: this pulls in starlette + the agent stack.
    from src.server.web_assets import web_dist_dir

    if web_dist_dir() is None:
        print(_missing_bundle_message(app_dir), file=sys.stderr)
        return 1

    def on_ready(host: str, port: int, token: str) -> None:
        url = browser_url(host, port, token)
        print(f"ClawCodex web is running at {url}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        if not args.no_open:
            _open_browser_soon(url)

    from src.entrypoints.serve_cli import run_serve_subcommand

    return run_serve_subcommand(_serve_argv(args), on_ready=on_ready)


__all__ = ["browser_url", "build_web_app", "is_loopback", "run_web_subcommand", "web_app_dir"]


if __name__ == "__main__":
    raise SystemExit(run_web_subcommand(sys.argv[1:]))
