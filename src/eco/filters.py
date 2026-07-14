"""Pure output filters for eco compression (ported from RTK's method set).

Every filter is a pure function ``(command, exit_code, text) -> FilterHit | None``
over the already-assembled model-bound text. ``None`` means "not my shape" —
the engine tries the next filter and ultimately passes through. Filters follow
RTK's fidelity rules (my-docs/token-compression/RTK/05-safety-and-fidelity.md):

* drop or count lines — never rewrite kept-line content;
* error/failure lines always survive;
* parse uncertainty → ``None`` (a test filter that can't find a summary line
  refuses rather than guessing);
* two loss classes, mirroring RTK strip-vs-truncate: ``safe_loss`` drops are
  pure ceremony (progress bars, spinner frames, advice lines) and may ship
  without recovery; everything else requires the engine to tee the raw output
  and append a recovery hint, or be discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── caps (RTK core/truncate.rs classes) ─────────────────────────────────────
CAP_FAILURES = 10          # failure blocks shown per test run
FAILURE_DETAIL_LINES = 5   # relevant lines kept inside one failure block
CAP_ERRORS = 20            # error lines in log summaries
CAP_WARNINGS = 10
LARGE_OUTPUT_LINES = 400   # success outputs longer than this get head-capped
HEAD_KEEP = 60             # lines kept by the large-output cap
LOG_MIN_LINES = 80         # minimum size before log dedup considers an output
_MAX_LINE = 300            # per-line clamp inside summaries (multi-MB one-liners)


@dataclass(frozen=True)
class FilterHit:
    """A successful compression of one output."""

    name: str
    body: str
    # True when the dropped content is pure ceremony (RTK "strip" class):
    # progress/spinner/advice lines that carry zero decision value. Safe-loss
    # hits may ship without a tee file; all other hits require recovery.
    safe_loss: bool = False
    # For head-cap style hits: the 1-based line offset where hidden content
    # starts in the teed file (drives the runnable ``tail -n +N`` hint).
    tail_offset: int | None = None


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def normalize_carriage_returns(text: str) -> str:
    """Keep only the final frame of ``\\r``-animated progress lines."""
    if "\r" not in text:
        return text
    out_lines = []
    for line in text.split("\n"):
        if "\r" in line:
            line = line.split("\r")[-1]
        out_lines.append(line)
    return "\n".join(out_lines)


def _clamp(line: str) -> str:
    if len(line) <= _MAX_LINE:
        return line
    return line[: _MAX_LINE - 3] + "..."


def _collapse_blank_runs(lines: list[str]) -> list[str]:
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip() == "":
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(line)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1. test_runner — failure focus (RTK's ~90% class, doc 03 §2.5/2.6/2.7)
#
# Native-output parsers; detection is output-signature based (a summary line
# is REQUIRED — no summary, no compression), so aliases like `make test`
# compress too and non-test output is never mangled.
# ─────────────────────────────────────────────────────────────────────────────

_PYTEST_SUMMARY_RE = re.compile(
    r"^=*\s*((?:\d+ (?:passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected)"
    r"(?:, )?)+ in [\d.]+s(?: \([^)]*\))?)\s*=*$"
)
_PYTEST_SECTION_RE = re.compile(r"^=+ (FAILURES|ERRORS) =+$")
_PYTEST_SHORT_SUMMARY_RE = re.compile(r"^=+ short test summary info =+$")
_PYTEST_FAIL_HEADER_RE = re.compile(r"^_{3,}\s*(.*?)\s*_{3,}$")
_PYTEST_RELEVANT_RE = re.compile(
    r"^(>|E |E$)|assert|[Ee]rror|Exception|\.py:\d+",
)


def _filter_pytest(exit_code: int, text: str) -> FilterHit | None:
    lines = text.split("\n")
    summary_line = None
    for line in reversed(lines):
        if _PYTEST_SUMMARY_RE.match(line.strip()):
            summary_line = line.strip().strip("=").strip()
            break
    if summary_line is None:
        return None

    # All green → one line. Only when the exit code agrees (critic M3): a
    # green summary + non-zero exit means something ELSE in the output
    # failed (a chained command, a plugin crash) — parse untrusted, pass
    # through rather than mask it behind an all-green one-liner.
    if "failed" not in summary_line and "error" not in summary_line:
        if exit_code != 0:
            return None
        return FilterHit(name="pytest", body=f"Pytest: {summary_line}")

    # Collect failure blocks from the FAILURES/ERRORS section and the short
    # summary (RTK pytest_cmd.rs state machine, simplified to native output).
    failures: list[list[str]] = []
    short_summary: list[str] = []
    in_fail_section = False
    in_short = False
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _PYTEST_SECTION_RE.match(stripped):
            in_fail_section, in_short = True, False
            continue
        if _PYTEST_SHORT_SUMMARY_RE.match(stripped):
            if current:
                failures.append(current)
                current = []
            in_fail_section, in_short = False, True
            continue
        if _PYTEST_SUMMARY_RE.match(stripped):
            in_fail_section = in_short = False
            continue
        if in_short:
            if stripped.startswith(("FAILED", "ERROR", "XPASS")):
                short_summary.append(_clamp(stripped))
            continue
        if in_fail_section:
            m = _PYTEST_FAIL_HEADER_RE.match(stripped)
            if m and m.group(1):
                if current:
                    failures.append(current)
                current = [m.group(1)]
            elif current and stripped:
                current.append(line.rstrip())
    if current:
        failures.append(current)

    out: list[str] = [f"Pytest: {summary_line}"]
    shown = failures[:CAP_FAILURES]
    for i, block in enumerate(shown, 1):
        out.append("")
        out.append(f"{i}. [FAIL] {block[0]}")
        kept = 0
        for detail in block[1:]:
            if kept >= FAILURE_DETAIL_LINES:
                break
            if _PYTEST_RELEVANT_RE.search(detail.strip()):
                out.append(f"   {_clamp(detail.strip())}")
                kept += 1
    if len(failures) > CAP_FAILURES:
        out.append(f"   ... +{len(failures) - CAP_FAILURES} more failures")
    if short_summary and not failures:
        out.append("")
        out.extend(f"  {s}" for s in short_summary[:CAP_FAILURES])
        if len(short_summary) > CAP_FAILURES:
            out.append(f"  ... +{len(short_summary) - CAP_FAILURES} more")
    return FilterHit(name="pytest", body="\n".join(out))


_CARGO_RESULT_RE = re.compile(
    r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed;.*$"
)
_CARGO_FAIL_BLOCK_RE = re.compile(r"^---- (.+) ----$")


def _filter_cargo_test(exit_code: int, text: str) -> FilterHit | None:
    lines = text.split("\n")
    results = [l.strip() for l in lines if _CARGO_RESULT_RE.match(l.strip())]
    if not results:
        return None
    total_passed = sum(int(_CARGO_RESULT_RE.match(r).group(2)) for r in results)  # type: ignore[union-attr]
    total_failed = sum(int(_CARGO_RESULT_RE.match(r).group(3)) for r in results)  # type: ignore[union-attr]

    if total_failed == 0:
        if exit_code != 0:
            return None  # green summary but failing exit — parse untrusted (M3)
        n = len(results)
        suites = f" across {n} suites" if n > 1 else ""
        return FilterHit(
            name="cargo-test",
            body=f"cargo test: ok — {total_passed} passed{suites}",
        )

    out = [f"cargo test: {total_passed} passed, {total_failed} failed"]
    blocks = 0
    in_block = False
    detail = 0
    for line in lines:
        stripped = line.strip()
        m = _CARGO_FAIL_BLOCK_RE.match(stripped)
        if m:
            if blocks >= CAP_FAILURES:
                in_block = False
                continue
            blocks += 1
            detail = 0
            in_block = True
            out.append("")
            out.append(f"[FAIL] {m.group(1)}")
            continue
        if in_block:
            if not stripped or stripped.startswith("failures:"):
                in_block = False
                continue
            if detail < FAILURE_DETAIL_LINES:
                out.append(f"   {_clamp(stripped)}")
                detail += 1
    hidden = total_failed - blocks
    if hidden > 0:
        out.append(f"   ... +{hidden} more failures")
    return FilterHit(name="cargo-test", body="\n".join(out))


_GO_FAIL_TEST_RE = re.compile(r"^--- FAIL: (\S+)")
_GO_SUMMARY_OK_RE = re.compile(r"^ok\s+\S+\s+([\d.]+s|\(cached\))")
# Package-fail lines carry a duration / (cached) / [build failed] suffix —
# jest's "FAIL src/x.test.ts" has none of those, so the go parser can never
# claim jest output. go's bare trailing "FAIL" verdict line is handled
# separately (it is not a package).
_GO_SUMMARY_FAIL_RE = re.compile(
    r"^FAIL\s+\S+\s+([\d.]+s|\(cached\)|\[build failed\])$"
)


def _filter_go_test(exit_code: int, text: str) -> FilterHit | None:
    lines = text.split("\n")
    ok_pkgs = sum(1 for l in lines if _GO_SUMMARY_OK_RE.match(l.strip()))
    fail_pkgs = sum(1 for l in lines if _GO_SUMMARY_FAIL_RE.match(l.strip()))
    fail_tests = [
        m.group(1)
        for l in lines
        if (m := _GO_FAIL_TEST_RE.match(l.strip())) is not None
    ]
    # Single-package `go test` runs end with a bare FAIL/PASS verdict and no
    # per-package summary line; require the --- FAIL marker alongside it so
    # nothing else can masquerade as go output.
    has_bare_verdict = any(l.strip() in ("FAIL", "PASS", "ok") for l in lines)
    if ok_pkgs + fail_pkgs == 0 and not (fail_tests and has_bare_verdict):
        return None
    if fail_pkgs == 0 and not fail_tests:
        if exit_code != 0:
            return None  # green summary but failing exit — parse untrusted (M3)
        return FilterHit(name="go-test", body=f"go test: ok — {ok_pkgs} packages")

    failed_pkg_display = fail_pkgs if fail_pkgs else 1
    out = [
        f"go test: {failed_pkg_display} package(s) failed"
        + (f", {ok_pkgs} ok" if ok_pkgs else "")
    ]
    # Keep each failing test header + its indented output (capped).
    blocks = 0
    in_fail = False
    detail = 0
    for line in lines:
        if _GO_FAIL_TEST_RE.match(line.strip()):
            if blocks >= CAP_FAILURES:
                in_fail = False
                continue
            blocks += 1
            detail = 0
            in_fail = True
            out.append(_clamp(line.strip()))
            continue
        if in_fail:
            if line.startswith(("    ", "\t")) and detail < FAILURE_DETAIL_LINES:
                out.append(f"   {_clamp(line.strip())}")
                detail += 1
                continue
            in_fail = False
        stripped = line.strip()
        if _GO_SUMMARY_FAIL_RE.match(stripped) and len(out) < 60:
            out.append(_clamp(stripped))
    if len(fail_tests) > CAP_FAILURES:
        out.append(f"   ... +{len(fail_tests) - CAP_FAILURES} more failing tests")
    return FilterHit(name="go-test", body="\n".join(out))


_JEST_TESTS_RE = re.compile(
    r"^Tests:\s+(?:(\d+) failed, )?(?:(\d+) skipped, )?(\d+) passed, (\d+) total"
)
_JEST_FAIL_FILE_RE = re.compile(r"^FAIL\s+(\S+)")
_JEST_FAIL_CASE_RE = re.compile(r"^\s*(✕|✗|×)\s+(.*)$")


def _filter_jest(exit_code: int, text: str) -> FilterHit | None:
    lines = text.split("\n")
    summary = None
    for line in reversed(lines):
        m = _JEST_TESTS_RE.match(line.strip())
        if m:
            summary = m
            break
    if summary is None:
        return None
    failed = int(summary.group(1) or 0)
    if failed == 0:
        if exit_code != 0:
            return None  # green summary but failing exit — parse untrusted (M3)
        return FilterHit(
            name="jest",
            body=f"Tests: {summary.group(3)} passed, {summary.group(4)} total — ok",
        )
    out = [summary.group(0)]
    fail_files = [l.strip() for l in lines if _JEST_FAIL_FILE_RE.match(l.strip())]
    out.extend(_clamp(f) for f in fail_files[:CAP_FAILURES])
    cases = [m.group(2) for l in lines if (m := _JEST_FAIL_CASE_RE.match(l))]
    for c in cases[:CAP_FAILURES]:
        out.append(f"  ✕ {_clamp(c)}")
    # Keep assertion context lines (`expect(...)`, `Expected:`, `Received:`).
    ctx = [
        l.strip()
        for l in lines
        if l.strip().startswith(("expect(", "Expected", "Received", "at "))
    ]
    out.extend(f"   {_clamp(c)}" for c in ctx[: FAILURE_DETAIL_LINES * min(failed, CAP_FAILURES)])
    return FilterHit(name="jest", body="\n".join(out))


def filter_test_runner(command: str, exit_code: int, text: str) -> FilterHit | None:
    # jest before go: both use a bare "FAIL" keyword, but jest's "Tests:"
    # summary is unmistakable while go's FAIL lines are duration-suffixed.
    for parser in (_filter_pytest, _filter_cargo_test, _filter_jest, _filter_go_test):
        hit = parser(exit_code, text)
        if hit is not None:
            return hit
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. noise_strip — ceremony line removal (RTK TOML-corpus class, doc 04)
#
# This is RTK's "strip" loss class: dropped lines are pure ceremony, so hits
# are safe_loss (no tee required). To keep that promise, every pattern group
# is SCOPED to its tool family via a command matcher (critic M2 — RTK's TOML
# filters gate on match_command for the same reason): "Downloading x" from
# some unrelated script is only ceremony when the command is actually a
# package manager. A tool invoked behind `make`/a script is simply not
# compressed (RTK accepts the same under-coverage for safety). The only
# universal pattern is a line of pure spinner glyphs — ceremony in any
# context. Error/summary lines survive by construction: no pattern below can
# match them.
# ─────────────────────────────────────────────────────────────────────────────

# Progress bars: [=====>   ] 45% / 45%|████ — shared by downloader families.
_PROGRESS_BAR_PATTERNS = (
    re.compile(r"^\s*[\[|(]?[=\-#>. ]{6,}[\]|)]?\s*\d{1,3}(\.\d+)?%.*$"),
    re.compile(r"^\s*\d{1,3}(\.\d+)?%\s*[|▏▎▍▌▋▊▉█]+.*$"),
)

_NOISE_GROUPS: tuple[tuple[re.Pattern[str] | None, tuple[re.Pattern[str], ...]], ...] = (
    (
        re.compile(r"(^|[\s;&|(])git\b"),
        (
            re.compile(r"^(remote: )?(Enumerating|Counting|Compressing|Receiving|Resolving|Writing) (objects|deltas):"),
            re.compile(r"^remote: (Total|Compressing|Counting)\b"),
            # status/pull advice lines — UI guidance, meaningless to the model
            re.compile(r'^\s*\(use "git [^"]+"'),
            re.compile(r"^\s*\(fix conflicts and run"),
        ),
    ),
    (
        re.compile(r"(^|[\s;&|(])(pip3?|uv)\b|python3?(\.\d+)? -m pip\b"),
        (
            re.compile(r"^(Collecting|Downloading|Using cached|Requirement already satisfied)[ :]"),
            re.compile(r"^\s*(Preparing metadata|Installing build dependencies|Getting requirements to build)"),
            re.compile(r"^\s*Downloading\b"),
            *_PROGRESS_BAR_PATTERNS,
        ),
    ),
    (
        re.compile(r"(^|[\s;&|(])(npm|npx|yarn|pnpm|bun)\b"),
        (
            # npm >=9 lowercased its log prefixes ("npm warn deprecated ...");
            # error lines are deliberately NOT ceremony and must survive.
            re.compile(r"^npm (WARN|warn|notice)\b"),
            re.compile(r"^\s*reify:"),
            re.compile(r"^Progress: resolved \d+"),
            *_PROGRESS_BAR_PATTERNS,
        ),
    ),
    (
        re.compile(r"(^|[\s;&|(])cargo\b"),
        (
            re.compile(r"^\s*(Compiling|Downloading|Downloaded|Checking|Fresh|Updating crates.io index)\s"),
        ),
    ),
    (
        re.compile(r"(^|[\s;&|(])docker\b"),
        (
            re.compile(r"^[0-9a-f]{12}: (Pulling|Waiting|Verifying|Download complete|Pull complete|Downloading|Extracting)"),
            re.compile(r"^(Pulling from|Digest: sha256:)"),
            *_PROGRESS_BAR_PATTERNS,
        ),
    ),
    (
        re.compile(r"(^|[\s;&|(])(apt|apt-get|aptitude)\b"),
        (
            re.compile(r"^(Get:\d+|Hit:\d+|Reading package lists|Building dependency tree|Reading state information)"),
            *_PROGRESS_BAR_PATTERNS,
        ),
    ),
    (
        re.compile(r"(^|[\s;&|(])brew\b"),
        (
            re.compile(r"^(==> (Downloading|Fetching|Pouring)|#{3,}\s*\d*\.?\d*%?$)"),
            *_PROGRESS_BAR_PATTERNS,
        ),
    ),
    # Universal: a line consisting solely of spinner glyphs is ceremony
    # regardless of what produced it.
    (
        None,
        (re.compile(r"^(⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏)+\s*$"),),
    ),
)


def filter_noise_strip(command: str, exit_code: int, text: str) -> FilterHit | None:
    active: list[re.Pattern[str]] = []
    for matcher, patterns in _NOISE_GROUPS:
        if matcher is None or matcher.search(command):
            active.extend(patterns)
    if not active:
        return None
    lines = text.split("\n")
    kept: list[str] = []
    dropped = 0
    for line in lines:
        if any(p.search(line) for p in active):
            dropped += 1
            continue
        kept.append(line)
    if dropped == 0:
        return None
    kept = _collapse_blank_runs(kept)
    return FilterHit(name="noise-strip", body="\n".join(kept).strip("\n"), safe_loss=True)


# ─────────────────────────────────────────────────────────────────────────────
# 3. log_dedup — repeated log lines → counts (RTK log_cmd.rs, doc 03 §2.8)
# ─────────────────────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}[.,]?\d*\]?\s*")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUM_RE = re.compile(r"\b\d{4,}\b")
_PATH_RE = re.compile(r"/[\w./\-]+")
_LOGGY_RE = re.compile(
    r"^\[?\d{4}[-/]\d{2}[-/]\d{2}|\b(ERROR|WARN(ING)?|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b"
)
_ERR_WORD_RE = re.compile(r"error|fatal|panic|critical|alert|emerg|severe", re.IGNORECASE)
_WARN_WORD_RE = re.compile(r"warn|notice", re.IGNORECASE)


def _normalize_log_line(line: str) -> str:
    line = _TS_RE.sub("", line)
    line = _UUID_RE.sub("<UUID>", line)
    line = _HEX_RE.sub("<HEX>", line)
    line = _NUM_RE.sub("<NUM>", line)
    line = _PATH_RE.sub("<PATH>", line)
    return line.strip()


def filter_log_dedup(command: str, exit_code: int, text: str) -> FilterHit | None:
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < LOG_MIN_LINES:
        return None
    loggy = sum(1 for l in lines if _LOGGY_RE.search(l))
    if loggy < len(lines) * 0.5:
        return None

    err_counts: dict[str, int] = {}
    err_first: dict[str, str] = {}
    warn_counts: dict[str, int] = {}
    warn_first: dict[str, str] = {}
    info = 0
    other = 0
    for line in lines:
        norm = _normalize_log_line(line)
        if _ERR_WORD_RE.search(line):
            err_counts[norm] = err_counts.get(norm, 0) + 1
            err_first.setdefault(norm, line.strip())
        elif _WARN_WORD_RE.search(line):
            warn_counts[norm] = warn_counts.get(norm, 0) + 1
            warn_first.setdefault(norm, line.strip())
        elif "info" in line.lower():
            info += 1
        else:
            other += 1

    out = [
        f"Log summary ({len(lines)} lines): "
        f"{sum(err_counts.values())} errors ({len(err_counts)} unique), "
        f"{sum(warn_counts.values())} warnings ({len(warn_counts)} unique), "
        f"{info} info, {other} other"
    ]
    if err_counts:
        out.append("")
        out.append("[ERRORS]")
        for norm, count in sorted(err_counts.items(), key=lambda kv: -kv[1])[:CAP_ERRORS]:
            prefix = f"[×{count}] " if count > 1 else ""
            out.append(f"  {prefix}{_clamp(err_first[norm])}")
        if len(err_counts) > CAP_ERRORS:
            out.append(f"  ... +{len(err_counts) - CAP_ERRORS} more unique errors")
    if warn_counts:
        out.append("")
        out.append("[WARNINGS]")
        for norm, count in sorted(warn_counts.items(), key=lambda kv: -kv[1])[:CAP_WARNINGS]:
            prefix = f"[×{count}] " if count > 1 else ""
            out.append(f"  {prefix}{_clamp(warn_first[norm])}")
        if len(warn_counts) > CAP_WARNINGS:
            out.append(f"  ... +{len(warn_counts) - CAP_WARNINGS} more unique warnings")
    return FilterHit(name="log-dedup", body="\n".join(out))


# ─────────────────────────────────────────────────────────────────────────────
# 4. large_success_cap — recoverable head window (RTK strategy 12)
#
# Fallback for successful, no-better-filter outputs: keep the head, point at
# the rest with a runnable `tail -n +N` hint. The recoverable version of the
# blind `[N lines truncated]` marker bash's truncate_output produces today.
# ─────────────────────────────────────────────────────────────────────────────


def filter_large_success(command: str, exit_code: int, text: str) -> FilterHit | None:
    if exit_code != 0:
        return None
    lines = text.split("\n")
    if len(lines) <= LARGE_OUTPUT_LINES:
        return None
    head = lines[:HEAD_KEEP]
    body = "\n".join(head) + f"\n... (+{len(lines) - HEAD_KEEP} more lines)"
    return FilterHit(
        name="head-cap",
        body=body,
        tail_offset=HEAD_KEEP + 1,
    )


# Ordered registry: most specific first; first hit wins (RTK dispatch order).
FILTERS = (
    filter_test_runner,
    filter_log_dedup,
    filter_noise_strip,
    filter_large_success,
)
