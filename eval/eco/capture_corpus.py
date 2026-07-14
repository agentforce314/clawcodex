#!/usr/bin/env python3
"""Capture a real-output corpus for the /eco token-compression benchmark.

Mirrors the experiment behind RTK's README savings table
(https://github.com/rtk-ai/rtk): a set of operations an agent actually runs
in a coding session — test runners, git, package installs, listings, logs —
except every number here is *measured from a real command run*, never
estimated. This script only captures; ``measure.py`` replays the captures
through the production eco pipeline and counts tokens.

Corpus item = one JSON file: the command an agent would type, its exit code,
and full (untruncated) stdout/stderr. Sample projects (pytest/go/jest with
genuine failing tests) are built in ``--workdir`` so the failure output is
real tool output, RTK's own fixture rule ("never synthetic"). Repo-scale
items (git log/diff, big listings) run against this repository itself.

Stdlib-only on purpose: capture must not depend on the package under test.

Usage:
    python3 eval/eco/capture_corpus.py --workdir /tmp/eco-bench
    # then:  .venv/bin/python eval/eco/measure.py
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = Path(__file__).resolve().parent / "corpus"

# Full outputs are stored untruncated up to this cap; the eco engine itself
# accepts up to 10 MiB, and nothing in this corpus should get near either.
MAX_STORE_BYTES = 5 * 1024 * 1024

# Dropped into --workdir on creation; a pre-existing non-empty dir without it
# is refused rather than rmtree'd (a mistyped path must not cost user data).
WORKDIR_MARKER = ".eco-bench-workdir"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _pytest_python(cli_override: str | None) -> str:
    """A python that can actually run pytest: --python, repo venv, this one."""
    for cand in (cli_override, str(REPO_ROOT / ".venv/bin/python"), sys.executable):
        if not cand or not Path(cand).exists():
            continue
        try:
            probe = subprocess.run(
                [cand, "-c", "import pytest"], capture_output=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return cand
    raise SystemExit(
        "no python with pytest importable — pass --python /path/to/venv/bin/python"
    )


class Capture:
    def __init__(self, corpus_dir: Path) -> None:
        self.corpus_dir = corpus_dir
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        self.ok: list[str] = []
        self.failed: list[str] = []

    def run(
        self,
        label: str,
        op: str,
        command: str,
        *,
        cwd: Path,
        executed: str | None = None,
        timeout: int = 180,
        category: str = "",
    ) -> None:
        """Run one corpus command and store its full output.

        ``command`` is the agent-facing string (what eco's command-family
        matchers see); ``executed`` overrides what is actually run (e.g. a
        venv-qualified pytest) and defaults to ``command``.
        """
        try:
            t0 = time.monotonic()
            proc = subprocess.run(
                executed or command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
            dur_ms = int((time.monotonic() - t0) * 1000)
            item = {
                "label": label,
                "op": op,
                "category": category,
                "command": command,
                "executed": executed or command,
                "exit_code": proc.returncode,
                "stdout": proc.stdout[:MAX_STORE_BYTES],
                "stderr": proc.stderr[:MAX_STORE_BYTES],
                "duration_ms": dur_ms,
                "captured_at": _now_iso(),
                "platform": platform.platform(),
            }
            out = self.corpus_dir / f"{label}.json"
            out.write_text(json.dumps(item, ensure_ascii=False, indent=1))
            n_lines = item["stdout"].count("\n") + item["stderr"].count("\n")
            print(f"  [ok] {label}: exit={proc.returncode} ~{n_lines} lines {dur_ms}ms")
            self.ok.append(label)
        except Exception as exc:  # noqa: BLE001 — one bad item must not kill the sweep
            print(f"  [SKIP] {label}: {exc}")
            self.failed.append(label)


# ── sample projects (real tools, genuine failing tests) ─────────────────────


def build_pysample(root: Path) -> Path:
    """Small python lib + pytest suite; 5 of 34 tests fail on real bugs."""
    d = root / "pysample"
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "cart.py").write_text(
        '''"""Tiny shopping-cart lib (sample project for the eco benchmark)."""
import os


class Cart:
    def __init__(self):
        self.items = []

    def add(self, name, unit_price, qty=1):
        if qty < 1:
            raise ValueError("qty must be >= 1")
        self.items.append((name, unit_price, qty))

    def subtotal(self):
        return sum(price * qty for _, price, qty in self.items)

    def total(self, discount_pct=0):
        # BUG: discount is truncated to int cents per item instead of
        # applied once to the subtotal — off by cents in real carts.
        total = 0
        for _, price, qty in self.items:
            line = price * qty
            total += int(line * (100 - discount_pct)) / 100
        return total

    def count(self):
        return sum(qty for _, _, qty in self.items)
'''
    )
    (d / "textutil.py").write_text(
        '''"""Text helpers (sample project for the eco benchmark)."""


def slugify(text):
    out = []
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def truncate_words(text, max_words):
    words = text.split()
    word_count = len(words)
    if len(words) <= max_words:
        return text
    # BUG: keeps one word too many (classic off-by-one).
    return " ".join(words[: max_words + 1]) + "..."
'''
    )
    (d / "tests" / "test_cart.py").write_text(
        """from cart import Cart
