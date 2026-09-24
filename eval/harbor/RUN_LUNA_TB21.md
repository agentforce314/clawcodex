# Running `openai/gpt-5.6-luna` on terminal-bench 2.1

OpenRouter-proxied GPT-5.6 Luna, at reasoning effort `max`, through the
clawcodex Harbor adapter.

## Model facts (OpenRouter `/models`, fetched 2026-07-31)

| | |
|---|---|
| id | `openai/gpt-5.6-luna` (a `-pro` variant exists with identical specs) |
| context | 1,050,000 advertised; 922K max input (registered as 872,000 since 2026-09-24, the smaller of the API and ChatGPT-subscription input limits; runs past ~822K now compact where older builds overflowed, so such runs are not like-for-like with earlier baselines) |
| max output | 128,000 tokens |
| price | $0.10/M in, $0.60/M out — doubling to $0.20 / $0.90 above 272K prompt tokens |
| reasoning | `reasoning_effort` supported; verified honored, not just accepted — reasoning tokens rise low 148 → medium 154 → high 266 → max 516 on a fixed prompt |
| tools | `tools` + `tool_choice` supported; returns `finish_reason=tool_calls` |
| vision | text + image + file → text |

## Prerequisite: a build that actually sends the effort

Until 2026-07-31, clawcodex emitted reasoning effort **only** on the
Anthropic wire. On the headless `--print` path the adapter drives,
`--effort` was a silent no-op for every OpenAI-compatible provider —
OpenRouter, OpenAI, DeepSeek, Z.AI. The job config would record
`effort=max` and the request body would carry no effort field at all.

Two consequences:

1. Containers must install a clawcodex build dated 2026-07-31 or later, so
   pin `--ak source=git+…@<branch-or-main>` at a commit that includes the
   fix. The PyPI package (1.2.1) does not.
2. Earlier non-Anthropic runs labelled `effort=max` — including the
   `tb21-deepseek-max` job in `jobs/` — actually ran at the provider's
   default effort. Do not compare a new Luna run against those as if the
   effort setting matched.

## Commands

Run from the repo root. `PYTHONPATH` must point at `eval/harbor` so Harbor
can import the adapter in the host process.

```bash
export OPENROUTER_API_KEY=$(python3 -c \
  "import json,os;print(json.load(open(os.path.expanduser('~/.clawcodex/config.json')))['providers']['openrouter']['api_key'])")

export CX_SOURCE=git+https://github.com/agentforce314/clawcodex@main   # must contain the effort fix
```

### Smoke first — two tasks, ~5 minutes

Never start an 89-task run without this; a broken install or a missing key
fails identically on every task and costs an hour to find out.

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model openrouter/openai/gpt-5.6-luna \
  --ak effort=max \
  --ak source=$CX_SOURCE \
  -i 'terminal-bench/fix-git' \
  -i 'terminal-bench/openssl-selfsigned-cert' \
  --job-name smoke-tb21-luna-max \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 2
```

Expect reward 1.0 on both and zero infra errors. Then confirm the effort
really shipped — the whole point of the pin:

```bash
grep -ro '"reasoning_effort": *"[a-z]*"' eval/harbor/jobs/smoke-tb21-luna-max | head
```

### Full run — all 89 tasks

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model openrouter/openai/gpt-5.6-luna \
  --ak effort=max \
  --ak source=$CX_SOURCE \
  --job-name tb21-luna-max \
  --jobs-dir eval/harbor/jobs \
  --n-concurrent 4
```

`-k 5` matches the official leaderboard's pass@5 methodology (the published
Claude Code 0.79 is k=5); the in-repo 3-way comparison numbers — clawcodex
0.58, openclaude 0.551, claude-code 0.719 — are all k=1, so keep k=1 to
compare against those.

### Results

```bash
harbor view eval/harbor/jobs                       # browse trajectories
python3 eval/harbor/compare_trajectories.py …      # NOT ad-hoc counting:
                                                   # a denominator mistake
                                                   # once inverted 4 metrics
```

## Gotchas

- **Model string is doubly-qualified.** Harbor splits `--model` on the FIRST
  slash only, so `openrouter/openai/gpt-5.6-luna` → `--provider openrouter
  --model openai/gpt-5.6-luna`. That is intended; do not "fix" it to a
  single slash.
- **Hub datasets namespace task names.** Include filters need the prefix:
  `-i 'terminal-bench/fix-git'`. A bare `-i fix-git` matches nothing.
- **The key must be in the host environment.** The adapter forwards
  `OPENROUTER_API_KEY` from the host env into the container; it does not
  read `~/.clawcodex/config.json` for the provider key. Hence the `export`
  above. Alternatively pass `--ae OPENROUTER_API_KEY="$OPENROUTER_API_KEY"`.
- **Docker credential helper.** If image pulls hang forever, check
  `~/.docker/config.json` for `credsStore: "desktop"` — a Docker Desktop
  update can rewrite it, and it wedges even anonymous pulls of public
  images. It should be `osxkeychain`.
- **Cost.** Luna is cheap (~$0.10/M in). A full 89-task run at k=1 lands in
  the low single-digit dollars, versus roughly two orders of magnitude more
  for an Opus run.
