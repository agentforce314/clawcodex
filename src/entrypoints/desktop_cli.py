"""``clawcodex desktop`` — launch the ClawCodex Desktop app.

Dev-oriented launcher for the Electron shell in ``ui-desktop/``: it resolves
the checkout this CLI is running from, makes sure the app's npm deps exist,
and hands off to ``npm run dev`` (Vite renderer + Electron main, which spawns
`clawcodex serve` as its backend). The spawned app is pointed back at THIS
checkout via ``CLAWCODEX_DESKTOP_BACKEND_ROOT`` so the backend it boots is the
code you're sitting in, not whatever else is on PATH.

Packaged-app launching (installed .app/.exe) arrives with the packaging
stage; this entry covers the source-checkout path the TUI's ``clawcodex``
command already serves.

Usage::

    clawcodex desktop [--install] [--no-dev]

--install   run `npm ci` even if node_modules exists
--no-dev    build once and launch electron directly (`npm run start`)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    """The clawcodex checkout this module runs from."""
    return Path(__file__).resolve().parents[2]


def desktop_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "ui-desktop"


def launch_env(root: Path) -> dict[str, str]:
    """Environment for the app process: pin the backend to this checkout."""
    env = dict(os.environ)
    env.setdefault("CLAWCODEX_DESKTOP_CLAWCODEX_ROOT", str(root))
    return env


def build_launch_plan(app_dir: Path, *, install: bool, dev: bool) -> list[list[str]]:
    """The npm commands to run, in order. Pure for testing."""
    plan: list[list[str]] = []
    if install or not (app_dir / "node_modules").is_dir():
        plan.append(["npm", "ci"])
    plan.append(["npm", "run", "dev"] if dev else ["npm", "run", "start"])
    return plan


# The dev renderer binds this fixed port (ui-desktop package.json dev:renderer).
DEV_RENDERER_PORT = 5174


def dev_port_busy(port: int | None = None, host: str = "127.0.0.1") -> bool:
    """True when something already listens on the dev renderer port.

    A second `clawcodex desktop` would otherwise die mid-boot on vite's raw
    "Port 5174 is already in use" stack trace — the app is almost certainly
    just already running. The port resolves at call time so tests (and a
    future config override) can repoint DEV_RENDERER_PORT.
    """
    import socket

    if port is None:
        port = DEV_RENDERER_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def run_desktop_subcommand(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="clawcodex desktop",
        description="Launch the ClawCodex Desktop app from this checkout.",
    )
    parser.add_argument("--install", action="store_true",
                        help="Reinstall ui-desktop npm deps first (npm ci).")
    parser.add_argument("--no-dev", action="store_true", dest="no_dev",
                        help="Build once and launch Electron directly instead "
                             "of the dev server.")
    args = parser.parse_args(argv)

    root = repo_root()
    app_dir = desktop_dir(root)
    if not (app_dir / "package.json").is_file():
        print(f"desktop: no ui-desktop app at {app_dir} — run from a full "
              "clawcodex checkout (git pull to get the desktop app).",
              file=sys.stderr)
        return 2
    if shutil.which("npm") is None:
        print("desktop: npm not found on PATH — install Node.js 22+ first.",
              file=sys.stderr)
        return 2
    if not args.no_dev and dev_port_busy():
        print("desktop: ClawCodex Desktop appears to be already running "
              f"(port {DEV_RENDERER_PORT} is in use). Switch to its window, "
              "or quit it and run this again.", file=sys.stderr)
        return 1

    env = launch_env(root)
    for cmd in build_launch_plan(app_dir, install=args.install, dev=not args.no_dev):
        print(f"desktop: {' '.join(cmd)}  (in {app_dir})", file=sys.stderr)
        try:
            result = subprocess.run(cmd, cwd=str(app_dir), env=env)
        except KeyboardInterrupt:
            return 0
        if result.returncode != 0:
            return result.returncode
    return 0


__all__ = ["build_launch_plan", "desktop_dir", "launch_env", "repo_root",
           "run_desktop_subcommand"]


if __name__ == "__main__":
    raise SystemExit(run_desktop_subcommand(sys.argv[1:]))
