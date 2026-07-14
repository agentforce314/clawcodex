#!/usr/bin/env python3
"""Measure /eco token savings over a captured corpus (see capture_corpus.py).

Each corpus item is replayed through the *production* pipeline, byte-for-byte
what the Bash tool does when ``/eco`` is on:

    baseline = _assemble_bash_body(truncate_output(stdout), truncate_output(stderr))
    outcome  = compress_bash_output(command, exit_code, full_text, baseline, tee_dir)

and both renderings get the mapper's ``returnCodeInterpretation`` suffix, so
the counted text is exactly the wire content with ``/eco`` off vs on. Tokens
are counted with tiktoken ``cl100k_base`` — real tokenizer counts, not the
chars/4 estimate RTK reports (chars/4 is also computed, into the JSON, for
comparability with RTK's methodology).

Determinism: tee filenames embed ``time_ns``/``pid``; both are pinned here and
the tee dir is fixed (``corpus/.tee``), so on an unchanged corpus this script
is a pure function — reruns must not churn the committed ``results/``. The
recovery-hint line is charged to eco at the bench path's length; for the
committed run that tokenizes no shorter than a typical production
``~/.clawcodex/...`` hint, so its published savings are, if anything, slightly
understated.

Outputs (default ``eval/eco/results/``):
    results.md    — per-operation table + corpus totals + RTK session projection
    results.json  — full numbers, per item
    examples/     — before/after renderings for a few showcase items

Run from the repo root with the project venv:
    .venv/bin/python eval/eco/measure.py [--corpus DIR] [--results DIR]
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.eco.tee as _tee  # noqa: E402
from src.eco.engine import compress_bash_output  # noqa: E402
from src.eco.guard import estimate_tokens as chars4_tokens  # noqa: E402
from src.tool_system.tools.bash.bash_tool import _assemble_bash_body  # noqa: E402
from src.tool_system.tools.bash.command_semantics import (  # noqa: E402
    interpret_command_result,
)
from src.tool_system.tools.bash.utils import truncate_output  # noqa: E402

try:
    import tiktoken
except ImportError:  # pragma: no cover — measurement requires the real tokenizer
    sys.exit("measure.py needs tiktoken (the README numbers are real token counts): pip install tiktoken")

_ENC = tiktoken.get_encoding("cl100k_base")

CATEGORY_ORDER = (
    "test-runner", "package-manager", "git", "listing", "logs", "lint", "passthrough",
)

EXAMPLE_LABELS = ("pytest-fail", "git-status-dirty", "npm-install")


def _pin_tee_determinism() -> None:
    """Tee filenames are ``{time_ns}_{pid}_{counter}_{slug}.log``; pin the
    volatile parts so identical corpora produce identical recovery hints
    (reruns must not churn the committed results/examples)."""
    _tee.time = types.SimpleNamespace(time_ns=lambda: 1707)

    real_os = _tee.os

    class _PinnedOS:
        def __getattr__(self, name):
            return getattr(real_os, name)

        @staticmethod
        def getpid() -> int:
            return 0

    _tee.os = _PinnedOS()
    _tee._counter = itertools.count()


def tok(text: str) -> int:
    return len(_ENC.encode(text, disallowed_special=()))


def measure_item(item: dict, tee_dir: Path) -> dict:
    baseline = _assemble_bash_body(
        truncate_output(item["stdout"]), truncate_output(item["stderr"])
    )
    full_text = _assemble_bash_body(item["stdout"], item["stderr"])
    outcome = compress_bash_output(
        command=item["command"],
        exit_code=item["exit_code"],
        full_text=full_text,
        baseline=baseline,
        tee_dir=tee_dir,
    )
    final = outcome.content if outcome is not None else baseline

    # The mapper appends returnCodeInterpretation after the body on BOTH
    # paths (bash_tool._bash_map_result_to_api); include it so the counted
    # text is exactly the wire content.
    interp = interpret_command_result(
        item["command"], item["exit_code"], item["stdout"], item["stderr"]
    ).message
    baseline_wire = f"{baseline}\n{interp}" if interp else baseline
    final_wire = f"{final}\n{interp}" if interp else final

    b_tok, f_tok = tok(baseline_wire), tok(final_wire)
    saved = b_tok - f_tok
    return {
        "label": item["label"],
        "op": item["op"],
        "category": item.get("category", ""),
        "command": item["command"],
        "exit_code": item["exit_code"],
        "filter": outcome.filter_name if outcome else None,
        "baseline_chars": len(baseline_wire),
        "eco_chars": len(final_wire),
        "baseline_tokens": b_tok,
        "eco_tokens": f_tok,
        "saved_tokens": saved,
        "saved_pct": round(100.0 * saved / b_tok, 1) if b_tok else 0.0,
        "baseline_tokens_chars4": chars4_tokens(baseline_wire),
        "eco_tokens_chars4": chars4_tokens(final_wire),
        "baseline_text": baseline_wire,
        "eco_text": final_wire,
    }


# ── RTK 30-minute-session projection ─────────────────────────────────────────
# RTK's README models a 30-minute Claude Code session as a frequency-weighted
# mix of operations and claims -80% overall (~118,000 → ~23,900 tokens),
# estimated, per their caveat, on "medium-sized TypeScript/Rust projects".
# We recompute the SAME session model with our *measured* savings ratios.
# Where RTK's assumed per-call size sits below eco's thresholds (a ~200-token
# `ls`, a ~2,000-token `cat` — under the 400-line head-cap), eco passes the
# output through untouched, so those rows honestly contribute 0%.
#
# rows: (operation, freq, rtk_standard_tokens, rtk_tokens, [(label|None, share)])
RTK_SESSION = (
    ("`ls` / `tree`", 10, 2000, 400, ((None, 1.0),)),
    ("`cat` / read", 20, 40000, 12000, ((None, 1.0),)),
    ("`grep` / `rg`", 8, 16000, 3200, ((None, 1.0),)),
    ("`git status`", 10, 3000, 600, (("git-status-dirty", 1.0),)),
    ("`git diff`", 5, 10000, 2500, ((None, 1.0),)),
    ("`git log`", 5, 2500, 500, ((None, 1.0),)),
    # 8 calls ≈ add + commit + push cycles; only push output compresses.
    ("`git add/commit/push`", 8, 1600, 120, (("git-push", 0.375), (None, 0.625))),
    ("`cargo test` / `npm test`", 5, 25000, 2500, (("jest-fail", 1.0),)),
    ("`ruff check`", 3, 3000, 600, (("ruff-check", 1.0),)),
    ("`pytest`", 4, 8000, 800, (("pytest-fail", 1.0),)),
    ("`go test`", 3, 6000, 600, (("go-test-fail", 1.0),)),
    ("`docker ps`", 3, 900, 180, (("docker-ps", 1.0),)),
)

# RTK's README states the totals as ~118,000 → ~23,900 (-80%); its own rows
# sum to 24,000. Quote their claimed total for the comparison line.
RTK_CLAIMED_TOTAL = 23_900


def project_session(by_label: dict[str, dict]) -> tuple[list[dict], int, int]:
    rows = []
    std_total = ours_total = 0
    for op, freq, std, rtk, mix in RTK_SESSION:
        ours = 0.0
        for label, share in mix:
            m = by_label.get(label) if label else None
            ratio = (m["saved_pct"] / 100.0) if m else 0.0
            ours += std * share * (1.0 - ratio)
        rows.append(
            {
                "op": op, "freq": freq, "standard": std, "rtk": rtk,
                "eco": int(round(ours)),
                # Floor to match fmt_pct — display never overstates savings.
                "eco_pct": float(int(100.0 * (1 - ours / std))) if std else 0.0,
            }
        )
        std_total += std
        ours_total += int(round(ours))
    return rows, std_total, ours_total


def fmt_pct(saved_pct: float) -> str:
    """Floor, not round: a 99.7% saving displays as -99%, never a false -100%."""
    floored = int(saved_pct)
    return f"-{floored}%" if floored >= 1 else "0%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(HERE / "corpus"), help="captured corpus dir")
    ap.add_argument("--results", default=str(HERE / "results"), help="output dir")
    args = ap.parse_args()

    corpus_dir = Path(args.corpus).resolve()
    results_dir = Path(args.results).resolve()
    examples_dir = results_dir / "examples"
    results_dir.mkdir(parents=True, exist_ok=True)
    examples_dir.mkdir(exist_ok=True)

    items = []
    for path in sorted(corpus_dir.glob("*.json")):
        items.append(json.loads(path.read_text()))
    if not items:
        sys.exit(f"no corpus at {corpus_dir} — run capture_corpus.py first")

    # Fixed, gitignored tee dir; cleared so counter-based names are stable.
    tee_dir = corpus_dir / ".tee"
    if tee_dir.exists():
        shutil.rmtree(tee_dir)
    _pin_tee_determinism()

    measured = [measure_item(it, tee_dir) for it in items]
    measured.sort(
        key=lambda m: (
            CATEGORY_ORDER.index(m["category"])
            if m["category"] in CATEGORY_ORDER
            else len(CATEGORY_ORDER),
            m["label"],
        )
    )
    by_label = {m["label"]: m for m in measured}

    # Never-worse invariant: eco must not emit more (estimated) tokens than
    # the baseline it replaced. The guard operates on chars/4.
    violations = [
        m["label"]
        for m in measured
        if m["eco_tokens_chars4"] > m["baseline_tokens_chars4"]
    ]
    if violations:
        sys.exit(f"never-worse violated (bug!): {violations}")

    # ── per-operation table ──
    lines = [
        "| Operation | Filter | Raw tokens | /eco tokens | Saved |",
        "|---|---|---:|---:|---:|",
    ]
    for m in measured:
        lines.append(
            f"| {m['op']} | {m['filter'] or '— (passthrough)'} "
            f"| {m['baseline_tokens']:,} | {m['eco_tokens']:,} "
            f"| **{fmt_pct(m['saved_pct'])}** |"
        )
    b_sum = sum(m["baseline_tokens"] for m in measured)
    e_sum = sum(m["eco_tokens"] for m in measured)
    total_pct = 100 * (b_sum - e_sum) / b_sum if b_sum else 0.0
    lines.append(
        f"| **Corpus total** |  | **{b_sum:,}** | **{e_sum:,}** "
        f"| **-{int(total_pct)}%** |"
    )
    hits = [m for m in measured if m["filter"]]
    hb = sum(m["baseline_tokens"] for m in hits)
    he = sum(m["eco_tokens"] for m in hits)
    hits_pct = 100 * (hb - he) / hb if hb else 0.0

    # ── RTK session projection ──
    srows, s_std, s_eco = project_session(by_label)
    slines = [
        "| Operation | Freq | Standard | rtk (claimed) | clawcodex /eco (measured) |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in srows:
        slines.append(
            f"| {r['op']} | {r['freq']}x | {r['standard']:,} | {r['rtk']:,} "
            f"| {r['eco']:,} ({fmt_pct(r['eco_pct'])}) |"
        )
    slines.append(
        f"| **Total** |  | **~{s_std:,}** | **~{RTK_CLAIMED_TOTAL:,} (-80%)** "
        f"| **~{s_eco:,} (-{int(100 * (s_std - s_eco) / s_std)}%)** |"
    )

    md = "\n".join(
        [
            "# /eco benchmark results",
            "",
            "Generated by `eval/eco/measure.py` over the corpus captured by",
            "`eval/eco/capture_corpus.py`. Tokens are tiktoken `cl100k_base` counts",
            "of the exact wire content (baseline vs eco rendering, including the",
            "recovery-hint line and `returnCodeInterpretation` suffix).",
            "",
            "## Per-operation (measured on real outputs)",
            "",
            *lines,
            "",
            f"Filter-hit subset: {hb:,} → {he:,} tokens "
            f"(**-{int(hits_pct)}%**) across {len(hits)} operations; "
            f"{len(measured) - len(hits)} operations passed through unchanged (0%).",
            "",
            "## RTK 30-minute-session model, recomputed with measured ratios",
            "",
            *slines,
            "",
        ]
    )
    (results_dir / "results.md").write_text(md)
    (results_dir / "results.json").write_text(
        json.dumps(
            {
                "tokenizer": "cl100k_base",
                "items": [
                    {k: v for k, v in m.items() if not k.endswith("_text")}
                    for m in measured
                ],
                "corpus_total": {"baseline": b_sum, "eco": e_sum},
                "filter_hits_total": {"baseline": hb, "eco": he},
                "rtk_session_projection": {
                    "rows": srows,
                    "standard": s_std,
                    "rtk_claimed": RTK_CLAIMED_TOTAL,
                    "eco": s_eco,
                },
            },
            indent=1,
        )
    )

    for label in EXAMPLE_LABELS:
        m = by_label.get(label)
        if m is None:
            continue
        (examples_dir / f"{label}.before.txt").write_text(m["baseline_text"])
        (examples_dir / f"{label}.after.txt").write_text(m["eco_text"])

    print(md)
    print(f"\nwrote {results_dir}/results.md, results.json, examples/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
