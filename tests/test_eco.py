"""Unit tests for the /eco compression module (src/eco/).

Fixture texts are realistic captures of the tools' native output shapes
(pytest, cargo, go, jest, pip, git, log streams) — the RTK discipline: test
against real output, assert both content preservation AND a savings floor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.eco.engine import compress_bash_output
from src.eco.filters import (
    FilterHit,
    filter_large_success,
    filter_log_dedup,
    filter_noise_strip,
    filter_test_runner,
    normalize_carriage_returns,
    strip_ansi,
)
from src.eco.guard import estimate_tokens, never_worse
from src.eco.state import (
    eco_stats,
    is_eco_session,
    record_compression,
    reset_eco,
    set_eco_session,
)
from src.eco.tee import (
    MAX_TEE_FILES,
    full_hint,
    sanitize_slug,
    tail_hint,
    tee_raw,
)


@pytest.fixture(autouse=True)
def _fresh_eco_state():
    reset_eco()
    yield
    reset_eco()


def _savings_pct(raw: str, filtered: str) -> float:
    return 100.0 * (1 - estimate_tokens(filtered) / max(1, estimate_tokens(raw)))


# ── guard ────────────────────────────────────────────────────────────────────


def test_estimate_tokens_chars_over_four_ceil():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_never_worse_prefers_smaller_and_keeps_ties():
    raw = "x" * 400
    assert never_worse(raw, "ok") == "ok"
    assert never_worse("{}", "{\n  'pretty': true\n}") == "{}"
    assert never_worse("abcd", "wxyz") == "wxyz"  # tie keeps filtered


# ── state ────────────────────────────────────────────────────────────────────


def test_state_toggle_and_stats():
    assert not is_eco_session()
    set_eco_session(True)
    assert is_eco_session()
    record_compression("pytest", 1000, 100)
    record_compression("pytest", 500, 50)
    stats = eco_stats()
    assert stats.commands == 2
    assert stats.saved_tokens == 1350
    assert stats.by_filter["pytest"] == (2, 1350)
    assert 85.0 < stats.savings_pct < 95.0
    reset_eco()
    assert not is_eco_session()
    assert eco_stats().commands == 0


# ── tee ──────────────────────────────────────────────────────────────────────


def test_tee_writes_and_hints(tmp_path: Path):
    content = "line\n" * 200  # > MIN_TEE_SIZE
    path = tee_raw(content, "cargo test --all", tmp_path)
    assert path is not None and path.exists()
    assert path.suffix == ".log"
    assert "cargo_test" in path.name
    assert full_hint(path).startswith("[full output: ")
    assert tail_hint(path, 61).startswith("[see remaining: tail -n +61 ")


def test_tee_skips_tiny_content(tmp_path: Path):
    assert tee_raw("short", "cmd", tmp_path) is None


def test_tee_same_second_same_slug_never_collides(tmp_path: Path):
    """Critic B1: two identical commands in the same instant must get
    distinct files — a clobbered tee would make the first result's already-
    emitted recovery hint point at the WRONG command's output."""
    content_a = "AAAA\n" * 200
    content_b = "BBBB\n" * 200
    p1 = tee_raw(content_a, "pytest -q tests/", tmp_path)
    p2 = tee_raw(content_b, "pytest -q tests/", tmp_path)
    assert p1 is not None and p2 is not None
    assert p1 != p2
    assert "AAAA" in p1.read_text(encoding="utf-8")
    assert "BBBB" in p2.read_text(encoding="utf-8")


def test_tee_rotation_keeps_newest(tmp_path: Path):
    for i in range(MAX_TEE_FILES + 5):
        (tmp_path / f"{1000 + i:010d}_old.log").write_text("x" * 600)
    tee_raw("y" * 600, "new", tmp_path)
    remaining = list(tmp_path.glob("*.log"))
    assert len(remaining) == MAX_TEE_FILES
    # The oldest files were removed.
    assert not (tmp_path / f"{1000:010d}_old.log").exists()


