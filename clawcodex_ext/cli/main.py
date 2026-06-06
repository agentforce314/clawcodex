"""Downstream CLI entrypoint."""

from __future__ import annotations


def main():
    """Delegate to the downstream CLI dispatch."""
    from clawcodex_ext.cli.dispatch import run_cli
    return run_cli()