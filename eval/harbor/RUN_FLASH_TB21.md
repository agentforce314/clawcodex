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
| per-request `max_tokens` | 16384 (explicit values clamped to 2x) | `CLAWCODEX_DEEPSEEK_MAX_OUTPUT` (0 = off) |
| reasoning fuse: length-truncated + nothing actionable → retry once with thinking disabled; a length-truncated JSON-repaired TRAILING tool call is dropped rather than executed | on | `CLAWCODEX_DEEPSEEK_FUSE=0` |
| sticky thinking-off after N consecutive fuse trips (resets on any non-burn; cleared by `/clear`) | 0 (off) — headless arms 3 | `CLAWCODEX_DEEPSEEK_FUSE_STICKY` |
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

## Measurement discipline

- Same-code replicates swing hard on this bench (k=1 proves nothing);
  judge on the failure-mode subset first, then a full run, and re-run
  flipped tasks before believing them.
- Exclude cold-start requests when comparing cache behaviour
  (`prefix_cache_probe.py`), and check `docker ps` for competing
  containers before attributing timeouts.
- Baseline jobs for comparison live in the MAIN checkout's
  `eval/harbor/jobs/`; this worktree's runs land in the worktree's.