import pytest


def make_cart():
    c = Cart()
    c.add("apple", 1.25, 4)
    c.add("bread", 3.50)
    c.add("milk", 2.15, 2)
    return c


def test_empty_subtotal():
    assert Cart().subtotal() == 0


def test_add_single():
    c = Cart()
    c.add("apple", 1.25)
    assert c.subtotal() == 1.25


def test_add_qty():
    c = Cart()
    c.add("apple", 1.25, 4)
    assert c.subtotal() == 5.0


def test_count():
    assert make_cart().count() == 7


def test_subtotal_mixed():
    assert make_cart().subtotal() == pytest.approx(12.80)


def test_total_no_discount():
    assert make_cart().total() == pytest.approx(12.80)


def test_total_discount():
    assert make_cart().total(discount_pct=10) == pytest.approx(11.52)


def test_total_discount_rounds_once():
    c = Cart()
    c.add("pen", 0.99, 3)
    assert c.total(discount_pct=15) == pytest.approx(2.52, abs=0.005)


def test_qty_validation():
    c = Cart()
    with pytest.raises(ValueError):
        c.add("apple", 1.0, 0)


def test_add_many_lines():
    c = Cart()
    for i in range(10):
        c.add(f"item{i}", 1.0)
    assert c.count() == 10


def test_subtotal_after_many():
    c = Cart()
    for i in range(10):
        c.add(f"item{i}", 2.5)
    assert c.subtotal() == 25.0


def test_zero_discount_is_subtotal():
    c = make_cart()
    assert c.total(0) == pytest.approx(c.subtotal())
"""
    )
    (d / "tests" / "test_textutil.py").write_text(
        """from textutil import slugify, truncate_words


def test_slug_basic():
    assert slugify("Hello World") == "hello-world"


def test_slug_punctuation():
    assert slugify("a, b, c!") == "a-b-c"


def test_slug_leading_junk():
    assert slugify("--Already--Slugged--") == "already-slugged"


def test_slug_unicode_dropped():
    assert slugify("café au lait") == "café-au-lait"


def test_slug_empty():
    assert slugify("") == ""


def test_slug_numbers():
    assert slugify("v1.2.3 release") == "v1-2-3-release"


def test_truncate_short():
    assert truncate_words("one two", 5) == "one two"


def test_truncate_exact():
    assert truncate_words("one two three", 3) == "one two three"


def test_truncate_long():
    assert truncate_words("one two three four five", 3) == "one two three..."


def test_truncate_one():
    assert truncate_words("alpha beta", 1) == "alpha..."


def test_slug_spaces_collapse():
    assert slugify("a   b") == "a-b"


def test_slug_mixed_case():
    assert slugify("MiXeD CaSe") == "mixed-case"
"""
    )
    (d / "orders.py").write_text(
        '''"""Order handling (sample project for the eco benchmark)."""


class OrderError(Exception):
    pass


def _validate_qty(qty):
    if qty < 1:
        raise OrderError(f"qty must be >= 1, got {qty}")
    return qty


def _unit_price(catalog, sku):
    try:
        return catalog[sku]
    except KeyError:
        raise OrderError(f"unknown sku: {sku}") from None


class Order:
    def __init__(self, catalog):
        self.catalog = catalog
        self.lines = []

    def add(self, sku, qty=1):
        self.lines.append((sku, _validate_qty(qty)))

    def total(self):
        return sum(_unit_price(self.catalog, sku) * qty for sku, qty in self.lines)

    def summary(self):
        # BUG: counts order lines, not items.
        return f"{len(self.lines)} items, total ${self.total():.2f}"
'''
    )
    (d / "tests" / "test_orders.py").write_text(
        """from orders import Order, OrderError
