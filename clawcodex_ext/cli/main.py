"""Downstream CLI entrypoint."""

from __future__ import annotations


def main():
    """Delegate to the compatibility CLI entrypoint."""

    from src.cli import main as cli_main

    return cli_main()
