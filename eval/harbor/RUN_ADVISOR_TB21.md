# Running an ADVISOR pairing on terminal-bench 2.1

A cheap API-key worker consulting a premium subscription reviewer:
`openai/gpt-5.6-luna` at effort `xhigh` as the main loop, with
`anthropic:claude-opus-5` at effort `xhigh` as the advisor.

The advisor tool forwards the whole conversation so far to a stronger
model and feeds its critique back as a tool result. In client-side mode
that is a **separate API call per consultation**, so the reviewer's
provider is independent of the worker's.

## Prerequisites

### 1. A build containing the advisor fixes

Containers install clawcodex from PyPI or from a git ref. The published
package predates this work, so the run **must** pin a source:

```bash
export CX_SOURCE=git+https://github.com/agentforce314/clawcodex@feat/advisor-subscription-and-effort
```

Before this branch the advisor was unusable in exactly this configuration:

| Defect | Symptom |
|---|---|
| `system` sent as a string | Premium Anthropic models over subscription rejected every consultation with a **mislabelled** `429 rate_limit_error` — it reads as capacity, not shape |
| No thinking / effort on either wire | The reviewer ran with thinking off at the API default |
| Flat `max_tokens=4096` | Thinking draws from the same budget → `stop_reason=max_tokens`, "Advisor returned no text content" |
| No retry | One transient 429/5xx ended the consultation |
| Key read only from `config.json` | An advisor provider whose key lives in an env var got `api_key=""` → "Missing credentials" |
| Adapter forwarded only the main provider's key | Advisor at a different vendor had no credentials at all |

### 2. Host subscription credentials

`subscription=true` reads `~/.clawcodex/anthropic-oauth.json` on the host
and refreshes it when under 2h of runway, injecting a refresh-token-free
copy per container. **That file must exist**:

```bash
clawcodex login          # → anthropic → subscription
```

A token imported from the Claude Code keychain is *not* sufficient for a
long run: the adapter refreshes below the runway threshold, which needs a
real `refresh_token`.

> Subscription rate limits are **shared with interactive Claude usage**.
> A wide `--n-concurrent` competes with your own session; the advisor now
> retries transient 429s three times, but sustained saturation still ends
> individual consultations (the worker continues without advice).

## Smoke first

```bash
cd /Users/ericlee2/workspace/clawcodex
export OPENAI_API_KEY=$(python3 -c \
  "import json,os;print(json.load(open(os.path.expanduser('~/.clawcodex/config.json')))['providers']['openai']['api_key'])")

PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model openai/gpt-5.6-luna \
  --ak source=$CX_SOURCE \
  --ak effort=xhigh \
  --ak subscription=true \
  --ak advisor=anthropic:claude-opus-5 \
  --ak advisor_effort=xhigh \
  --jobs-dir eval/harbor/jobs --job-name smoke-advisor-opus5 \
  -i 'terminal-bench/fix-git' --n-concurrent 1
```

**Always verify the advisor actually answered.** A failed consultation
degrades quietly — the worker carries on and can still score 1.0, so the
reward alone will not tell you:

```bash
python3 - <<'PY'
import json, pathlib
d = sorted(pathlib.Path("eval/harbor/jobs/smoke-advisor-opus5").glob("*/agent/clawcodex.txt"))
for p in d:
    calls = answered = 0
    for ln in p.read_text().splitlines():
        try: ev = json.loads(ln)
        except Exception: continue
        if ev.get("type") == "tool_use" and ev.get("name") == "advisor":
            calls += 1
        if ev.get("type") == "tool_result" and "Advisor unavailable" not in (ev.get("output") or ""):
            answered += ("Gaps" in (ev.get("output") or ""))
    print(f"{p.parts[-3]}: advisor calls={calls} answered={answered}")
PY
```

## Full run (89 tasks)

```bash
cd /Users/ericlee2/workspace/clawcodex
export OPENAI_API_KEY=$(python3 -c \
  "import json,os;print(json.load(open(os.path.expanduser('~/.clawcodex/config.json')))['providers']['openai']['api_key'])")
export CX_SOURCE=git+https://github.com/agentforce314/clawcodex@feat/advisor-subscription-and-effort

PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model openai/gpt-5.6-luna \
  --ak source=$CX_SOURCE \
  --ak effort=xhigh \
  --ak subscription=true \
  --ak advisor=anthropic:claude-opus-5 \
  --ak advisor_effort=xhigh \
  --jobs-dir eval/harbor/jobs \
  --job-name tb21-luna-xhigh-advisor-opus5 \
  --n-concurrent 4
```

### The control run you need for a comparison

The advisor's whole point is the delta it produces, and that is only
readable against the same worker with no reviewer:

```bash
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model openai/gpt-5.6-luna \
  --ak source=$CX_SOURCE --ak effort=xhigh \
  --jobs-dir eval/harbor/jobs \
  --job-name tb21-luna-xhigh-noadvisor \
  --n-concurrent 4
```

Compare on the **shared scored subset**, never whole-run means — task
difficulty varies enormously and an unequal denominator has inverted
conclusions here before:

```bash
python3 eval/harbor/compare_trajectories.py \
  eval/harbor/jobs/tb21-luna-xhigh-advisor-opus5 \
  eval/harbor/jobs/tb21-luna-xhigh-noadvisor
```

## Cost note

The advisor roughly **doubles API calls on turns where it fires**, and
each consultation forwards the entire conversation so far — cost grows
with conversation length, not with the size of the advice. The worker
side is cheap ($0.10/$0.60 per Mtok, doubling above 272K prompt tokens);
the reviewer side bills against the subscription (reported as $0 with an
`estimated_cost_usd` computed from list price for observability).

## Tuning

* `--ak advisor_effort=` — the reviewer's own level, independent of the
  worker's `--ak effort=`. Omit to inherit `effort`; omit both and the
  API applies its model default. `xhigh` is accepted on Opus 5 and
  clamped to `high` on models that reject it (e.g. Sonnet 4.6).
* `--ak advisor=<provider>:<model>` — any configured provider. An
  API-key reviewer (`--ak advisor=zai:glm-5.2`) needs no subscription and
  no `clawcodex login`; its provider key is forwarded automatically.
* Drop `--ak subscription=true` if you point the advisor at an
  API-key Anthropic account instead.

## Verified

| | |
|---|---|
| Wire shape | `claude-opus-5` + `thinking={"type":"adaptive"}` + `output_config={"effort":"xhigh"}` + block-list `system` over subscription OAuth — 200, `billing_mode: subscription` |
| Local e2e | luna worker (API, xhigh) → opus-5 advisor (subscription, xhigh), advisor called twice, real advice both times, worker acted on it |
| Container e2e | tb2.1 `fix-git`, advisor calls=3 answered=3, reward 1.0, 0 exceptions (reviewer `zai:glm-5.2`, since host OAuth was not provisioned on this machine) |