import pytest

CATALOG = {"tea": 4.50, "mug": 11.00, "spoon": 1.25}


def make_order():
    o = Order(CATALOG)
    o.add("tea", 2)
    o.add("mug", 1)
    return o


def test_total():
    assert make_order().total() == pytest.approx(20.0)


def test_add_validates_qty():
    with pytest.raises(OrderError):
        make_order().add("tea", 0)


def test_unknown_sku_raises():
    o = Order(CATALOG)
    o.add("gold-bar")
    with pytest.raises(OrderError):
        o.total()


def test_unknown_sku_message():
    o = Order(CATALOG)
    o.add("gold-bar")
    with pytest.raises(OrderError, match="unknown sku 'gold-bar'"):
        o.total()


def test_summary_counts_items():
    o = Order(CATALOG)
    o.add("tea", 2)
    o.add("spoon", 3)
    assert o.summary() == "5 items, total $12.75"


def test_summary_total_formatting():
    assert make_order().summary().endswith("$20.00")


def test_total_with_seasonal_catalog():
    seasonal = {"tea": 4.50, "mug": 11.00}
    o = Order(seasonal)
    o.add("tea")
    o.add("cinnamon", 2)
    assert o.total() == pytest.approx(8.10)


def test_empty_order():
    assert Order(CATALOG).total() == 0


def test_multi_line_total():
    o = make_order()
    o.add("spoon", 4)
    assert o.total() == pytest.approx(25.0)


def test_lines_accumulate():
    o = make_order()
    assert len(o.lines) == 2
"""
    )
    (d / "pytest.ini").write_text("[pytest]\npythonpath = .\ntestpaths = tests\n")
    return d


def build_gosample(root: Path) -> Path:
    """Go module, 3 packages, 2 with genuinely failing tests."""
    d = root / "gosample"
    for pkg in ("mathx", "strfmt", "version"):
        (d / pkg).mkdir(parents=True, exist_ok=True)
    (d / "go.mod").write_text("module sample.dev/gosample\n\ngo 1.21\n")
    (d / "mathx" / "mathx.go").write_text(
        """package mathx

func Clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// Mean returns the arithmetic mean.
// BUG: integer division happens before the float conversion.
func Mean(xs []int) float64 {
	if len(xs) == 0 {
		return 0
	}
	sum := 0
	for _, x := range xs {
		sum += x
	}
	return float64(sum / len(xs))
}
"""
    )
    (d / "mathx" / "median.go").write_text(
        """package mathx

import "sort"

// Median returns the middle value.
// BUG: sorts the caller's slice in place AND mishandles even lengths.
func Median(xs []int) float64 {
	if len(xs) == 0 {
		return 0
	}
	sort.Ints(xs)
	return float64(xs[len(xs)/2])
}
"""
    )
    (d / "mathx" / "mathx_test.go").write_text(
        """package mathx

import "testing"

func TestClamp(t *testing.T) {
	cases := []struct{ v, lo, hi, want int }{
		{5, 0, 10, 5}, {-3, 0, 10, 0}, {42, 0, 10, 10},
	}
	for _, c := range cases {
		if got := Clamp(c.v, c.lo, c.hi); got != c.want {
			t.Errorf("Clamp(%d,%d,%d) = %d, want %d", c.v, c.lo, c.hi, got, c.want)
		}
	}
}

func TestMeanWhole(t *testing.T) {
	if got := Mean([]int{2, 4, 6}); got != 4.0 {
		t.Errorf("Mean = %v, want 4.0", got)
	}
}

func TestMeanFractional(t *testing.T) {
	if got := Mean([]int{1, 2, 2}); got < 1.66 || got > 1.67 {
		t.Errorf("Mean([1 2 2]) = %v, want ~1.667", got)
	}
}

func TestMedianTable(t *testing.T) {
	cases := []struct {
		name string
		in   []int
		want float64
	}{
		{"odd sorted", []int{1, 3, 9}, 3},
		{"odd unsorted", []int{9, 1, 3}, 3},
		{"even mid", []int{1, 2, 3, 4}, 2.5},
		{"even unsorted", []int{4, 1, 3, 2}, 2.5},
		{"single", []int{7}, 7},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Median(c.in); got != c.want {
				t.Errorf("Median(%v) = %v, want %v", c.in, got, c.want)
			}
		})
	}
}

