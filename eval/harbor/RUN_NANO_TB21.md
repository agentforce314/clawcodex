# Running clawcodex --nano on terminal-bench 2.1

Nano mode (`docs/nano.md`) is clawcodex's pi-shaped minimal profile. The
Harbor adapter forwards it with a single agent kwarg, so any run
documented in `README.md` / `RUN_PI_TB21.md` becomes a nano run by adding
`--ak nano=1`.

## A/B a task (nano vs default), DeepSeek

```bash
export DEEPSEEK_API_KEY=sk-...

# nano
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs-nano \
  --n-concurrent 4 \
  --ak nano=1

# baseline: same command, no --ak nano=1, --jobs-dir eval/harbor/jobs-default
```

Filter to single tasks with `-i 'terminal-bench/fix-git'` (hub datasets
namespace task names). To eval unreleased working-tree changes, build a
wheel and point `source` at it:

```bash
uv build --wheel
...  --ak source=$PWD/dist/clawcodex_cli-<version>-py3-none-any.whl --ak nano=1
```

Then compare with the existing tooling (`compare_results.py`,
`compare_trajectories.py`) across the two jobs dirs.

## Measured (2026-08-15, deepseek-v4-flash, this adapter, k=1)

Identical wheel, only `--ak nano=1` differing:

**`terminal-bench/fix-git`**

| | nano | default | ratio |
|---|---|---|---|
| reward | **1.0** | **1.0** | = |
| input tokens | 54,847 | 282,601 | **5.2× less** |
| cache tokens | 48,896 | 233,344 | 4.8× less |
| output tokens | 2,421 | 9,283 | 3.8× less |
| cost | **$0.00165** | **$0.01015** | **6.2× cheaper** |
| job runtime | 75 s | 128 s | 1.7× faster |

**`terminal-bench/pypi-server`**

| | nano | default | ratio |
|---|---|---|---|
| reward | **1.0** | **1.0** | = |
| input tokens | 111,554 | 300,198 | **2.7× less** |
| output tokens | 4,941 | 7,362 | 1.5× less |
| cost | **$0.00255** | **$0.00849** | **3.3× cheaper** |
| job runtime | 100 s | 117 s | 1.2× faster |

Aggregate: **2/2 pass in both modes; nano $0.0042 vs default $0.0186 —
4.4× cheaper** at equal quality. Nano's fix-git trajectory was 10 focused
Bash calls, zero tool errors.

## Full-suite nano vs pi (89 tasks, deepseek-v4-flash, max thinking, k=1)

Same wheel lineage, pi ran its TB extension (vision+websearch); the nano
arm matched it (`NANO_VISION=openai:gpt-5.6-luna NANO_WEBSEARCH=1`):

| | nano `tb21-nano-flash-max-2` | pi `tb21-pi-flash-max-2` |
|---|---|---|
| solved | **64/89 (71.9%)** | 63/89 (70.8%) |
| cost | **$1.31** | $2.01 |
| runtime | 5h38m | 5h54m |

The pre-fix nano baseline (`tb21-nano-flash-max-1`, no vision/websearch,
before the care/verification guidelines and staleness soft-refresh) was
60/89 at $1.02. Six of the eleven tasks v2 gained are directly
attributable to shipped fixes (vision ×1, websearch ×1, care ×1,
timeout-race mechanics ×3); seven v1 passes flipped red on k=1
variance (build/training-time jitter). At this k, treat 64-vs-63 as
parity-to-slight-edge on score with a durable ~35% cost advantage.

Round 3 (#900 — no Bash timeout ceiling + anti-poll guidance, the
constraint-checklist guideline, `run_in_background` removed from the nano
Bash schema, stuck-command detection), `tb21-nano-flash-max-3`, same model,
vision+websearch: **63/89 (70.8%)**. The draw was 61/87 at $1.04, with two
trials lost to a pre-agent infra failure (curl SSL error fetching the uv
installer, no agent involvement); both passed when re-run, and their cost is
not in the $1.04. It was measured before #900's last commit, the nano-only
tail-keeping truncation with a full-output spill file. Across the three draws
nano's pass@1 is 60 / 64 / 63 (three different builds, the first without
vision or web search); pass@2 70/89, pass@3 73/89 across those builds.

Nano sends six tools and a ~2K-token fixed payload (vs ~17K default), no
per-turn injections, /eco on. A trivial live A/B outside Harbor
(deepseek-v4-pro, write-and-verify, both solved in 4 turns) showed the
same shape: 3,439 vs 27,173 fresh input tokens.

Comparability notes mirror `RUN_PI_TB21.md`: nano has no
vision_analyze/websearch, so image- and web-dependent TB tasks will lose
capability relative to default clawcodex — that difference is part of
what the A/B measures. Don't describe the runs as "same tools".

## Fair nano-vs-pi setup

**Matching a pi run that used the TB extension** (vision_analyze +
websearch — e.g. `jobs/tb21-pi-flash-max-2`, which ran
`vision_model: gpt-5.6-luna` with TAVILY websearch): add
`--ak vision=openai:gpt-5.6-luna --ak websearch=1` to the nano arm
(runner: `NANO_VISION=openai:gpt-5.6-luna NANO_WEBSEARCH=1`) so nano
registers its identically-named `vision_analyze` plus WebSearch. The
2026-08 run analysis found 4–5 of pi's exclusive wins used
vision/websearch (chess-best-move ×3 vision, video-processing ×11+3,
mteb-leaderboard ×2 web, path-tracing…); without the kwargs those tasks
measure a capability gap, not the harness. Stock-vs-stock (no kwargs on
nano, `--ak tools=off` on pi) is the other clean pairing.


For a clean head-to-head against pi, pair **nano** with **stock pi**
(`--ak tools=off`, its native 4-tool surface): both are then text-only
with no web access — capability parity of absence on TB's image/web
tasks. `--ak tools=on` (the default) would grant pi vision_analyze +
websearch that nano lacks. Keep both harnesses at model defaults: pass
no `--ak effort=` / `--ak thinking=` and leave `CLAWCODEX_EFFORT` and
`PI_THINKING` unset. The advisor is structurally off under nano
(query.py's nano gate — settings cannot enable it), and it is never
seeded unless `--ak advisor=` is passed; pass no advisor kwargs. Pin
what runs: nano via a wheel built from the commit under test
(`--ak source=`), pi via its adapter-pinned npm version
(`--ak pi_version=` to override).