def test_tee_truncates_at_utf8_boundary(tmp_path: Path):
    big = "😀" * 300_000  # 1.2 MB of 4-byte chars
    path = tee_raw(big, "emoji", tmp_path)
    assert path is not None
    text = path.read_text(encoding="utf-8")  # must not raise
    assert "--- truncated at" in text


def test_sanitize_slug():
    assert sanitize_slug("go test ./...") == "go_test______"
    assert len(sanitize_slug("a" * 100)) == 40
    assert sanitize_slug("") == "cmd"


# ── filters: test_runner ─────────────────────────────────────────────────────

PYTEST_FAIL = """============================= test session starts ==============================
platform darwin -- Python 3.11.6, pytest-8.0.0, pluggy-1.4.0
rootdir: /work/proj
collected 120 items

tests/test_auth.py ........................                              [ 20%]
tests/test_core.py ..........F...........................                [ 52%]
tests/test_util.py ......................................................[100%]

=================================== FAILURES ===================================
_________________________________ test_refresh _________________________________

    def test_refresh():
        token = make_token()
>       assert refresh(token) is not None
E       AssertionError: assert None is not None

tests/test_core.py:88: AssertionError
=========================== short test summary info ============================
FAILED tests/test_core.py::test_refresh - AssertionError: assert None is not...
==================== 1 failed, 119 passed in 4.32s ====================
"""

PYTEST_PASS = """============================= test session starts ==============================
collected 250 items

tests/test_a.py ................................................         [ 40%]
tests/test_b.py ................................................         [ 80%]
tests/test_c.py ..........................                               [100%]

============================= 250 passed in 12.50s =============================
"""


def test_pytest_failure_focus_keeps_failures_and_saves():
    hit = filter_test_runner("pytest", 1, PYTEST_FAIL)
    assert hit is not None and hit.name == "pytest"
    assert "1 failed, 119 passed" in hit.body
    assert "test_refresh" in hit.body
    assert "AssertionError" in hit.body
    assert "tests/test_core.py:88" in hit.body
    # Passing progress lines are gone.
    assert "test_auth.py" not in hit.body
    assert _savings_pct(PYTEST_FAIL, hit.body) >= 60.0


def test_pytest_all_pass_one_liner():
    hit = filter_test_runner("pytest", 0, PYTEST_PASS)
    assert hit is not None
    assert hit.body == "Pytest: 250 passed in 12.50s"
    assert _savings_pct(PYTEST_PASS, hit.body) >= 60.0


def test_pytest_quiet_mode_summary_without_wrapper():
    text = "..........\n10 passed in 0.10s\n"
    hit = filter_test_runner("pytest -q", 0, text)
    assert hit is not None
    assert "10 passed" in hit.body


def test_test_runner_refuses_without_summary():
    assert filter_test_runner("pytest", 1, "Traceback...\nboom\n") is None


def test_green_summary_with_failing_exit_passes_through():
    """Critic M3: a green summary + non-zero exit means something else in the
    output failed — never mask it behind an all-green one-liner."""
    assert filter_test_runner("pytest && cargo build", 1, PYTEST_PASS) is None
    cargo_green = (
        "running 3 tests\ntest a ... ok\n\n"
        "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
    )
    assert filter_test_runner("cargo test", 101, cargo_green) is None
    assert filter_test_runner("go test ./...", 1, "ok  \texample.com/a\t0.1s\n") is None
    jest_green = "Tests:       5 passed, 5 total\n"
    assert filter_test_runner("npx jest", 1, jest_green) is None


