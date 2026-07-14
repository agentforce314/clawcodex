# Eval — `/eco` token-compression benchmark

This directory reproduces, with **measurements**, the experiment behind
[RTK](https://github.com/rtk-ai/rtk)'s README savings table. RTK models a
30-minute agent session and *estimates* per-operation token savings
("Estimates based on medium-sized TypeScript/Rust projects"). Here the same
class of operations is **captured live and counted**: every number in the
ClawCodex README's `/eco` section comes from replaying real command outputs
through the production compression pipeline.

## Layout

```text
eval/eco/
├── capture_corpus.py   # runs real commands, stores full outputs (stdlib-only)
├── measure.py          # replays corpus through src/eco, counts tokens
├── corpus/             # gitignored — machine-specific captured outputs
└── results/            # committed — results.md, results.json, examples/
```

## Method

1. **Capture** (`capture_corpus.py`). 27 operations an agent actually runs:
   - *Test runners*: `pytest` / `pytest -v` (failing + green), `go test [-v] ./...`
     (failing), `npx jest` (failing + green), plus a green slice of this repo's own
     suite. Failing runs come from small sample projects written into `--workdir`
     with genuine bugs (off-by-one, in-place mutation, wrong error message) so the
     tracebacks, `--- FAIL` blocks, and assertion diffs are real tool output —
     RTK's own "never synthetic" fixture rule.
   - *Package managers*: `npm install jest`, `pip install flask` into a fresh venv
     (`--no-cache-dir` so the Collecting/Downloading ceremony is real).
   - *Git*: `clone --no-local --progress` of this repo, dirty + clean `git status`,
     `commit`, `push --progress` to a local bare remote.
   - *Repo-scale reads*: `git log` (full + oneline), `git diff v1.0.0..v1.1.0`,
     `ls -R src`, `find`, `cat` a 900-line file, two `grep -rn` sweeps (one under,
     one over the 400-line head-cap threshold).
   - *Logs*: `log show --last 90s --style syslog` (macOS unified log; ~35k
     lines, varies by run).
   - *Small outputs*: `docker ps`, `wc -l`, clean `git status` — passthrough rows
     that must come back byte-identical.

2. **Measure** (`measure.py`). Each item is replayed through the *production*
   path, byte-for-byte what the Bash tool does when `/eco` is on:

   ```python
   baseline = _assemble_bash_body(truncate_output(stdout), truncate_output(stderr))
   outcome  = compress_bash_output(command, exit_code, full_text, baseline, tee_dir)
   ```

   `baseline` is exactly the model-bound text without eco (including the
   pre-existing 30k-char truncation), and both renderings get the mapper's
   `returnCodeInterpretation` suffix, so the counted text is the wire content.
   The eco text includes the recovery-hint line, so its token cost is charged
   honestly — for the committed run, the bench tee path tokenizes no *shorter*
   than a typical production `~/.clawcodex/...` hint, so its published savings
   are, if anything, slightly understated. Tokens are tiktoken `cl100k_base` counts (RTK
   reports chars/4 estimates; we compute those too, into `results.json`, but
   headline numbers are real tokenizer counts). The never-worse invariant is
   asserted over the whole corpus, and tee-filename entropy (`time_ns`, pid)
   is pinned so reruns on an unchanged corpus are byte-identical.

3. **Project** — `results.md` also recomputes RTK's 30-minute-session table
   with our measured ratios: rows whose assumed per-call size sits below eco's
   thresholds (a ~200-token `ls`, a ~2,000-token `cat`) map to 0% because eco
   passes them through; `git add/commit/push` applies the measured push ratio
   to the push share (3 of 8 calls) only. That projection is deliberately
   conservative — the corpus table is the measured result.

## Reproduce

```bash
python3 eval/eco/capture_corpus.py --workdir /tmp/eco-bench
.venv/bin/python eval/eco/measure.py       # needs tiktoken (repo venv has it)
```

`capture_corpus.py` needs `git`, and captures what it can find: `go`, `npm`,
`docker` (daemon up), and macOS `log` rows are skipped when unavailable.
`--python` points at a python that can `import pytest` (defaults to the repo
venv). Outputs are machine-specific — sizes and therefore percentages will
vary a few points from the committed `results/` run; the invariants (never
worse, failures survive) hold everywhere.

## Caveats, honestly

- The corpus is **fat-tail weighted** by design: it includes the big outputs
  (repo-scale listings, a failing suite, a system log) that dominate real
  context spend, alongside 8 passthrough rows. The per-operation table is the
  primary result; the -80% corpus total describes this mix, not every session.
- The `git clone` / `git push` rows are captured **with `--progress`** (as
  agents often run them for visibility); a plain non-TTY push emits far less
  ceremony. Those two rows contribute ~13k baseline tokens — dropping both
  still leaves the corpus total around -77%.
- `cargo test` isn't captured (no local Rust toolchain); the RTK-model
  projection maps its `cargo test / npm test` row to the measured jest ratio.
- Percentages for the same operation differ run to run (system-log volume,
  npm registry noise, repo growth) — regenerate before quoting new numbers.
