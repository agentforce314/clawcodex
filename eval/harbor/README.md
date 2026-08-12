# Running clawcodex on terminal-bench 2.0 with Harbor

[Harbor](https://github.com/harbor-framework/harbor) is the eval framework
behind terminal-bench. `clawcodex_agent.py` is a Harbor custom agent that
installs `clawcodex-cli` from PyPI inside each task container and runs it
headless (`--print --dangerously-skip-permissions`).

## One-time setup

```bash
# Harbor CLI on the host (needs a running Docker daemon)
uv tool install harbor

# DeepSeek API key (or pass any other provider key clawcodex supports)
export DEEPSEEK_API_KEY=sk-...
```

If `docker pull` hangs on your machine (Docker Desktop's
`"credsStore": "desktop"` credential helper can wedge, stalling even
anonymous pulls of public images), the durable fix is to switch
`~/.docker/config.json` to the direct keychain helper — with empty
`auths` there is nothing to migrate:

```bash
# in ~/.docker/config.json:  "credsStore": "desktop"  ->  "osxkeychain"
```

Non-invasive alternative (leaves your Docker config untouched): point
`DOCKER_CONFIG` at a helper-free config for the eval only:

```bash
mkdir -p ~/.docker-nocreds
echo '{}' > ~/.docker-nocreds/config.json
ln -sfn ~/.docker/cli-plugins ~/.docker-nocreds/cli-plugins  # keep compose v2
export DOCKER_CONFIG=~/.docker-nocreds
```

## Evaluate ALL terminal-bench 2.1 tasks

Terminal-bench 2.1 ([harbor-framework/terminal-bench-2-1](https://github.com/harbor-framework/terminal-bench-2-1))
is the verified iteration of 2.0 — same 89 tasks, 26 of them fixed for
bugs, timeouts/resources, and reward-hacking robustness. It resolves from
Harbor Hub under an org-qualified name (no `@version`). From the repo root:

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 4
```

NOTE: hub datasets namespace task names — filters must match the full
name: `-i 'terminal-bench/fix-git'` (or use a glob: `-i '*fix-git*'`).

## Evaluate with claude-opus-5 on a Claude subscription

Uses your Claude Pro/Max subscription (OAuth) instead of an API key.
One-time prerequisite on the host: `clawcodex login` (writes
`~/.clawcodex/anthropic-oauth.json`). Before each trial the adapter
checks that file and refreshes it when under 30 minutes of runway;
containers receive a refresh-token-free copy on a path outside the
bind-mounted `/logs` tree, so no credential ever enters the jobs
directory. When an `effort` kwarg is set it is also seeded into the
container's settings so subagents inherit it (the `--effort` flag alone
covers only the main loop).

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model anthropic/claude-opus-5 \
  --ak subscription=true \
  --ak effort=high \
  --ak source=git+https://github.com/agentforce314/clawcodex@main \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 2
```

Notes:
- Official Terminal-Bench submissions may not modify task timeouts or
  resources. Keep Harbor's default timeout policy for leaderboard-comparable
  runs; use timeout multipliers only for explicitly labeled diagnostics.
- `effort=high` maps to `clawcodex --effort high` →
  `output_config.effort` on effort-capable models (Opus 5, Opus 4.6/4.8,
  Sonnet 4.6, Fable 5). Requires clawcodex > 1.2.1 in the container —
  until the next PyPI release, keep the
  `--ak source=git+https://github.com/agentforce314/clawcodex@main` above.
- **claude-opus-5 needs a clawcodex that registers it** (the model tables
  in `src/models/configs.py`, `src/services/pricing.py`, and the three
  gates in `src/query/query.py`). On a build without it, opus-5 falls off
  the adaptive-thinking allowlist and the request carries
  `thinking={"type": "enabled", "budget_tokens": …}` — removed on Opus 5,
  so **every request 400s** — while `--effort` is silently dropped and the
  context window is assumed to be 200K instead of 1M (early compaction,
  the handicap #730 removed for opus-4-8). PyPI 1.2.1 predates this, so
  `source=` is required; verify the branch/SHA you point it at actually
  carries the registration before starting a full run, and prefer a
  commit SHA over `@main` for anything you plan to compare later.
- Subscription rate limits are shared with your interactive Claude
  usage — keep `--n-concurrent` low (2-4) and consider
  `--max-retries 2 --retry-include ApiRateLimitError`.
- In subscription mode the adapter does NOT forward `ANTHROPIC_API_KEY`
  (inside clawcodex an API key would take precedence and bill the API).

## Compare against the LATEST official Claude Code

`claude_code_subscription.py` wraps Harbor's own `claude-code` agent
(which bootstrap-installs the latest official CLI inside each container)
with the same per-trial subscription-token handling the other adapters
use: every trial starts with ≥30 min of access-token runway (above the
900s task timeouts; a single trial >30 min could still outlive its
token), so multi-hour jobs work without a manually exported token:

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent claude_code_subscription:ClaudeCodeSubscription \
  --model anthropic/claude-opus-5 \
  --ak reasoning_effort=high \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 2
```

Notes:
- Effort uses the parent agent's kwarg name: `--ak reasoning_effort=`
  (low|medium|high|xhigh|max).
- The model is forwarded as the `ANTHROPIC_MODEL` env var with the
  provider prefix stripped (`harbor/agents/installed/claude_code.py`
  :1372-1386 — so don't expect a `--model` on the exec line when
  debugging), and effort as the `--effort` CLI flag. Neither is gated on
  a model table anywhere in the adapter, and the bootstrap installs the
  latest official CLI in every container, so moving this arm to a new
  model is the model string alone — no adapter or clawcodex change.
- Pin the CLI to a leaderboard row's version with `--ak version=2.1.205`
  (default: latest). A pin predating a model's support will fail on that
  model, so re-check the pin when changing `--model`.
- `CLAUDE_FORCE_OAUTH` is set by the wrapper, so a host
  `ANTHROPIC_API_KEY` can never silently take over and bill the API.
- `--ak subprocess_env_scrub=true` enables the CLI's subprocess-env scrub
  — only on images with bubblewrap (modern claude-code hard-fails
  without `bwrap`; default off matches stock harbor/leaderboard runs).

## Compare against openclaude (the vendored TS Claude Code)

`openclaude_agent.py` runs the old TypeScript implementation at
`<repo>/typescript` through the same harness, for apples-to-apples
comparisons with clawcodex. It uploads the host-built bundle into each
container plus an `npm install` of its 7 unbundled runtime externals
(native sharp/ripgrep binaries must match the container platform; the
ripgrep postinstall downloads from GitHub, so containers need egress).
Build the bundle once first:

```bash
cd typescript && bun run build   # produces dist/cli.mjs
```

Then (same subscription + effort semantics as the clawcodex adapter;
`--provider anthropic` is always pinned because the any-LLM fork would
otherwise auto-route to whatever provider credentials it detects):

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent openclaude_agent:OpenClaude \
  --model anthropic/claude-opus-4-8 \
  --ak subscription=true \
  --ak effort=high \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 2
```

Notes:
- `typescript/` is gitignored and absent in worktrees — run from the main
  checkout, or point `OPENCLAUDE_DIST` / `--ak dist=` at a built
  `cli.mjs`.
- Subscription auth is env-only (`CLAUDE_CODE_OAUTH_TOKEN` access token,
  no refresh token, no credential file in the container); the same
  30-min-runway host refresh as the clawcodex adapter applies.
- This openclaude snapshot (0.24.0) knows claude-opus-4-8 but **not
  claude-opus-5**, and on opus-5 it does not merely degrade — it breaks.
  `modelSupportsAdaptiveThinking` (`typescript/src/utils/thinking.ts:159`)
  allowlists only opus-4-8/4-7/4-6 and sonnet-4-6, then excludes anything
  else matching `opus`, so opus-5 falls to the `budget_tokens` branch at
  `typescript/src/services/api/claude.ts:1731` — and `budget_tokens` is
  removed on Opus 5, i.e. **HTTP 400 on every request** (the same failure
  the clawcodex adapter's `source=` note describes). Model metadata is
  missing too, so the context window falls back to the 200K default
  (`typescript/src/utils/context.ts:17`). Keep this arm on
  `anthropic/claude-opus-4-8` until `typescript/` is refreshed to a
  snapshot carrying opus-5.
- Its effort ladder is low|medium|high|xhigh|max as of 0.24.0.

## Compare against pi (the harness DeepSeek documents)

`pi_agent.py` runs [pi](https://pi.dev) — the harness DeepSeek points at for
its V4 models — through this same harness. pi is installed from npm inside
each container; the adapter also uploads `pi_assets/tb-tools.ts`, which adds
`vision_analyze` and `websearch` to pi's four built-in tools (read, bash,
edit, write). That closes the two capability gaps that would otherwise make
some tasks impossible — it does **not** equalise the surfaces, since pi runs
6 tools against clawcodex's much larger registry.

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent pi_agent:Pi \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs \
  --job-name pi-tb21-full \
  --ak thinking=max \
  -n 4
```

`--ak thinking=max` lines pi up with a clawcodex run at `--ak effort=max`:
both land on DeepSeek `reasoning_effort: "max"`. Needs `DEEPSEEK_API_KEY`
plus `OPENAI_API_KEY` (vision) and `TAVILY_API_KEY` (search).

See `RUN_PI_TB21.md` for the probed wire facts — in particular that published
pi 0.84.1 sends `max_completion_tokens`, which DeepSeek silently ignores, so
stock pi runs with no output cap.

## Evaluate ALL terminal-bench 2.0 tasks

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench@2.0 \
  --agent clawcodex_agent:Clawcodex \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 4
```

Results land in `eval/harbor/jobs/<job-name>/` (`result.json` has the
aggregate accuracy; each trial dir has the agent's stream-json log under
`agent/clawcodex.txt` plus session JSONLs under `agent/sessions/`).

## Useful variations

```bash
# A subset of tasks (repeatable glob filter) — good for smoke tests
  -i fix-git -i openssl-selfsigned-cert          # terminal-bench@2.0
  -i 'terminal-bench/fix-git'                    # hub datasets (2.1)

# First N tasks only
  --n-tasks 5

# Retry provider blips
  --max-retries 2 --retry-include ApiRateLimitError

# Agent kwargs
  --ak max_turns=100        # clawcodex --max-turns (default 300)
  --ak effort=high          # clawcodex --effort (low|medium|high|xhigh|max)
                            # xhigh is model-dependent (opus-5/opus-4-8 yes,
                            # sonnet-4-6/opus-4-6 no → degraded to high)
  --ak version=1.2.1        # pin the clawcodex-cli PyPI version
  --ak source=git+https://github.com/agentforce314/clawcodex@main
                            # install from git instead of PyPI (unreleased code)
  --ak source=dist/clawcodex_cli-1.4.0-py3-none-any.whl
                            # a local wheel: uploaded into each container and
                            # installed there, so a working tree can be
                            # benchmarked without pushing (uv build --wheel)
  --ak subscription=true    # Claude Pro/Max OAuth instead of ANTHROPIC_API_KEY

# Pass the key explicitly instead of exporting it
  --ae DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}"

# Other models/providers (Harbor convention: provider/model)
  --model deepseek/deepseek-v4-pro
  --model anthropic/claude-opus-4-8   # the previous tb2.1 baseline
  --model anthropic/claude-opus-4-5   # needs ANTHROPIC_API_KEY
```

## Measuring prefix-cache efficiency

`prefix_cache_probe.py` answers "how many tokens is each request re-sending?",
which is the number that actually moves cost on DeepSeek. Aggregate hit rate
hides the failure mode: a harness can sit at 90% while re-billing the same
multi-thousand-token block every single turn.

```bash
# 1. Record — wraps any clawcodex invocation, capturing every wire payload
python eval/harbor/prefix_cache_probe.py record --out /tmp/pl -- \
  --print --dangerously-skip-permissions \
  --model deepseek-v4-flash --provider deepseek -- "your task"

# 2. Analyse — diff consecutive requests, attribute the misses
python eval/harbor/prefix_cache_probe.py analyse --out /tmp/pl
```

`analyse` prints, per consecutive pair, the longest common message prefix and
the bytes that had to be recomputed, next to the provider's own
`cached_tokens`. A healthy session diverges only at the append point. Anything
re-sent every turn (the DeepSeek REQUEST-scope tail) shows up immediately.

Reference points, terminal-bench 2.1 on deepseek-v4-flash: Reasonix 98.24% hit
/ ~1,295 miss tokens per request; clawcodex ~1,600-3,400 after the tail split
(~6,764 before it).

## Notes

- The model name uses Harbor's `provider/model` form; the adapter splits it
  into clawcodex's `--provider` / `--model` flags.
- Task containers run as root; the adapter sets `IS_SANDBOX=1`, which is
  clawcodex's sanctioned way to allow `--dangerously-skip-permissions` under
  root inside sandboxes (same pattern Harbor uses for Claude Code).
- The adapter bootstraps `uv` + a managed CPython 3.13 in each container, so
  task images need no preinstalled Python.
- `harbor view eval/harbor/jobs/<job-name>` serves a local results browser
  (the trajectory pane stays empty — this adapter doesn't emit ATIF; read
  `agent/clawcodex.txt` for the full stream-json trajectory).
