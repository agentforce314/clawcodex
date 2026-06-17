# Eval — clawcodex vs openclaude on SWE-bench

This directory drives a side-by-side comparison of **clawcodex** (this repo)
and **openclaude** (the TypeScript reference) against
[SWE-bench](https://swe-bench.github.io). The goal is to confirm parity:
when both agents are pointed at the same backing model, the same dataset, and
the same prompts, do they resolve the same set of GitHub issues?

The agent-side wrappers and prediction generation live with the SWE-bench
harness in `SWE-bench-dev/scripts/`. The comparison logic and the driver that
ties everything together live here.

## Layout

```text
SWE-bench-dev/scripts/                   # in the SWE-bench-dev repo
├── clawcodex_api_server.py              # FastAPI wrapper around `clawcodex -p ...`
├── openclaude_api_server.py             # FastAPI wrapper around `node dist/cli.mjs -p ...`
└── run_custom_api.py                    # generic dataset → HTTP → predictions.jsonl

eval/                                    # in this repo (committed on feat/eval)
├── run_compare.py                       # one-command driver: prepare → run → compare
├── compare_results.py                   # standalone summary diff
├── README.md                            # this file
└── runs/                                # gitignored output (per-run)
    └── compare-YYYYMMDD-HHMMSS/
        ├── clawcodex_preds.jsonl
        ├── openclaude_preds.jsonl
        ├── clawcodex_server.log
        ├── openclaude_server.log
        ├── clawcodex_harness.log
        ├── openclaude_harness.log
        ├── only_clawcodex.txt           # instance ids only clawcodex solved
        ├── only_openclaude.txt          # instance ids only openclaude solved
        ├── both_solved.txt
        └── comparison.md                # the headline report
```

## Prerequisites (one-time)

1. **Sibling repos.** Clone `openclaude` and `SWE-bench-dev` next to `clawcodex`:

   ```bash
   git clone https://github.com/Gitlawb/openclaude.git
   git clone https://github.com/swe-bench/SWE-bench.git SWE-bench-dev
   ```

   (Or set `OPENCLAUDE_REPO` / `SWEBENCH_REPO` to wherever you keep them.)

2. **Docker** is installed and `docker ps` works.

3. **SWE-bench venv** (per `SWE-bench-dev/clawcodex_test.md` §2.1):

   ```bash
   cd SWE-bench-dev
   python3 -m venv .venv && source .venv/bin/activate
   pip install -U pip
   pip install -e .
   pip install fastapi uvicorn tiktoken transformers
   ```

   Point the driver at this interpreter via `SWEBENCH_PYTHON`:

   ```bash
   export SWEBENCH_PYTHON=/abs/path/to/SWE-bench-dev/.venv/bin/python
   ```

4. **clawcodex configured** for the model you want to compare (`clawcodex login`).

5. **openclaude provider env** for the same backing model. For `gpt-4o`:

   ```bash
   export CLAUDE_CODE_USE_OPENAI=1
   export OPENAI_API_KEY=sk-...
   export OPENAI_MODEL=gpt-4o
   ```

   For DeepSeek through the OpenAI-compatible path:

   ```bash
   export CLAUDE_CODE_USE_OPENAI=1
   export OPENAI_API_KEY=sk-deepseek-...
   export OPENAI_BASE_URL=https://api.deepseek.com/v1   # the driver also sets this
   export OPENAI_MODEL=deepseek-v4-pro
   ```

   The driver's `--provider deepseek` preset will pass `OPENAI_BASE_URL` and
   `OPENAI_MODEL` per request, so the env exports for those two are optional;
   `OPENAI_API_KEY` always travels via the environment.

### Python that can import `swebench`

`prepare` runs `python -m swebench.inference.make_datasets.create_text_dataset`, so the
interpreter chosen by `SWEBENCH_PYTHON` (or your default `python3` / `python`) must
have SWE-bench installed. Typical setups:

**Option A — install into the clawcodex venv** (one interpreter for everything):

```bash
cd clawcodex
# Prefer the venv binary so you are not using the Windows Store `python3` shim.
uv pip install -e ./SWE-bench-dev fastapi uvicorn tiktoken transformers
# Optional: `run_compare` defaults to sys.executable, so after `activate` you
# can omit SWEBENCH_PYTHON when you launch with that same `python`.
```

If you see **`cannot import swebench`** while the error suggests
`...\WindowsApps\python3.EXE`, you never installed `swebench` into *that* stub.
Use `.venv/Scripts/python.exe eval/run_compare.py ...` or set
`export SWEBENCH_PYTHON="$PWD/.venv/Scripts/python.exe"`.

**Option B — separate SWE-bench venv** (matches `clawcodex_test.md`):

```bash
cd SWE-bench-dev
python -m venv .venv
# Windows Git Bash:
source .venv/Scripts/activate
pip install -U pip
pip install -e .
pip install fastapi uvicorn tiktoken transformers
export SWEBENCH_PYTHON="$PWD/.venv/Scripts/python.exe"
```

Without this, `prepare` will fail with `No module named 'swebench'`.

### `bun not found on PATH` during `prepare`

OpenClaude’s build script expects [Bun](https://bun.com). Either install it, or
only build the SWE-bench dataset for now:

```bash
python eval/run_compare.py prepare --skip-openclaude-build
```

Then install Bun and run `bun install && bun run build` inside `openclaude/`, or
re-run `prepare` without `--skip-openclaude-build` once `bun` is on your `PATH`.
On Windows, Git Bash sometimes does not see Bun until you add
`~/.bun/bin` (or the path the installer prints) to `PATH`.

### `UnicodeDecodeError: 'gbk' codec can't decode...` during `prepare`

On Chinese (and some other) Windows locales, the default text encoding is GBK.
SWE-bench’s dataset builder was opening cloned source as “system default”, which
breaks on UTF-8 files. This repo’s `SWE-bench-dev` fork reads those paths as
UTF-8. Re-run `prepare` after pulling the latest `create_instance.py` changes.

### Windows: `No module named 'resource'`

The stdlib `resource` module exists only on Unix. Upstream SWE-bench imported it
unconditionally in `prepare_images.py`, which breaks `import swebench` on Windows.
This repo’s `SWE-bench-dev` fork patches that import so dataset prep and imports
work on Windows. Docker-based evaluation still requires Docker Desktop; if you hit
other POSIX-only code paths, use WSL2 for the harness.

## Quickest path to a result

```bash
# 1. Build openclaude and the SWE-bench text dataset (only needed once).
python eval/run_compare.py prepare

# 2. Smoke run: 1 known instance, both agents, gpt-4o, full Docker harness.
python eval/run_compare.py run --scope smoke

# 3. Open the report.
ls eval/runs/                      # find the latest compare-* directory
cat eval/runs/compare-*/comparison.md
```

A smoke run takes a few minutes per instance. Scaling up:

```bash
# Pick your own instances:
python eval/run_compare.py run \
    --scope instances \
    --instance-ids astropy__astropy-12907,django__django-11099

# Or the full 300-instance Lite split (takes hours, costs real money):
python eval/run_compare.py run --scope all
```

### Picking the model for both agents

The `--provider` preset sets the model and per-agent provider routing in one
flag. Available presets:

| Preset | Model | clawcodex routing | openclaude routing |
|---|---|---|---|
| `openai` (default) | `gpt-4o` | `--provider openai` | OpenAI native |
| `deepseek` | `deepseek-v4-pro` | `--provider deepseek` | OpenAI-compatible (`https://api.deepseek.com/v1`) |
| `anthropic` | `claude-sonnet-4-6` | `--provider anthropic` | Anthropic native |
| `zai` | `glm-5.2` | `--provider zai` | OpenAI-compatible (`https://api.z.ai/api/coding/paas/v4`) |

Examples:

```bash
# DeepSeek v4-pro on both:
python eval/run_compare.py run --scope smoke --provider deepseek

# DeepSeek but a different model name:
python eval/run_compare.py run --scope smoke --provider deepseek --model deepseek-coder-v4

# Custom OpenAI-compatible endpoint:
python eval/run_compare.py run --scope smoke \
    --provider openai --model my-finetune \
    --openclaude-base-url https://my-gateway.example.com/v1

# Run only one of the two agents (sometimes useful when iterating):
python eval/run_compare.py run --agents openclaude --provider deepseek
```

Per-field overrides (`--model`, `--clawcodex-provider`,
`--openclaude-provider`, `--openclaude-base-url`) layer on top of the preset.

## What `run` actually does (sequentially per agent)

For each agent in `--agents` (default `clawcodex,openclaude`):

1. **Spawn its API server** (`uvicorn scripts.<agent>_api_server:app`) on its
   port (8000 for clawcodex, 8001 for openclaude). Logs go to
   `eval/runs/<id>/<agent>_server.log`.
2. **Wait for `/health`** to come up. Falls back to a TCP-only liveness check
   if the wrapper doesn't expose `/health` yet.
3. **Generate predictions** by invoking
   `SWE-bench-dev/scripts/run_custom_api.py` against the local `/generate`
   endpoint. Writes `<agent>_preds.jsonl`.
4. **Stop the server.**
5. **Run the Docker harness** (`swebench.harness.run_evaluation`) on those
   predictions. Writes the harness summary into the SWE-bench repo as
   `<agent>-local.<run-id>.json`.

After both agents are done:

6. **Diff the two summary jsons** via `compare_results.py` and write
   `comparison.md` plus `only_<agent>.txt` triage lists.

## Just compare two existing runs

If you already have two harness summary jsons lying around:

```bash
python eval/compare_results.py \
    --left  /path/to/clawcodex-local.run-001.json  --left-label  clawcodex \
    --right /path/to/openclaude-local.run-001.json --right-label openclaude \
    --out   eval/runs/manual/comparison.md
```

`run_compare.py compare` is a thin alias for the same call.

## Tunables worth knowing

| Flag | Default | Notes |
|---|---|---|
| `--scope` | `smoke` | `smoke` / `instances` / `all` (full split) |
| `--provider` | `openai` | Preset: `openai` / `deepseek` / `anthropic` / `zai` |
| `--model` | (preset's default) | Override the preset's model name |
| `--clawcodex-provider` | (preset) | Override clawcodex `--provider` flag |
| `--openclaude-provider` | (preset) | Override openclaude routing hint |
| `--openclaude-base-url` | (preset) | Override `OPENAI_BASE_URL` for openclaude |
| `--max-turns` | `30` | Per-instance agent turn cap |
| `--request-timeout` | `1800` | HTTP timeout per instance, seconds |
| `--max-patch-retries` | `2` | Re-prompt when extracted diff is invalid |
| `--max-workers` | `1` | Docker harness parallelism (raise carefully) |
| `--skip-harness` | off | Generate predictions only — useful when iterating on prompts |
| `--agents` | `clawcodex,openclaude` | Drop one to run only the other |

## Troubleshooting

- **`Text dataset not found`** — You skipped `prepare`, or it never finished. Run
  `python eval/run_compare.py prepare` (from the clawcodex repo) after installing
  `swebench` into the interpreter `SWEBENCH_PYTHON` points at (see **Python that
  can import `swebench`** above). On Windows Git Bash you can use
  `.venv/Scripts/python.exe eval/run_compare.py prepare`.

- **`text dataset not found at .../datasets/SWE-bench__SWE-bench_Lite__style-3__fs-oracle`** —
  Same as above: run `prepare` first, or pass `--dataset-local` if you keep the dataset elsewhere.

- **`OPENCLAUDE_REPO set but dist/cli.mjs not found`** — `prepare` should
  build it for you. If it doesn't, `cd openclaude && bun install && bun run build`.

- **`No module named 'src'` from clawcodex** — the wrapper's fallback path
  needs `CLAWCODEX_REPO` set. The driver passes it automatically; if you're
  running the wrapper by hand, follow `SWE-bench-dev/clawcodex_test.md` §2.2.

- **One agent blew up but the other was fine** — the run still produces a
  half-finished comparison. Check `eval/runs/<id>/<agent>_server.log` and
  `<agent>_harness.log`.

- **Patch apply errors (`Only garbage was found in the patch input`)** —
  usually the model returned prose instead of a unified diff. The patch-retry
  loop in `run_custom_api.py` already handles this; if it persists, lower
  `--max-turns` or pick a model with stronger tool/diff fidelity.

## See also

- `SWE-bench-dev/clawcodex_test.md` — manual reproduction of every step the
  driver automates, plus the original error-recovery cookbook.
- `SWE-bench-dev/swebench/harness/reporting.py` — defines the summary JSON
  shape that `compare_results.py` reads.