CARGO_FAIL = """   Compiling proj v0.1.0 (/work/proj)
    Finished test [unoptimized + debuginfo] target(s) in 2.31s
     Running unittests src/lib.rs

running 42 tests
test util::tests::test_parse ... ok
test util::tests::test_format ... ok
test core::tests::test_overflow ... FAILED

failures:

---- core::tests::test_overflow stdout ----
thread 'core::tests::test_overflow' panicked at src/core.rs:99:5:
assertion `left == right` failed
  left: 4
 right: 5

failures:
    core::tests::test_overflow

test result: FAILED. 41 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.02s
"""


def test_cargo_test_failure_focus():
    hit = filter_test_runner("cargo test", 101, CARGO_FAIL)
    assert hit is not None and hit.name == "cargo-test"
    assert "41 passed, 1 failed" in hit.body
    assert "core::tests::test_overflow" in hit.body
    assert "panicked at src/core.rs:99:5" in hit.body
    assert "test_parse" not in hit.body  # passing tests dropped
    assert _savings_pct(CARGO_FAIL, hit.body) >= 50.0


def test_cargo_test_pass_one_liner():
    text = "running 10 tests\n" + "test a ... ok\n" * 10 + (
        "\ntest result: ok. 10 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
    )
    hit = filter_test_runner("cargo test", 0, text)
    assert hit is not None
    assert hit.body == "cargo test: ok — 10 passed"


GO_FAIL = """=== RUN   TestParse
--- FAIL: TestParse (0.00s)
    parse_test.go:21: expected 5, got 4
=== RUN   TestFormat
--- PASS: TestFormat (0.00s)
FAIL
FAIL\texample.com/pkg/util\t0.012s
ok  \texample.com/pkg/core\t0.031s
"""


def test_go_test_failure_focus():
    hit = filter_test_runner("go test ./...", 1, GO_FAIL)
    assert hit is not None and hit.name == "go-test"
    assert "1 package(s) failed" in hit.body
    assert "TestParse" in hit.body
    assert "expected 5, got 4" in hit.body
    assert "TestFormat" not in hit.body


def test_go_test_all_ok():
    text = "ok  \texample.com/a\t0.1s\nok  \texample.com/b\t0.2s\n"
    hit = filter_test_runner("go test ./...", 0, text)
    assert hit is not None
    assert hit.body == "go test: ok — 2 packages"


JEST_FAIL = """ FAIL  src/auth.test.ts
  auth
    ✓ signs in (12 ms)
    ✕ refreshes token (5 ms)

  ● auth › refreshes token

    expect(received).toBe(expected)

    Expected: "ok"
    Received: undefined

      at Object.<anonymous> (src/auth.test.ts:30:25)

 PASS  src/util.test.ts

Tests:       1 failed, 41 passed, 42 total
Snapshots:   0 total
Time:        3.214 s
"""


def test_jest_failure_focus():
    hit = filter_test_runner("npx jest", 1, JEST_FAIL)
    assert hit is not None and hit.name == "jest"
    assert "1 failed, 41 passed" in hit.body
    assert "refreshes token" in hit.body
    assert "Expected" in hit.body


# ── filters: noise_strip ─────────────────────────────────────────────────────

PIP_NOISY = """Collecting requests
  Downloading requests-2.31.0-py3-none-any.whl (62 kB)
Collecting idna<4,>=2.5
  Using cached idna-3.6-py3-none-any.whl (61 kB)
Requirement already satisfied: certifi in ./venv/lib (2024.2.2)
Installing collected packages: idna, requests
Successfully installed idna-3.6 requests-2.31.0
"""

GIT_PUSH_NOISY = """Enumerating objects: 12, done.
Counting objects: 100% (12/12), done.
Compressing objects: 100% (6/6), done.
Writing objects: 100% (7/7), 1.02 KiB | 1.02 MiB/s, done.
remote: Resolving deltas: 100% (4/4), completed with 4 local objects.
To github.com:me/proj.git
   abc1234..def5678  main -> main
"""


