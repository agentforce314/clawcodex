"""UTILS-2 — subprocess env secret-scrub (port of utils/subprocessEnv.ts).

Anti-exfiltration: when CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is truthy, secret env
vars (+ their INPUT_ GitHub-Action twins) are stripped from a child process's
environment so a prompt-injected Bash command can't read them via ${VAR}.
Wired at the bash (fg+bg) + hook subprocess sites.
"""
from __future__ import annotations

import os

import pytest

from src.utils.subprocess_env import subprocess_env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "ANTHROPIC_API_KEY",
              "INPUT_ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "SSH_SIGNING_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_flag_off_passes_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sekret")
    e = subprocess_env()
    assert e["ANTHROPIC_API_KEY"] == "sekret"  # untouched when flag off


def test_flag_on_scrubs_secrets_and_input_twins(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "s")
    monkeypatch.setenv("INPUT_ANTHROPIC_API_KEY", "twin")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "a")
    monkeypatch.setenv("SSH_SIGNING_KEY", "k")
    e = subprocess_env()
    for gone in ("ANTHROPIC_API_KEY", "INPUT_ANTHROPIC_API_KEY",
                 "AWS_SECRET_ACCESS_KEY", "SSH_SIGNING_KEY"):
        assert gone not in e
    assert "PATH" in e  # non-secret retained


def test_truthy_variants(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "s")
    for v, scrubbed in [("true", True), ("yes", True), ("on", True),
                        ("0", False), ("false", False), ("", False)]:
        monkeypatch.setenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", v)
        assert ("ANTHROPIC_API_KEY" not in subprocess_env()) is scrubbed


def test_returns_fresh_dict_not_os_environ(monkeypatch):
    e = subprocess_env()
    e["_MUT"] = "x"
    assert "_MUT" not in os.environ


def test_explicit_base(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1")
    e = subprocess_env({"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "ANTHROPIC_API_KEY": "x", "KEEP": "y"})
    assert "ANTHROPIC_API_KEY" not in e and e["KEEP"] == "y"


def test_all_23_scrub_vars_stripped(monkeypatch):
    from src.utils.subprocess_env import _GHA_SUBPROCESS_SCRUB
    assert len(_GHA_SUBPROCESS_SCRUB) == 23
    monkeypatch.setenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1")
    for v in _GHA_SUBPROCESS_SCRUB:
        monkeypatch.setenv(v, "secret")
        monkeypatch.setenv(f"INPUT_{v}", "twin")
    e = subprocess_env()
    for v in _GHA_SUBPROCESS_SCRUB:
        assert v not in e and f"INPUT_{v}" not in e, v
    # the flag itself is preserved for the child (TS keeps it)
    assert e.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") == "1"