func TestMedianDoesNotMutate(t *testing.T) {
	in := []int{9, 1, 3}
	Median(in)
	if in[0] != 9 {
		t.Errorf("Median mutated its input: %v", in)
	}
}
"""
    )
    (d / "strfmt" / "strfmt.go").write_text(
        """package strfmt

import "strings"

// PadLeft pads s with spaces to exactly width runes.
// BUG: off by one when padding is needed.
func PadLeft(s string, width int) string {
	if len(s) >= width {
		return s
	}
	return strings.Repeat(" ", width-len(s)-1) + s
}

func Ellipsis(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max-3] + "..."
}
"""
    )
    (d / "strfmt" / "strfmt_test.go").write_text(
        """package strfmt

import "testing"

func TestPadLeft(t *testing.T) {
	cases := []struct {
		in    string
		width int
		want  string
	}{
		{"7", 3, "  7"},
		{"42", 5, "   42"},
		{"abc", 3, "abc"},
		{"abcdef", 3, "abcdef"},
		{"", 2, "  "},
	}
	for _, c := range cases {
		if got := PadLeft(c.in, c.width); got != c.want {
			t.Errorf("PadLeft(%q, %d) = %q, want %q", c.in, c.width, got, c.want)
		}
	}
}

func TestEllipsis(t *testing.T) {
	if got := Ellipsis("hello world", 8); got != "hello..." {
		t.Errorf("Ellipsis = %q", got)
	}
	if got := Ellipsis("hi", 8); got != "hi" {
		t.Errorf("Ellipsis short = %q", got)
	}
}
"""
    )
    (d / "version" / "version.go").write_text(
        "package version\n\nconst Current = \"1.4.2\"\n"
    )
    (d / "version" / "version_test.go").write_text(
        """package version

import "testing"

func TestCurrent(t *testing.T) {
	if Current == "" {
		t.Fatal("version must not be empty")
	}
}
"""
    )
    return d


_DURATION_BUGGY = """// Duration formatting (sample project for the eco benchmark).
// BUG: minutes are not zero-padded.
function formatDuration(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  if (h > 0) return `${h}h ${m}m ${pad(s)}s`;
  return `${m}m ${pad(s)}s`;
}

module.exports = { formatDuration };
"""

_DURATION_FIXED = _DURATION_BUGGY.replace(
    "return `${h}h ${m}m ${pad(s)}s`", "return `${h}h ${pad(m)}m ${pad(s)}s`"
).replace("// BUG: minutes are not zero-padded.\n", "")


def build_jssample(root: Path) -> Path:
    """Node project with a jest suite; 2 of 10 tests fail on a real bug."""
    d = root / "jssample"
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(
        json.dumps(
            {
                "name": "jssample",
                "version": "1.0.0",
                "license": "MIT",
                "scripts": {"test": "jest"},
            },
            indent=2,
        )
    )
    (d / "duration.js").write_text(_DURATION_BUGGY)
    (d / "duration.test.js").write_text(
        """const { formatDuration } = require("./duration");

test("seconds only", () => expect(formatDuration(7)).toBe("0m 07s"));
test("minutes and seconds", () => expect(formatDuration(125)).toBe("2m 05s"));
test("exact minute", () => expect(formatDuration(60)).toBe("1m 00s"));
test("hours pad minutes", () => expect(formatDuration(3723)).toBe("1h 02m 03s"));
test("hours zero minutes", () => expect(formatDuration(3605)).toBe("1h 00m 05s"));
test("zero", () => expect(formatDuration(0)).toBe("0m 00s"));
"""
    )
    (d / "slug.js").write_text(
        """function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

module.exports = { slugify };
"""
    )
    (d / "slug.test.js").write_text(
        """const { slugify } = require("./slug");