def test_noise_strip_pip():
    hit = filter_noise_strip("pip install requests", 0, PIP_NOISY)
    assert hit is not None and hit.safe_loss
    assert "Successfully installed idna-3.6 requests-2.31.0" in hit.body
    assert "Installing collected packages" in hit.body  # informative, kept
    assert "Downloading" not in hit.body
    assert "Requirement already satisfied" not in hit.body


def test_noise_strip_git_push_keeps_result():
    hit = filter_noise_strip("git push", 0, GIT_PUSH_NOISY)
    assert hit is not None
    assert "main -> main" in hit.body
    assert "To github.com:me/proj.git" in hit.body
    assert "Enumerating objects" not in hit.body
    assert "Resolving deltas" not in hit.body


def test_noise_strip_git_status_advice_lines():
    text = (
        "On branch main\n"
        "Changes not staged for commit:\n"
        '  (use "git add <file>..." to update what will be committed)\n'
        '  (use "git restore <file>..." to discard changes)\n'
        "\tmodified:   src/app.py\n"
    )
    hit = filter_noise_strip("git status", 0, text)
    assert hit is not None
    assert "modified:   src/app.py" in hit.body
    assert "use \"git add" not in hit.body


def test_noise_strip_none_when_clean():
    assert filter_noise_strip("echo hi", 0, "hi\n") is None


def test_noise_strip_scoped_to_command_family():
    """Critic M2: family patterns must NOT fire for unrelated commands — a
    script legitimately printing 'Downloading x' keeps its line."""
    text = "Downloading dataset shard 3\nCollecting metrics: done\n"
    assert filter_noise_strip("./run_pipeline.sh", 0, text) is None
    assert filter_noise_strip("python train.py", 0, text) is None
    # The same lines ARE ceremony when pip actually ran.
    hit = filter_noise_strip("pip install foo", 0, text)
    assert hit is not None and "Downloading" not in hit.body


def test_noise_strip_preserves_git_state_lines():
    """Fidelity rescue (RTK doc 05): in-progress state must survive."""
    text = (
        "interactive rebase in progress; onto abc1234\n"
        "You are currently rebasing branch 'main' on 'abc1234'.\n"
        '  (use "git commit --amend" to amend the current commit)\n'
        "Unmerged paths:\n"
        "\tboth modified:   src/app.py\n"
        "HEAD detached at abc1234\n"
    )
    hit = filter_noise_strip("git status", 0, text)
    assert hit is not None
    assert "rebase in progress" in hit.body
    assert "You are currently rebasing" in hit.body
    assert "Unmerged paths:" in hit.body
    assert "both modified:   src/app.py" in hit.body
    assert "HEAD detached at abc1234" in hit.body
    assert "git commit --amend" not in hit.body  # only the advice line drops


def test_noise_strip_preserves_errors():
    text = "Collecting bad-pkg\nERROR: No matching distribution found for bad-pkg\n"
    hit = filter_noise_strip("pip install bad-pkg", 1, text)
    assert hit is not None
    assert "ERROR: No matching distribution found" in hit.body


def test_noise_strip_npm_lowercase_warn():
    """npm >=9 lowercased its log prefixes ("npm warn deprecated ...");
    deprecation spam is ceremony, but `npm error` lines must survive."""
    text = (
        "added 294 packages in 6s\n"
        "npm warn deprecated inflight@1.0.6: This module is not supported\n"
        "npm WARN deprecated abab@2.0.6: Use your platform's native atob()\n"
        "npm warn deprecated glob@7.2.3: Old versions of glob\n"
        "npm error code ELIFECYCLE\n"
    )
    hit = filter_noise_strip("npm install --no-fund jest", 0, text)
    assert hit is not None and hit.safe_loss
    assert "added 294 packages" in hit.body
    assert "deprecated" not in hit.body  # both npm<9 WARN and npm>=9 warn drop
    assert "npm error code ELIFECYCLE" in hit.body


# ── filters: log_dedup ───────────────────────────────────────────────────────


