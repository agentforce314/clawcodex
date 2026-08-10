# Tuning the harness for `deepseek/deepseek-v4-flash` on terminal-bench 2.1

Campaign notes for the DeepSeek-flash harness profile (branch
`feat/deepseek-flash-harness`). Baseline: `jobs/tb21-flash-visiontool`
(0.554 over 74 scored tasks) vs `jobs/tb21-clawcodex-3` (opus-5, 0.784 on
the same 74). DeepSeek claims >0.8 on this bench with their unpublished
harness (flowtivity.ai/blog/deepseek-v4-flash-agent-benchmarks — no
scaffold details disclosed; the one hint is native Responses-API support).

## What the baseline trajectories showed

Failure classes over the 23 tasks where flash < opus:

| class | tasks | mechanism |
|---|---|---|
| reasoning runaway → timeout | 13 | 80-95% of output tokens are `reasoning_content`; single requests truncate at the server's 131,072 output cap as **100% reasoning, zero content** (`polyglot-rust-c`: the entire 900s budget on ONE request). Observed decode speed 90-145 tok/s. `max_tokens` does not discipline reasoning — flash reasons until truncated — but `thinking: {"type": "disabled"}` returns an action in seconds. |
| completed-but-wrong | ~8 | success claimed without verifying against the actual spec; turns wasted writing persistent memories inside ephemeral containers. |
| fat requests | all | 40 tool schemas (~47K chars) + 36.5K-char system prompt (14.6K memory doctrine) on a wire with no `tool_reference` deferral; `# Non-Interactive Mode` never reached headless. |

Notable: per-request output volume is FLAT across turn index (median
~0.4-1K, p95 8K, p99 26K) — the blowups are early-request outliers, so a
per-request cap + fuse beats any turn-indexed effort decay.

## The profile (all deepseek-gated, all env-tunable)

| lever | default | env |
|---|---|---|
| per-request `max_tokens` | 40960 (explicit values clamped to 2x) | `CLAWCODEX_DEEPSEEK_MAX_OUTPUT` (0 = off) |
| reasoning fuse: length-truncated + nothing actionable → retry once with thinking disabled; a length-truncated JSON-repaired TRAILING tool call is dropped rather than executed | on | `CLAWCODEX_DEEPSEEK_FUSE=0` |
| sticky thinking-off after N consecutive fuse trips (resets on any non-burn; cleared by `/clear`) | **0 (off) everywhere** — see subset finding below | `CLAWCODEX_DEEPSEEK_FUSE_STICKY` |
| headless core-tool profile (14 tools = the observed working set) | **opt-in**; the harbor adapter sets it for deepseek trials | `CLAWCODEX_DEEPSEEK_CORE_TOOLS=1` |
| `# Working Style` prompt section (act > deliberate, verify before done) | on | `CLAWCODEX_DEEPSEEK_PROMPT=0` |
| memory systems off in trial containers | adapter-set | (harbor adapter `_build_env` / seeded settings) |

Supporting fix outside the profile: the OpenAI-compat wire's
`finish_reason="length"` now normalizes onto the internal `max_tokens`
stop-reason vocabulary (`query.py`), so capped truncations engage the
loop's existing escalation (64K, clamped to 32K here) and "resume" recovery
lanes — previously they fell through as normal end-of-turn on EVERY
OpenAI-compatible provider.

Wire effect on a trivial headless request: 86.9K chars → 50.8K (-41%),
registry 44 tools → 14 on the wire (15 with a configured advisor), system
prompt 36.5K → 24.5K chars, `max_tokens: 16384` and `# Working Style` /
`# Non-Interactive Mode` present; container-side init confirms the same 14.

## Running

```bash
cd <worktree>
uv build --wheel        # local wheel — benchmark without pushing

export DEEPSEEK_API_KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.clawcodex/config.json')))['providers']['deepseek']['api_key'])")
export OPENAI_API_KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.clawcodex/config.json')))['providers']['openai']['api_key'])")

PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model deepseek/deepseek-v4-flash \
  --ak effort=max \
  --ak vision=openai:gpt-5.6-luna \
  --ak source=$PWD/dist/clawcodex_cli-1.5.0-py3-none-any.whl \
  --jobs-dir $PWD/eval/harbor/jobs \
  --job-name <name> -n 4 -q -y \
  -i 'terminal-bench/<task>' [...]
```