test("basic", () => expect(slugify("Hello World")).toBe("hello-world"));
test("punctuation", () => expect(slugify("a, b, c!")).toBe("a-b-c"));
test("edges", () => expect(slugify("--x--")).toBe("x"));
test("empty", () => expect(slugify("")).toBe(""));
"""
    )
    return d


# ── capture sweep ────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", required=True, help="scratch dir for sample projects")
    ap.add_argument("--corpus", default=str(CORPUS_DIR), help="output corpus dir")
    ap.add_argument("--repo", default=str(REPO_ROOT), help="repository to benchmark against")
    ap.add_argument("--python", default=None, help="python with pytest (default: repo venv)")
    args = ap.parse_args()

    work = Path(args.workdir).resolve()
    repo = Path(args.repo).resolve()
    if work.exists():
        if any(work.iterdir()) and not (work / WORKDIR_MARKER).exists():
            raise SystemExit(
                f"refusing to delete pre-existing non-empty {work} — it was not "
                f"created by this script (no {WORKDIR_MARKER} marker); pass a "
                "fresh --workdir path"
            )
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / WORKDIR_MARKER).write_text(
        "scratch dir created by eval/eco/capture_corpus.py — safe to delete\n"
    )
    cap = Capture(Path(args.corpus).resolve())
    py = _pytest_python(args.python)

    # 1. pytest — sample project with real failures, plain + verbose + green.
    print("== pytest (sample project) ==")
    pysample = build_pysample(work)
    cap.run(
        "pytest-fail", "pytest (failing run)", "pytest",
        executed=f"{py} -m pytest", cwd=pysample, category="test-runner",
    )
    cap.run(
        "pytest-fail-verbose", "pytest -v (failing run)", "pytest -v",
        executed=f"{py} -m pytest -v", cwd=pysample, category="test-runner",
    )
    cap.run(
        "pytest-green-verbose", "pytest -v (green run)",
        "pytest -v tests/test_cart.py",
        executed=f"{py} -m pytest -v tests/test_cart.py",
        cwd=pysample, category="test-runner",
    )

    # 2. pytest — a green slice of this repository's real suite.
    print("== pytest (this repo, green slice) ==")
    cap.run(
        "pytest-green-repo",
        "pytest (repo suite slice, all pass)",
        "pytest tests/test_eco.py tests/test_bash_eco_integration.py",
        executed=f"{py} -m pytest tests/test_eco.py tests/test_bash_eco_integration.py",
        cwd=repo, category="test-runner", timeout=600,
    )

    # 3. go test — sample module with real failures.
    if shutil.which("go"):
        print("== go test ==")
        gosample = build_gosample(work)
        cap.run(
            "go-test-fail", "go test ./... (failing run)", "go test ./...",
            cwd=gosample, category="test-runner", timeout=300,
        )
        cap.run(
            "go-test-fail-verbose", "go test -v ./... (failing run)",
            "go test -v ./...", cwd=gosample, category="test-runner", timeout=300,
        )
    else:
        print("== go not installed; skipping go test ==")

    # 4. jest — npm install (ceremony corpus) + failing + green runs.
    if shutil.which("npm"):
        print("== jest ==")
        jssample = build_jssample(work)
        cap.run(
            "npm-install", "npm install jest", "npm install --no-fund --no-audit jest",
            cwd=jssample, category="package-manager", timeout=600,
        )
        cap.run(
            "jest-fail", "npx jest (failing run)", "npx jest",
            cwd=jssample, category="test-runner", timeout=300,
        )
        (jssample / "duration.js").write_text(_DURATION_FIXED)
        cap.run(
            "jest-green", "npx jest (green run)", "npx jest",
            cwd=jssample, category="test-runner", timeout=300,
        )
    else:
        print("== npm not installed; skipping jest ==")

    # 5. pip install into a fresh venv (Collecting/Downloading ceremony).
    print("== pip install ==")
    pipvenv = work / "pipvenv"
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(pipvenv)],
            check=True, capture_output=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  [SKIP] pip-install + ruff-check: venv creation failed ({exc})")
        pipvenv = None
    if pipvenv is not None:
        pip = pipvenv / "bin" / "pip"
        cap.run(
            "pip-install", "pip install flask", "pip install --no-cache-dir flask",
            executed=f"{pip} install --no-cache-dir flask",
            cwd=work, category="package-manager", timeout=600,
        )

        # 6. ruff check — real lint errors, no eco filter (honest passthrough).
        try:
            subprocess.run(
                [str(pip), "install", "--quiet", "ruff"],
                capture_output=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  [SKIP] ruff install: {exc}")
        ruff = pipvenv / "bin" / "ruff"
        if ruff.exists():
            cap.run(
                "ruff-check", "ruff check (findings)", "ruff check .",
                executed=f"{ruff} check .", cwd=pysample, category="lint",
            )
        else:
            print("  [SKIP] ruff-check: ruff not installed")

    # 7. git — clone with progress, dirty status, commit, push to a local bare.
    print("== git ==")
    clone = work / "clawcodex-clone"
    cap.run(
        "git-clone", "git clone --progress",
        f"git clone --no-local --progress {repo} {clone}",
        cwd=work, category="git", timeout=600,
    )
    if clone.exists():
        # Clean-tree status is captured in the pristine clone (the benchmark
        # repo itself may be mid-change while this script runs).
        cap.run(
            "git-status-clean", "git status (clean tree)", "git status",
            cwd=clone, category="passthrough",
        )
        for rel in ("README.md", "pyproject.toml", "src/eco/state.py"):
            p = clone / rel
            if p.exists():
                with p.open("a") as fh:
                    fh.write("\n# local experiment\n")
        (clone / "notes.txt").write_text("scratch notes\n")
        (clone / "scratch").mkdir(exist_ok=True)
        (clone / "scratch" / "probe.py").write_text("print('probe')\n")
        cap.run(
            "git-status-dirty", "git status (3 modified, 2 untracked)", "git status",
            cwd=clone, category="git",
        )
        bare = work / "push-target.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "remote", "add", "target", str(bare)],
            cwd=clone, check=True, capture_output=True,
        )
        subprocess.run(["git", "add", "-A"], cwd=clone, check=True, capture_output=True)
        cap.run(
            "git-commit", "git commit", 'git commit -m "wip: local experiment"',
            cwd=clone, category="git",
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=clone,
            capture_output=True, text=True,
        ).stdout.strip() or "main"
        cap.run(
            "git-push", "git push --progress",
            f"git push --progress target {branch}",
            cwd=clone, category="git", timeout=300,
        )

    # 8. repo-scale reads: history, diffs, listings, big files, grep sweeps.
    print("== repo-scale reads ==")
    cap.run("git-log-full", "git log -n 300 (full format)", "git log -n 300",
            cwd=repo, category="listing")
    cap.run("git-log-oneline", "git log --oneline (full history)",
            "git log --oneline", cwd=repo, category="listing")
    diff_range = "v1.0.0..v1.1.0"
    have_tags = subprocess.run(
        ["git", "rev-parse", "v1.0.0", "v1.1.0"], cwd=repo, capture_output=True
    ).returncode == 0
    if not have_tags:
        diff_range = "HEAD~30..HEAD"
    cap.run("git-diff-large", f"git diff {diff_range} -- src",
            f"git diff {diff_range} -- src", cwd=repo, category="listing")
    cap.run("ls-R", "ls -R src (whole tree)", "ls -R src", cwd=repo, category="listing")
    cap.run("find-py", "find . -name '*.py'",
            "find . -type f -name '*.py' -not -path './.git/*'",
            cwd=repo, category="listing")
    cap.run("cat-large", "cat (900-line source file)",
            "cat src/tool_system/tools/bash/bash_tool.py", cwd=repo, category="listing")
    cap.run("grep-defs", "grep -rn 'def ' (subsystem sweep)",
            "grep -rn 'def ' src/tool_system/", cwd=repo, category="listing")
    cap.run("grep-defs-wide", "grep -rn 'def ' src/ (repo-wide)",
            "grep -rn 'def ' src/", cwd=repo, category="listing")

    # 9. log-shaped output — macOS unified log (real timestamped log lines).
    if platform.system() == "Darwin":
        print("== system log ==")
        cap.run(
            "log-dedup", "log show --last 90s (system log)",
            "log show --last 90s --style syslog",
            cwd=work, category="logs", timeout=300,
        )

    # 10. small outputs — must pass through byte-identical (never-worse rows).
    print("== small passthrough rows ==")
    cap.run("wc-readme", "wc -l README.md", "wc -l README.md",
            cwd=repo, category="passthrough")
    docker_up = False
    if shutil.which("docker"):
        try:
            docker_up = (
                subprocess.run(
                    ["docker", "info"], capture_output=True, timeout=20
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            docker_up = False
    if docker_up:
        cap.run("docker-ps", "docker ps", "docker ps", cwd=work, category="passthrough")
    else:
        print("  [SKIP] docker-ps: no running docker daemon")

    print(f"\ncaptured {len(cap.ok)} items → {cap.corpus_dir}")
    if cap.failed:
        print(f"failed: {', '.join(cap.failed)}")
    # Partial failures exit non-zero so a benchmark run can't silently lose rows.
    return 0 if (cap.ok and not cap.failed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