def test_log_dedup_counts_repeats():
    lines = []
    for i in range(120):
        lines.append(f"2024-01-01 10:00:{i % 60:02d} ERROR Connection failed to /api/server port 8080")
    for i in range(30):
        lines.append(f"2024-01-01 10:01:{i % 60:02d} INFO heartbeat ok seq {i}")
    text = "\n".join(lines)
    hit = filter_log_dedup("docker logs app", 0, text)
    assert hit is not None and hit.name == "log-dedup"
    assert "[×120]" in hit.body
    assert "Connection failed" in hit.body
    assert _savings_pct(text, hit.body) >= 80.0


def test_log_dedup_ignores_short_output():
    text = "\n".join("2024-01-01 10:00:00 ERROR x" for _ in range(10))
    assert filter_log_dedup("cmd", 0, text) is None


def test_log_dedup_ignores_non_loggy_output():
    text = "\n".join(f"file_{i}.txt" for i in range(200))
    assert filter_log_dedup("ls", 0, text) is None


# ── filters: large_success_cap ───────────────────────────────────────────────


def test_large_success_caps_with_tail_offset():
    text = "\n".join(f"item {i}" for i in range(1000))
    hit = filter_large_success("find .", 0, text)
    assert hit is not None and hit.name == "head-cap"
    assert hit.tail_offset == 61
    assert "item 0" in hit.body and "item 59" in hit.body
    assert "item 60" not in hit.body.split("...")[0]
    assert "+940 more lines" in hit.body


def test_large_success_skips_failures_and_small_output():
    text = "\n".join(f"item {i}" for i in range(1000))
    assert filter_large_success("find .", 2, text) is None
    assert filter_large_success("ls", 0, "a\nb\nc") is None


# ── helpers ──────────────────────────────────────────────────────────────────


def test_strip_ansi_and_carriage_returns():
    assert strip_ansi("\x1b[31mred\x1b[0m plain") == "red plain"
    assert normalize_carriage_returns("progress 10%\rprogress 99%\rdone\nnext") == "done\nnext"


# ── engine ───────────────────────────────────────────────────────────────────


def test_engine_passthrough_on_plain_output(tmp_path: Path):
    out = compress_bash_output("echo hi", 0, "hi", "hi", tmp_path)
    assert out is None
    assert eco_stats().commands == 0


def test_engine_head_cap_requires_tee(tmp_path: Path):
    text = "\n".join(f"line {i}" for i in range(1000))
    # With a tee dir: hit + runnable hint.
    out = compress_bash_output("find .", 0, text, text, tmp_path)
    assert out is not None
    assert "[see remaining: tail -n +61 " in out.content
    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 1
    # The teed file contains the full processed text (offsets line up).
    assert logs[0].read_text(encoding="utf-8").splitlines()[60] == "line 60"
    # Without a tee dir: the lossy hit must be discarded.
    reset_eco()
    assert compress_bash_output("find .", 0, text, text, None) is None
    assert eco_stats().commands == 0


def test_engine_safe_loss_needs_no_tee():
    out = compress_bash_output(
        "pip install requests", 0, PIP_NOISY, PIP_NOISY, None
    )
    assert out is not None
    assert out.filter_name == "noise-strip"
    assert "[full output:" not in out.content


def test_engine_records_stats():
    compress_bash_output("pip install requests", 0, PIP_NOISY, PIP_NOISY, None)
    stats = eco_stats()
    assert stats.commands == 1
    assert stats.saved_tokens > 0
    assert "noise-strip" in stats.by_filter


def test_engine_never_worse_discards_bloaty_filter(monkeypatch, tmp_path: Path):
    import src.eco.engine as engine_mod

    def bloaty(command, exit_code, text):
        return FilterHit(name="bloat", body=text + "x" * 500, safe_loss=True)

    monkeypatch.setattr(engine_mod, "FILTERS", (bloaty,))
    assert compress_bash_output("cmd", 0, "small", "small", tmp_path) is None