Keep `effort=max` + `vision=openai:gpt-5.6-luna` identical to the baseline
job so only the harness differs.

## Results (2026-08-09)

### Broad measurement — 64 of 74 tasks on the tuned harness

Aggregated across all 40K-cap tuned runs (per-task average where a task was
measured more than once; `eval/harbor/aggregate_flash_runs.py`):

| | baseline | tuned |
|---|---|---|
| mean reward (64 common tasks) | 0.562 | **0.695** |
| delta | — | **+0.133** (+13 tasks up, −4 down) |

**>0.8 is mathematically excluded.** Reaching 0.8 needs 59.2/74 passes. The
64 measured tasks already fix the pass count at 44.5; even if all 10
unmeasured tasks scored a perfect 1.0, the full-74 max is
(64×0.695 + 10)/74 = **0.736 < 0.8**. Realistic full-74 (the 10 at their
baseline mean 0.5) ≈ **0.669**. So the honest harness gain is
**0.554 → ~0.67**, ~+0.11, closing ~40% of the gap to opus-5's 0.81 — and
no outcome on the remainder can reach DeepSeek's claimed >0.8, which relies
on their unpublished, model-co-designed harness.

The 10 unmeasured tasks (caffe-cifar-10, regex-chess, mcmc-sampling-stan,
install-windows-3.11, fix-ocaml-gc, make-mips-interpreter, make-doom-for-mips,
train-fasttext, circuit-fibsqrt, path-tracing-reverse) have single-task
runtimes (25-58 min) that exceed this machine's ~29-min background-task
lifetime, so their orchestrator is killed before they verify — an
environment limit, not the harness. Baseline mean of those 10 is 0.5 (5
passes); none are reasoning-runaway tasks the changes target. Re-run on a
host without the background-task cap to close the grid.

Tasks fixed (baseline 0 → tuned ≥0.5), several the reasoning-runaway
deaths: `polyglot-rust-c` (the canonical 131K-token death → 14 steps),
`model-extraction-relu-logits`, `configure-git-webserver`,
`qemu-alpine-ssh`, `qemu-startup`, `torch-pipeline-parallelism`,
`extract-elf`, `chess-best-move`, `large-scale-text-editing`,
`overfull-hbox`, `password-recovery`. The 3 "down" are all k=1 variance,
not harness faults: `kv-store-grpc` and `sqlite-with-gcov` average 0.5
(pass in some runs), `dna-insert` reproduces the passing baseline's exact
analysis and fails only on fiddly primer-output (Bash+Read only, no
cap/fuse/trim involvement).

### Hard-subset check (the 12 tasks most affected by runaway)

`flash-h2-subsetA`: baseline **0.250 → 0.500**, **+3 / −0**. Mechanism
check, not the headline; deliberately the hardest tasks.

### 17 tasks not measured here — environmental limit, not the harness

The eval orchestrator (`harbor run` as a background task) is killed by this
environment after an erratic, shrinking window (~110 min first, then
25→22→9 min), orphaning any in-flight container. Tasks whose single-task
runtime exceeds that window — `caffe-cifar-10` (~58 m), `regex-chess`
(~52 m), `mcmc-sampling-stan`, `install-windows-3.11`, `compile-compcert`,
`fix-ocaml-gc`, `make-mips-interpreter`, `make-doom-for-mips`,
`rstan-to-pystan`, `sam-cell-seg`, `train-fasttext` — plus a few hang-prone
stragglers (`crack-7z-hash`, `protein-assembly`, `circuit-fibsqrt`,
`path-tracing-reverse`, `db-wal-recovery`) could not be collected. Their
baseline mean is 0.471 (8/17 baseline passes); none are reasoning-runaway
tasks the changes target. Re-run them on a host without the background-task
lifetime cap, in small batches, to complete the full-74 grid.

## Subset-A finding (2026-08-09) — why the cap is 40K and sticky is off

A deliberately adversarial 12-task subset (the hardest reasoning-runaway
tasks) run at the 16K cap + headless-armed sticky came out **net negative**:
+1 fixed (extract-elf), −2 regressed (feal-linear-cryptanalysis,
sanitize-git-repo), 9 same. The lesson, not the number, is the point (this
subset is not representative of the full 74 — it excludes every task the
harness already passes):

