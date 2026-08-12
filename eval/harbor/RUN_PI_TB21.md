# Running pi on terminal-bench 2.1 with DeepSeek

[pi](https://pi.dev) (`@earendil-works/pi-coding-agent`) is the harness
DeepSeek documents for its V4 models
([api-docs.deepseek.com](https://api-docs.deepseek.com/quick_start/agent_integrations/pi_mono/)).
`pi_agent.py` runs it through the same Harbor harness as `clawcodex_agent.py`
and `openclaude_agent.py`, so all three are directly comparable.

## One-time setup

```bash
uv tool install harbor            # host CLI, needs a running Docker daemon

export DEEPSEEK_API_KEY=sk-...    # the model under test
export OPENAI_API_KEY=sk-...      # vision_analyze (gpt-5.6-luna)
export TAVILY_API_KEY=tvly-...    # websearch
```

If `docker pull` hangs, see the credential-helper note in `README.md`.

Nothing needs to be installed for pi itself: the adapter bootstraps Node >= 22
and `npm install -g @earendil-works/pi-coding-agent@0.84.1` inside each task
container.

## Tools

pi ships **four** built-in tools: `read`, `bash`, `edit`, `write`. Terminal-bench
2.1 has tasks that need to look at an image and tasks that need the live web,
and DeepSeek V4 is text-only, so `pi_assets/tb-tools.ts` is uploaded and loaded
with `-e`. It registers two more, with the same argument shapes as clawcodex's
equivalents so trajectories stay comparable:

| Tool | Arguments | Backend |
|------|-----------|---------|
| `vision_analyze` | `image_url` (local path), `question` | OpenAI chat/completions, `PI_VISION_MODEL` (default `gpt-5.6-luna`) |
| `websearch` | `query`, `allowed_domains?`, `blocked_domains?` | Tavily `/search` |

`--ak tools=off` runs stock pi with its four built-ins only.

This closes the two capability gaps; it does **not** equalise the harnesses.
pi runs 6 tools against clawcodex's much larger registry, and that difference
is part of what the benchmark measures — don't describe the run as
"same tools".

Both tools have been exercised in a container: `websearch` on the first smoke
run, `vision_analyze` on `code-from-image` (one call, task scored 1.0).

## Comparability caveats

* **Project trust is off by default** (`--no-approve`). AGENTS.md / CLAUDE.md
  load either way; trust additionally enables `.pi/settings.json`,
  `.pi/extensions`, `.pi/skills` and `.pi/SYSTEM.md`, which would let task
  content replace the system prompt or run its own extension code and
  silently change what is being measured. `--ak trust_project=true` opts in.
* **`PI_OFFLINE=1` is set**, which also pins pi to the model catalogue bundled
  with 0.84.1 (no catalogue refresh). That is deliberate: it keeps runs
  reproducible and keeps the exact-model-id `modelOverrides` assumption valid.
* **The npm pin is real**: the published package ships `npm-shrinkwrap.json`,
  so `@0.84.1` pins the whole transitive tree. Node is only pinned on the
  tarball fallback — images shipping their own Node >= 22 use theirs, so the
  JS runtime is not identical across all 74 tasks (same caveat as
  `openclaude_agent.py`).
* **Credentials are readable by the agent.** pi's bash tool inherits the
  process environment, and tool results are serialized into `pi.jsonl` and the
  session JSONL under the host-synced trial directory. pi has no equivalent of
  Claude Code's `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`, so use benchmark-scoped
  keys.

## Wire facts (probed against pi 0.84.1, 2026-08-11)

Captured by pointing pi's `deepseek` provider at a local recording endpoint
(`provider === "deepseek"` is enough to trigger pi's DeepSeek auto-detection,
so the real code path runs even against localhost).

**Thinking.** `--thinking` maps through pi's catalogue entry:

| `--thinking` | wire |
|---|---|
| `off` | `thinking: {"type":"disabled"}`, no `reasoning_effort` |
| `minimal` / `low` / `medium` / `high` | `thinking: {"type":"enabled"}` + `reasoning_effort: "high"` |
| `xhigh` / `max` | `thinking: {"type":"enabled"}` + `reasoning_effort: "max"` |

pi's default is `medium`, i.e. `reasoning_effort: "high"`. DeepSeek's `low`
level is not reachable through pi.

**Output cap — the important one.** Published pi 0.84.1 sends
`max_completion_tokens`, not `max_tokens`, because `isDeepSeek` is missing from
the `useMaxTokens` chain in that build (the git tree has since fixed it).
Verified directly against the DeepSeek API:

```
max_tokens: 16            -> completion_tokens=16   finish_reason=length
max_completion_tokens: 16 -> completion_tokens=456  finish_reason=stop
```

DeepSeek honours `max_tokens` and **silently ignores `max_completion_tokens`**.
So stock pi runs with **no effective output cap** and inherits the server
default (131,072). That is the reasoning-runaway condition measured on this
benchmark with `deepseek-v4-flash`: single requests that stream 100%
`reasoning_content`, emit no content and no tool call, and burn a whole task
budget at the observed 90-145 tok/s. Expect runaway timeouts in a stock run;
that is a real property of the released harness, not an adapter bug.

`--ak output_cap=40960` writes a `models.json` `modelOverrides` entry pinning
`compat.maxTokensField: "max_tokens"` plus `maxTokens`, giving pi a cap DeepSeek
actually honours. **Off by default** — the headline run should measure pi as a
user following DeepSeek's own docs would get it.

**Note on DeepSeek's documented config.** The `models.json` on DeepSeek's page
puts `reasoningEffortMap` inside `compat`. pi's schema has no such key: the
effort map is `thinkingLevelMap` and it lives at the *model* level, not inside
`compat`. The documented key is therefore inert. It happens not to matter,
because pi's built-in catalogue already carries the correct
`thinkingLevelMap` and auto-detects `thinkingFormat: "deepseek"` and
`requiresReasoningContentOnAssistantMessages` from the provider id — so you can
skip the docs' `models.json` entirely.

**Request shape.** A trivial first request is ~6.0 KB total: 2,685-char system
prompt, 3,023 bytes of tool JSON for the four built-ins.

**`--mode json` always exits 0.** `runPrintMode` only sets `exitCode = 1`
inside its `mode === "text"` branch, and pi's `StreamFn` contract forbids
throwing on request failures — they arrive as an assistant message with
`stopReason: "error"`. Left alone, an expired key or a 429 storm at task 40 of
74 would look exactly like 34 legitimately failed tasks: reward 0.0, **zero
exceptions**, plausible token counts, and harbor's `--retry-include
ApiRateLimitError` never firing. The adapter therefore inspects the event
stream after the run and raises a classified `ApiError` on a terminal
`stopReason` of `error`/`aborted`. If you fork this adapter, keep that check.

## Smoke test

```bash
cd /path/to/worktree
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent pi_agent:Pi \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs \
  --job-name pi-smoke-1 \
  -i '*prove-plus-comm*' -i '*sqlite-db-truncate*' \
  -n 2
```

Add `--install-only` to validate the container bootstrap alone (~3 min).

Validated 2026-08-11 (pi 0.84.1, `deepseek/deepseek-v4-flash`, default
thinking), **2/2 reward 1.0, 0 exceptions, 6m57s**:

| task | reward | input | cached | output | cost |
|---|---|---|---|---|---|
| prove-plus-comm | 1.0 | 15,476 | 12,928 (83.5%) | 1,715 | $0.00087 |
| sqlite-db-truncate | 1.0 | 309,939 | 288,768 (93.2%) | 31,934 | $0.01271 |

Tools exercised across the two trials: `bash` x14, `write` x3, `read` x1,
`websearch` x1 — the extension loads and executes inside the container, not
just on the host.

A second run after the failure-detection changes (`set -eu`, `--no-approve`,
post-run `stopReason` check) confirmed no regression and exercised the vision
path — **2/2 reward 1.0, 0 exceptions, 4m48s**:

| task | reward | input | cached | output | cost | tools |
|---|---|---|---|---|---|---|
| code-from-image | 1.0 | 12,164 | 11,648 (95.8%) | 785 | $0.00032 | `read`, **`vision_analyze`**, `bash`, `write` |
| prove-plus-comm | 1.0 | 57,688 | 56,320 (97.6%) | 6,993 | $0.00231 | `read` x3, `bash` x5, `edit` x2 |

## Full run

Matched to the clawcodex baseline (`--ak effort=max`,
`vision=openai:gpt-5.6-luna`) so the harness comparison is apples-to-apples —
both sides sit at DeepSeek `reasoning_effort: "max"` with the same vision model:

```bash
cd /path/to/worktree
export DEEPSEEK_API_KEY=sk-... OPENAI_API_KEY=sk-... TAVILY_API_KEY=tvly-...

PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent pi_agent:Pi \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs \
  --job-name pi-tb21-full \
  --ak thinking=max \
  --ak vision_model=gpt-5.6-luna \
  -n 4
```

For pi at its own default effort (`reasoning_effort: "high"`), drop
`--ak thinking=max`. For a capped variant that removes the runaway failure mode,
add `--ak output_cap=40960` and use a distinct `--job-name`.

Hub datasets namespace task names, so filters must match the full name:
`-i 'terminal-bench/fix-git'` or a glob `-i '*fix-git*'`.

## Batching on this Mac

A `harbor run` launched as a background task here is killed after an erratic,
shrinking window (~110 min on the first run, then 25 → 22 → 9 min), orphaning
in-flight containers. Practical recipe, unchanged from the flash runs:

* Small batches (<= 4 tasks) of sub-~16-minute tasks, each with a **fresh**
  `--job-name` — Harbor refuses to resume a job whose task set differs.
* Killed batches still bank their fast finishers; re-run to recover stragglers.
* Aggregate across job dirs afterwards rather than expecting one complete job.
* Tasks whose single-task runtime exceeds the window (caffe ~58 m, regex-chess
  ~52 m) are simply unmeasurable here and need a host without the cap.

## Reading results

```bash
harbor view eval/harbor/jobs
```

Per-trial pi output is `agent/pi.jsonl` (pi's `--mode json` event stream) and
session JSONL under `agent/pi-sessions/`. The adapter backfills
`n_input_tokens` / `n_cache_tokens` / `n_output_tokens` / `cost_usd` from the
`message_end` events, matching clawcodex's convention that `n_input_tokens` is
the full prompt side and the cached part is also reported separately.