def test_engine_guard_reject_leaves_no_orphan_tee(monkeypatch, tmp_path: Path):
    """A lossy hit the guard rejects must not leave an unreferenced file."""
    import src.eco.engine as engine_mod

    text = "x" * 2000  # big enough to tee

    def barely_smaller(command, exit_code, t):
        # Body smaller than baseline but not by enough to cover the hint.
        return FilterHit(name="tight", body=t[:-8], safe_loss=False)

    monkeypatch.setattr(engine_mod, "FILTERS", (barely_smaller,))
    assert compress_bash_output("cmd", 0, text, text, tmp_path) is None
    assert list(tmp_path.glob("*.log")) == []


def test_engine_skips_giant_input(monkeypatch, tmp_path: Path):
    import src.eco.engine as engine_mod

    called = []

    def spy(command, exit_code, text):
        called.append(1)
        return None

    monkeypatch.setattr(engine_mod, "FILTERS", (spy,))
    giant = "x" * (engine_mod._MAX_INPUT_CHARS + 1)
    assert compress_bash_output("cmd", 0, giant, "small", tmp_path) is None
    assert called == []


def test_engine_skips_empty_filter_output(monkeypatch, tmp_path: Path):
    import src.eco.engine as engine_mod

    def empty(command, exit_code, text):
        return FilterHit(name="empty", body="   ", safe_loss=True)

    monkeypatch.setattr(engine_mod, "FILTERS", (empty,))
    assert compress_bash_output("cmd", 0, "content", "content", tmp_path) is None


def test_engine_filter_exception_falls_through(monkeypatch, tmp_path: Path):
    import src.eco.engine as engine_mod

    def boom(command, exit_code, text):
        raise RuntimeError("bad filter")

    monkeypatch.setattr(
        engine_mod, "FILTERS", (boom, engine_mod.FILTERS[2])
    )  # noise_strip after the broken one
    out = compress_bash_output(
        "pip install requests", 0, PIP_NOISY, PIP_NOISY, None
    )
    assert out is not None and out.filter_name == "noise-strip"


def test_engine_strips_ansi_before_filtering(tmp_path: Path):
    noisy = "\x1b[32mCollecting requests\x1b[0m\nSuccessfully installed requests\n"
    out = compress_bash_output("pip install requests", 0, noisy, noisy, None)
    assert out is not None
    assert out.content == "Successfully installed requests"


# ── /eco command ─────────────────────────────────────────────────────────────


def _ctx(tmp_path: Path):
    from src.command_system.types import CommandContext

    return CommandContext(
        workspace_root=tmp_path,
        cwd=tmp_path,
        conversation=None,
        cost_tracker=None,
        history=None,
    )


def test_eco_command_toggle_and_status(tmp_path: Path):
    from src.command_system.eco_command import eco_command_call

    ctx = _ctx(tmp_path)
    r = eco_command_call("", ctx)
    assert "Eco mode on" in r.value
    assert is_eco_session()
    r = eco_command_call("status", ctx)
    assert "Eco mode: on" in r.value
    r = eco_command_call("", ctx)
    assert "Eco mode off" in r.value
    assert not is_eco_session()
    r = eco_command_call("on", ctx)
    assert is_eco_session()
    r = eco_command_call("off", ctx)
    assert not is_eco_session()
    r = eco_command_call("bogus", ctx)
    assert "Unknown argument" in r.value
    assert "Usage: /eco" in r.value


def test_eco_command_status_reports_stats(tmp_path: Path):
    from src.command_system.eco_command import eco_command_call

    record_compression("pytest", 2000, 200)
    r = eco_command_call("status", _ctx(tmp_path))
    assert "Compressed 1 command output(s)" in r.value
    assert "pytest" in r.value
    assert "90%" in r.value


def test_eco_command_registered():
    from src.command_system.builtins import get_builtin_commands

    names = [c.name for c in get_builtin_commands()]
    assert "eco" in names
