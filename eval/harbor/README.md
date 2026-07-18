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

## Evaluate with claude-opus-4-8 on a Claude subscription

Uses your Claude Pro/Max subscription (OAuth) instead of an API key.
One-time prerequisite on the host: `clawcodex login` (writes
`~/.clawcodex/anthropic-oauth.json`; the adapter refreshes it
automatically before each trial and never copies it into the synced
jobs directory).

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model anthropic/claude-opus-4-8 \
  --ak subscription=true \
  --ak effort=high \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 2
```

Notes:
- `effort=high` maps to `clawcodex --effort high` →
  `output_config.effort` on effort-capable models (Opus 4.6/4.8,
  Sonnet 4.6, Fable 5). Requires clawcodex > 1.2.1 in the container —
  until the next PyPI release, add
  `--ak source=git+https://github.com/agentforce314/clawcodex@main`.
- Subscription rate limits are shared with your interactive Claude
  usage — keep `--n-concurrent` low (2-4) and consider
  `--max-retries 2 --retry-include ApiRateLimitError`.
- In subscription mode the adapter does NOT forward `ANTHROPIC_API_KEY`
  (inside clawcodex an API key would take precedence and bill the API).

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
                            # xhigh is model-dependent (opus-4-8 yes,
                            # sonnet-4-6/opus-4-6 no → degraded to high)
  --ak version=1.2.1        # pin the clawcodex-cli PyPI version
  --ak source=git+https://github.com/agentforce314/clawcodex@main
                            # install from git instead of PyPI (unreleased code)
  --ak subscription=true    # Claude Pro/Max OAuth instead of ANTHROPIC_API_KEY

# Pass the key explicitly instead of exporting it
  --ae DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}"

# Other models/providers (Harbor convention: provider/model)
  --model deepseek/deepseek-v4-pro
  --model anthropic/claude-opus-4-5   # needs ANTHROPIC_API_KEY
```

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