- **feal-linear-cryptanalysis (1.0 → 0.0)**: legitimate reasoning bursts of
  p90 21,732 / max 37,071 tokens. The 16K cap truncated them, the fuse
  tripped 4×, sticky disabled thinking, and a task whose *work is the
  reasoning* could no longer do it. → cap raised to 40,960 (above its max);
  sticky no longer armed. The per-request retry (fires only on a
  nothing-actionable truncation, thinking back on next turn) keeps the
  pathological-runaway protection without the permanent loss.
- **sanitize-git-repo (1.0 → 0.0)**: "keep deliberation short" read as "do
  fewer sweeps"; it missed a third contaminated file. → Working Style now
  says sweeps/verification are cheap actions to run freely.
- The persistent timeouts (gcode, raman, model-extraction, polyglot,
  dna-assembly) fail whether or not the cap is present — flash simply does
  not solve them in budget; the cap converts one giant request into several
  smaller ones without changing the outcome. Do not read those as
  cap-induced regressions, but do not claim the cap fixes them either.

Takeaway: the cap is a **runaway backstop sized above real reasoning**, not
a reasoning throttle; the fuse *retry* is the load-bearing win; sticky is
too blunt for reasoning-heavy work and is opt-in only.

## Levers investigated and rejected (why ~0.66 is the harness ceiling)

After the runaway fix, per-trajectory analysis shows the residual failures
are model-capability-bound, and the remaining harness levers don't address
them:

- **Verification / advisor.** clawcodex's advisor is a MODEL-INVOKED tool,
  not a completion gate — a confidently-wrong model won't call it. The code
  itself documents it as ~4x cost and net-negative on some of these tasks
  (`utils/advisor.py`: "4.1x the work, for a worse outcome"; the
  raman-fitting reviewer asserting a wrong peak). Would also worsen the
  timeout bucket. Rejected.

- **DeepSeek Responses API** (the blog's one scaffold hint). Probed live
  2026-08-09: `api.deepseek.com/v1/responses` works — reasoning items,
  function-calling with `call_id`, `store:true/false`, usage with
  `reasoning_tokens` — BUT returns **no `encrypted_content`** even with
  `store:false` + `include:["reasoning.encrypted_content"]`. So the
  client-side encrypted-reasoning replay that gives OpenAI's o-series its
  cross-turn reasoning persistence is not available; reasoning is ephemeral,
  same as chat.completions. Server-side `previous_response_id` chaining is at
  best equivalent to resending history (which the harness already does).
  Net: the Responses path offers no multi-turn advantage for this model and
  doesn't touch vision / speed / blind-spot failures. Not implemented.

- **Vision preprocessing** (implemented + measured). Added a Working Style
  directive to transform images (crop/rotate/upscale/threshold via PIL/cv2)
  before re-querying vision, targeting `gcode-to-text`'s 10 no-preprocess
  retries. Measured `flash-vision2`: the directive WORKS — the model now
  preprocesses (gcode 0→12 preprocess Bash calls, extract-moves→16) instead
  of blindly re-reading — but both tasks still fail (gpt-5.6-luna can't read
  the rotated 3D-printed text / video frames even from cleaned crops, and
  the extra work worsens the timeout); `chess-best-move` (the easier vision
  task) stays 1.0. Direct proof the vision bucket is vision-MODEL-limited,
  not harness-limited. Kept the directive (correct behavior, helps easier
  vision tasks) but it does not move these.

- **Residual failure modes are capability, not harness** (verified):
  vision (above), slow-reasoning timeouts (`raman`/`largest-eigenval`/
  `dna-assembly` run the full 900 s at 90-145 tok/s — failing faster only
  yields wrong-answer-0), and false-confidence wrong answers
  (`cancel-async-tasks` wrote its OWN passing test; the hidden verifier
  wanted different semantics).

**Bottom line:** matching opus-5 (0.81) with deepseek-v4-flash via clawcodex
harness changes is not achievable; DeepSeek's claimed >0.8 relies on an
unpublished, model-co-designed harness. The honest gain from harness work
alone is ~+0.10 (0.554 → ~0.656).

## Measurement discipline

- Same-code replicates swing hard on this bench (k=1 proves nothing);
  judge on the failure-mode subset first, then a full run, and re-run
  flipped tasks before believing them.
- Exclude cold-start requests when comparing cache behaviour
  (`prefix_cache_probe.py`), and check `docker ps` for competing
  containers before attributing timeouts.
- Baseline jobs for comparison live in the MAIN checkout's
  `eval/harbor/jobs/`; this worktree's runs land in the worktree's.
