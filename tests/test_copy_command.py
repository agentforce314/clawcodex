"""Tests for the ``/copy`` command (Phase 13 — port of TS local-jsx).

Headless keystone = the direct path (no code blocks / ``copyFullResponse`` / ``/copy N``
to a block-less message) — never touches ``ctx.ui``. The code-block picker needs a
surface. Clipboard + copy-dir are monkeypatched; config isolated for ``copyFullResponse``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.command_system.copy_command as cc
import src.config as cfg
from src.command_system import (
    COPY_COMMAND,
    CopyCommand,
    create_command_context,
    get_builtin_commands,
    is_bridge_safe_command,
)
from src.command_system.copy_command import (
    CodeBlock,
    _extract_code_blocks,
    _file_extension,
    _strip_prompt_xml_tags,
    _truncate_line,
    collect_recent_assistant_texts,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.safe_commands import REMOTE_SAFE_COMMANDS
from src.command_system.types import CommandType, InteractiveOutcome, NullUIHost


class FakeUIHost:
    def __init__(self, *, pick=None):
        self._pick = pick
        self.select_calls: list[dict] = []

    async def select(self, title, options, *, current=None):
        self.select_calls.append(
            {
                "title": title,
                "values": [o.value for o in options],
                "labels": [o.label for o in options],
                "descriptions": [o.description for o in options],
            }
        )
        return self._pick

    async def prompt_text(self, title, *, default="", placeholder=None):
        return None

    async def display(self, title, body):
        return None


@pytest.fixture
def copy_env(tmp_path, monkeypatch):
    """Clipboard succeeds by default; copy-dir redirected into tmp; config isolated."""
    monkeypatch.setattr(cc, "_set_clipboard", lambda text: True)
    copy_dir = tmp_path / "copydir"
    monkeypatch.setattr(cc, "_copy_dir", lambda: copy_dir)
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))
    return SimpleNamespace(tmp=tmp_path, copy_dir=copy_dir, monkeypatch=monkeypatch)


def _msg(role, content):
    return {"role": role, "content": content}


def _ctx(tmp_path, messages, *, ui=None):
    conversation = SimpleNamespace(messages=messages)
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, conversation=conversation, ui=ui
    )


# --------------------------------------------------------------------------- #
# A. Helpers
# --------------------------------------------------------------------------- #
def test_collect_recent_assistant_texts_order_skip_cap():
    messages = [_msg("user", "q1"), _msg("assistant", "a1"), _msg("system", "s")]
    messages += [_msg("assistant", f"a{i}") for i in range(2, 30)]
    texts = collect_recent_assistant_texts(messages)
    assert len(texts) == 20  # MAX_LOOKBACK cap
    assert texts[0] == "a29"  # newest first
    assert "q1" not in texts and "s" not in texts


def test_collect_joins_text_parts_and_skips_empty():
    messages = [
        _msg("assistant", [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]),
        _msg("assistant", [{"type": "tool_use", "name": "X"}]),  # tool-only -> skipped
    ]
    texts = collect_recent_assistant_texts(messages)
    assert texts == ["part1\n\npart2"]


def test_strip_prompt_xml_tags():
    s = "before\n<commit_analysis>secret</commit_analysis>\nafter"
    # The TS regex consumes the tag block AND its trailing newline (`\n?`).
    assert _strip_prompt_xml_tags(s) == "before\nafter"
    assert _strip_prompt_xml_tags("<context>a\nb</context>x").strip() == "x"


def test_extract_code_blocks():
    md = "intro\n```python\nprint(1)\nprint(2)\n```\nmid\n```\nplain\n```\n```js"
    blocks = _extract_code_blocks(md)
    assert blocks == [
        CodeBlock(code="print(1)\nprint(2)", lang="python"),
        CodeBlock(code="plain", lang=None),
    ]  # the unclosed ```js fence yields no block (documented divergence vs marked)
    assert _extract_code_blocks("no fences here") == []


def test_extract_code_blocks_lang_is_first_info_token():
    md = "```python title=x.py linenums\ncode\n```"
    assert _extract_code_blocks(md) == [CodeBlock(code="code", lang="python")]


def test_extract_code_blocks_close_fence_carries_no_info_string():
    # CommonMark: a ```js line INSIDE an open fence is content, not a close.
    md = "```md\nexample:\n```js\ncode\n```"
    assert _extract_code_blocks(md) == [
        CodeBlock(code="example:\n```js\ncode", lang="md")
    ]


async def test_copy_or_write_file_fail_branches(copy_env):
    def _boom(text, filename):
        raise OSError("disk full")

    copy_env.monkeypatch.setattr(cc, "_write_to_file", _boom)
    out = await COPY_COMMAND.run(
        "", _ctx(copy_env.tmp, [_msg("assistant", "hi")], ui=NullUIHost())
    )
    # clipboard ok + file-fail -> first line ONLY (TS catch behavior).
    assert out.message == "Copied to clipboard (2 characters, 1 lines)"

    copy_env.monkeypatch.setattr(cc, "_set_clipboard", lambda text: False)
    out = await COPY_COMMAND.run(
        "", _ctx(copy_env.tmp, [_msg("assistant", "hi")], ui=NullUIHost())
    )
    assert out.message == "Failed to copy: disk full"


@pytest.mark.parametrize(
    "lang,ext",
    [
        ("python", ".python"),
        ("tsx", ".tsx"),
        ("../../etc/passwd", ".etcpasswd"),  # traversal sanitized
        ("plaintext", ".txt"),
        (None, ".txt"),
        ("!!!", ".txt"),
    ],
)
def test_file_extension(lang, ext):
    assert _file_extension(lang) == ext


def test_truncate_line():
    assert _truncate_line("short", 60) == "short"
    assert _truncate_line("x" * 80, 60) == "x" * 59 + "…"
    assert _truncate_line("first\nsecond", 60) == "first"


# --------------------------------------------------------------------------- #
# B. Direct path (no code blocks) — the headless keystone
# --------------------------------------------------------------------------- #
async def test_direct_copy_clipboard_ok(copy_env):
    out = await COPY_COMMAND.run(
        "", _ctx(copy_env.tmp, [_msg("assistant", "hello world")], ui=NullUIHost())
    )
    assert isinstance(out, InteractiveOutcome)
    path = copy_env.copy_dir / "response.md"
    assert out.message == (
        f"Copied to clipboard (11 characters, 1 lines)\nAlso written to {path}"
    )
    assert out.display == "user"
    assert path.read_text() == "hello world"


async def test_direct_copy_clipboard_fail_is_honest(copy_env):
    copy_env.monkeypatch.setattr(cc, "_set_clipboard", lambda text: False)
    out = await COPY_COMMAND.run(
        "", _ctx(copy_env.tmp, [_msg("assistant", "hi")], ui=NullUIHost())
    )
    path = copy_env.copy_dir / "response.md"
    assert out.message == f"Written to {path} (2 characters, 1 lines)"


# --------------------------------------------------------------------------- #
# C. /copy N
# --------------------------------------------------------------------------- #
async def test_copy_n_picks_nth_latest(copy_env):
    msgs = [_msg("assistant", "older"), _msg("assistant", "newest")]
    out = await COPY_COMMAND.run("2", _ctx(copy_env.tmp, msgs, ui=NullUIHost()))
    assert out.message.startswith("Copied to clipboard")
    assert (copy_env.copy_dir / "response.md").read_text() == "older"


@pytest.mark.parametrize("bad", ["x", "0", "-1", "1.5"])
async def test_copy_n_invalid(copy_env, bad):
    out = await COPY_COMMAND.run(
        bad, _ctx(copy_env.tmp, [_msg("assistant", "a")], ui=NullUIHost())
    )
    assert out.message == (
        f"Usage: /copy [N] where N is 1 (latest), 2, 3, … Got: {bad}"
    )


async def test_copy_n_too_large_singular(copy_env):
    out = await COPY_COMMAND.run(
        "5", _ctx(copy_env.tmp, [_msg("assistant", "a")], ui=NullUIHost())
    )
    assert out.message == "Only 1 assistant message available to copy"


async def test_copy_n_too_large_plural(copy_env):
    msgs = [_msg("assistant", "a"), _msg("assistant", "b")]
    out = await COPY_COMMAND.run("5", _ctx(copy_env.tmp, msgs, ui=NullUIHost()))
    assert out.message == "Only 2 assistant messages available to copy"


async def test_no_assistant_message(copy_env):
    out = await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("user", "q")], ui=NullUIHost()))
    assert out.message == "No assistant message to copy"
    assert out.display == "user"


# --------------------------------------------------------------------------- #
# D. copyFullResponse skips the picker
# --------------------------------------------------------------------------- #
_BLOCKY = "text\n```python\ncode_here\n```\ntail"


async def test_copy_full_response_pref_skips_picker(copy_env):
    cfg._get_default_manager().set_global("copyFullResponse", True)
    ui = FakeUIHost(pick="full")
    out = await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("assistant", _BLOCKY)], ui=ui))
    assert ui.select_calls == []  # no picker
    assert out.message.startswith("Copied to clipboard")
    assert (copy_env.copy_dir / "response.md").read_text() == _BLOCKY


# --------------------------------------------------------------------------- #
# E. Picker
# --------------------------------------------------------------------------- #
async def test_picker_options_shape(copy_env):
    ui = FakeUIHost(pick="full")
    await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("assistant", _BLOCKY)], ui=ui))
    call = ui.select_calls[0]
    assert call["values"] == ["full", "0", "always"]
    assert call["labels"][0] == "Full response"
    # Full desc format: "{chars} chars, {lines} lines" over the whole message text.
    assert call["descriptions"][0] == f"{len(_BLOCKY)} chars, {_BLOCKY.count(chr(10)) + 1} lines"
    assert call["labels"][1] == "code_here"
    assert call["descriptions"][1] == "python"  # 1-line block: no "N lines" part
    assert call["labels"][2] == "Always copy full response"
    assert call["descriptions"][2] == "Skip this picker in the future (revert via /config)"


async def test_picker_block_description_matrix(copy_env):
    # All four TS desc shapes: lang+multi, no-lang+multi, lang+1-line, no-lang+1-line (None).
    md = (
        "```python\na\nb\nc\n```\n"  # python, 3 lines
        "```\nx\ny\nz\n```\n"  # 3 lines
        "```js\none\n```\n"  # js
        "```\nlone\n```"  # -> description None (TS `|| undefined`)
    )
    ui = FakeUIHost(pick="full")
    await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("assistant", md)], ui=ui))
    descs = ui.select_calls[0]["descriptions"][1:-1]  # block options only
    assert descs == ["python, 3 lines", "3 lines", "js", None]


async def test_picker_block_copy(copy_env):
    ui = FakeUIHost(pick="0")
    out = await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("assistant", _BLOCKY)], ui=ui))
    assert out.message.startswith("Copied to clipboard")
    assert (copy_env.copy_dir / "copy.python").read_text() == "code_here"


async def test_picker_always_persists_pref(copy_env):
    ui = FakeUIHost(pick="always")
    out = await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("assistant", _BLOCKY)], ui=ui))
    assert out.message.endswith(
        "\nPreference saved. Use /config to change copyFullResponse"
    )
    assert cfg.ConfigManager(cwd=copy_env.tmp).get("copyFullResponse") is True


async def test_picker_cancel(copy_env):
    ui = FakeUIHost(pick=None)
    out = await COPY_COMMAND.run("", _ctx(copy_env.tmp, [_msg("assistant", _BLOCKY)], ui=ui))
    assert out.message == "Copy cancelled"
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# F. Engine end-to-end
# --------------------------------------------------------------------------- #
async def test_engine_picker_errors_on_null_surface(copy_env):
    reg = CommandRegistry()
    reg.register(COPY_COMMAND)
    ctx = _ctx(copy_env.tmp, [_msg("assistant", _BLOCKY)])  # ui=None -> NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=copy_env.tmp, context=ctx)
    result = await eng.execute("/copy")
    assert result.success is False
    assert "interactive surface" in result.error


async def test_engine_direct_path_succeeds_headless(copy_env):
    reg = CommandRegistry()
    reg.register(COPY_COMMAND)
    ctx = _ctx(copy_env.tmp, [_msg("assistant", "plain text")])
    eng = CommandEngine(registry=reg, workspace_root=copy_env.tmp, context=ctx)
    result = await eng.execute("/copy")
    assert result.success is True
    assert result.text.startswith("Copied to clipboard")


# --------------------------------------------------------------------------- #
# G. Registration + metadata + safety + dispatch
# --------------------------------------------------------------------------- #
def test_registered_and_metadata():
    assert "copy" in {c.name for c in get_builtin_commands()}
    assert isinstance(COPY_COMMAND, CopyCommand)
    assert COPY_COMMAND.description == (
        "Copy Claude's last response to clipboard (or /copy N for the Nth-latest)"
    )
    assert COPY_COMMAND.command_type == CommandType.INTERACTIVE


def test_safety_and_dispatch():
    assert is_bridge_safe_command(COPY_COMMAND) is False  # INTERACTIVE by type
    assert "copy" in REMOTE_SAFE_COMMANDS  # the orthogonal name-based remote filter
