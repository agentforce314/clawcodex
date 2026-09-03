<div align="center">

**English** | [中文](docs/i18n/README_ZH.md) | [Français](docs/i18n/README_FR.md) | [Русский](docs/i18n/README_RU.md) | [हिन्दी](docs/i18n/README_HI.md) | [العربية](docs/i18n/README_AR.md) | [Português](docs/i18n/README_PT.md)

# ClawCodex

**A production-oriented Python rebuild of Claude Code — real architecture, reliable CLI agent**

*Ported from the TypeScript reference implementation and extended with a Python-native runtime*

***

[![GitHub stars](https://img.shields.io/github/stars/agentforce314/clawcodex?style=for-the-badge&logo=github&color=yellow)](https://github.com/agentforce314/clawcodex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/agentforce314/clawcodex?style=for-the-badge&logo=github&color=blue)](https://github.com/agentforce314/clawcodex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)


**🔥 Active Development • New Features Weekly 🔥**

![ClawCodex Screenshot](assets/clawcodex-screenshot-1.png)

</div>

***

<div align="center">

# 🏆 Terminal-Bench 2.1

# **80.9%** on Claude Opus 5 — a top-tier open-source result

### 72 of 89 tasks solved (pass@1) on the verified 89-task suite. On the [public leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.1) that slots **around 3rd** — behind Claude Code / Fable 5 (83.8%) and Codex / GPT-5.5 (83.1%), **ahead of Claude Code on Opus 4.8 (78.9%)**.

ClawCodex — the open-source Python rebuild of Claude Code — run headless on `claude-opus-5` at `effort=xhigh`.
A single run (k=1) against the board's k=5 averages; benchmarked on `main` at #756. Fully reproducible from the
open-source [Harbor adapter](eval/harbor/). **[Read the full breakdown ↓](#-news)**

</div>

***

<div align="center">

# 🌿✂️ /eco Token Compression

# Same session, **80% fewer** Bash-output tokens

### Measured, not estimated: 27 real command outputs replayed through the production pipeline — **92,989 → 17,767 tokens (-80%)**. Failure summaries keep the error lines that matter; anything lossy is teed to disk, one `tail` away.

One toggle, deterministic filters — failure-focused test summaries, `git`/`pip`/`npm` ceremony
stripping, log dedup, recoverable head-caps — guarded to be **never worse** than the raw rendering.
Stacks with the [DeepSeek prefix cache](#-deepseek-prefix-cache): the cache makes your stable
prefix nearly free, `/eco` shrinks the fresh suffix every turn actually pays for.
**[See the measured benchmark ↓](#eco-benchmark)**

</div>

***

<div align="center">

# 🐋🔥 DeepSeek Prefix Cache

# Run long agentic coding sessions for *pennies*

### Cache-hit input bills at **`$0.022` / 1M tokens** — about **450× cheaper** than Claude Fable 5 (`$10` / 1M).

ClawCodex keeps your request prefix **byte-stable**, so DeepSeek's prompt cache covers your whole
`system + tools + history` span across turns. **The longer you code, the more you save.**

<sub>`deepseek-v4-pro` off-peak, checked 2026-08-25. DeepSeek doubles every rate during peak hours
(01:00–04:00 and 06:00–10:00 UTC, Mon–Fri) — still ~227× cheaper there. `/cost` follows the
schedule.</sub>

</div>

***

<div align="center">

# 🐜 `--nano` Mode

# The pi-style minimal harness, built in

### Full Terminal-Bench 2.1, same model, head-to-head with the [pi harness](https://pi.dev) (its own TB setup, vision+websearch matched): **nano 64/89 (71.9%) at $1.31 vs pi 63/89 (70.8%) at $2.01** — equal-or-better score, **35% cheaper**, k=1.

Six tools, a **~2K-token** fixed payload (vs ~16K default), zero per-turn injections, /eco on.
`clawcodex --nano -p "<task>"` ports the pi harness's edit ladder (multi-edit + fuzzy match),
truncation guard, and compaction file-ledger — while non-nano behavior stays byte-identical.
**[docs/nano.md](docs/nano.md)**

</div>

***

## ⚡ Quick Install

**One line** — installs `uv`, Python 3.10+, and puts `clawcodex` on your PATH.

macOS / Linux / WSL / Git Bash:

```bash
curl -fsSL https://clawcodex.app/install.sh | bash
```

Windows (native PowerShell — no WSL needed):

```powershell
irm https://clawcodex.app/install.ps1 | iex
```

Then configure a provider and start coding:

```bash
clawcodex login   # interactive provider + API key setup → ~/.clawcodex/config.json
clawcodex         # start it in any project — Full Access by default, /permissions to change
clawcodex web     # start the Web UI at http://127.0.0.1:8081
```

Both installers ship the same `clawcodex` lifecycle helpers — `doctor` (diagnose your
environment), `verify` (health-check the install), `update`, and `uninstall` — and are
re-run-safe. To pass flags through the pipe:
`curl -fsSL https://clawcodex.app/install.sh | bash -s -- --dry-run` on POSIX, or
`& ([scriptblock]::Create((irm https://clawcodex.app/install.ps1))) -DryRun` on Windows.
Windows prerequisite: [Git for Windows](https://git-scm.com/download/win) — its Git Bash
is what the shell tool runs commands with.

<details>
<summary><b>Or install manually from source</b></summary>

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+
pip install -r requirements.txt

python -m src.cli login   # writes config to ~/.clawcodex/config.json

python -m src.cli   # start it (Full Access by default; /permissions to change)
```

</details>

The configuration file is saved at `~/.clawcodex/config.json`. Minimal example:

```json
{
  "default_provider": "deepseek",
  "providers": {
    "deepseek": {
      "api_key": "xxx-xxx",
      "base_url": "https://api.deepseek.com",
      "default_model": "deepseek-v4-pro"
    }
  },
  "env": {
    "TAVILY_API_KEY": "tvly-YOUR-TAVILY-API-KEY"
  }
}
```

> **Note:** `TAVILY_API_KEY` is required for the WebSearch tool — get a key at [tavily.com](https://tavily.com).

The `session`, `settings`, and `env` blocks are optional — sensible defaults apply when they're omitted. See [Configure](#configure) for the full structure.

***

## 📰 News

- **2026-08-15 (v1.6.0):** **ClawCodex Web — the whole agent in a browser tab (`ui-web/`, `clawcodex web`)** — one command serves the full agent at a stable, typeable address: **`http://127.0.0.1:8081`**. It is deliberately *not* a second server: the browser drives the **same in-process agent, JSON-RPC gateway, and durable session store as the TUI and the Desktop app** — start a session in one surface, resume it in another. The UI is a three-column shell (session tree | conversation | details) with streaming replies and collapsible reasoning, live tool cards (ANSI-colored terminal transcripts, unified diffs, line-numbered file reads), permission approvals as a composer takeover with once/session/always grants, a prompt queue for follow-ups typed mid-turn, slash-command completion, a context meter with per-category breakdown, a To-dos panel folded live from the transcript, and light/dark/system themes. Two views per session: **Chat** is the conversation; **Trajectory** is the same run as a metered ledger — every model request and tool call on a three-lane timeline with per-step tokens and timing (TTFT, throughput, cache-hit rate), model time and tool time reported separately. A **Settings** page covers what used to need slash commands or hand-editing `config.json`: provider **API keys** (add / replace / disconnect, stored where `clawcodex login` stores them), the **default provider** new sessions start on, the approvals default (with a Full-access confirmation that means it), response language, output style, and the end-of-turn recap toggle. `GET /` inlines the session token so the bare URL works after launch — and because of that, `clawcodex web` binds loopback only unless you explicitly pass `--allow-remote`.
- **2026-08-09 (later that day):** **ClawCodex Desktop runs and packages on Windows** — `npm run dist:win:nsis` in `ui-desktop/` now produces a working unsigned NSIS installer (`ClawCodex-<version>-win-x64.exe`, crab icon and version info stamped via rcedit, node-pty **conpty** binaries staged), and `clawcodex desktop` launches the dev app natively. The packaged app boots the same `%USERPROFILE%\.clawcodex\clawcodex` backend that `install.ps1` creates — **one shared install, config, and session store across CLI, TUI, and Desktop**. Verified live on Windows 11: silent NSIS install → app boot → backend spawned from the shared venv → healthy loopback gateway → real DeepSeek turns on the shared keys. Also fixed along the way: the in-app updater's relaunch resolver pointed at the upstream `apps/desktop/` layout (never matching `ui-desktop/`), its bash relaunch handoff now honestly reports manual-restart on Windows, and a hashbang broke vitest collection of the packaging tests.
- **2026-08-09:** **Native Windows support for the CLI** — ClawCodex now runs first-class on Windows 10/11 (PowerShell / cmd / Windows Terminal, no WSL required), installed with one line: `irm https://clawcodex.app/install.ps1 | iex`. The new `install.ps1` mirrors `install.sh` end to end — uv install, Python provisioning, lock-pinned deps, PATH registration, TUI build, plus the same `doctor` / `verify` / `update` / `uninstall` lifecycle. Under the hood the port is structural, not cosmetic: a shell platform layer resolves **Git Bash** for the Bash tool (explicitly refusing the WSL `System32\bash.exe` shim), so shell commands keep their POSIX semantics everywhere; process-tree kills go through `taskkill /T`; the mailbox/transcript/lockfile gain real `msvcrt` locking (the Windows CRT's `O_APPEND` emulation can silently *lose* concurrent writes); the permission layer folds NTFS case-insensitivity and refuses whole-drive grants and drive-relative escapes (deny-side only); and `@`-mentions, `CLAWCODEX.md` `@includes`, and persistent-`cd` tracking all round-trip real `C:\` paths. The full test suite now runs on `windows-latest` alongside Ubuntu in CI.
- **2026-08-08 (v1.5.0):** **ClawCodex Desktop — the whole agent in a native app (#802–#808)** — ClawCodex now ships a real desktop application (`ui-desktop/`): streaming chat with a live tool trail and reasoning, permission approvals with once/session/always grants, a session sidebar that lists and resumes the **same durable sessions as the TUI**, side-by-side previews, settings, and the official pixel-art crab as the dock icon and in-app brand mark. `clawcodex desktop` launches it from a checkout (first run installs the UI deps; `--no-dev` builds once and launches Electron directly). The architecture is the interesting part: the app spawns **`clawcodex serve`** — one loopback port serving `/api/*` REST plus a JSON-RPC WebSocket gateway at `/api/ws` — and sessions run on the **same in-process agent core the TUI uses**, so both surfaces share one config, one session store, one skills set, and one permission system; the wire contract is the TUI's own gateway vocabulary, adapted server-side. The port itself is one of the largest single features ClawCodex has landed: ~310K lines of TypeScript across ~1,500 files brought over from the reference desktop implementation, rebranded end to end, with every quality gate held to the reference's own baseline (typecheck green, lint identical, unit-test failure set byte-identical) and the whole loop verified live — boot → real chat turn → streamed reply rendered in the window. macOS packaging works today (`npm run dist:mac` → DMG/zip via electron-builder, hardened-runtime config in place). Ship-week fixes landed the same day: the root `.gitignore`'s Python-oriented `lib/` pattern had silently kept 180 renderer source files out of the initial import — fresh clones failed at boot until #806; the default UI scale moved from the reference's dense 90% preset to Chromium's 100% actual size (#807); and a second `clawcodex desktop` now gets a friendly "already running" message instead of a vite stack trace (#808).
- **2026-08-02 (v1.4.0):** **Fusion models — give a text-only model vision (#771, #787)** — several strong reasoning models cannot see images at all: `deepseek-v4-pro` rejects an image content block outright (`400 unknown variant \`image_url\``), so pasting a screenshot, `@`-mentioning one, or letting `Read` return one ended the turn. A **fusion model** pairs that base model with a second, vision-capable one — every image is described by the vision model first, and the base model reads the description. `/fusion create <name> <base> <vision>` saves one; it then behaves like a normal model in the `/model` picker, as `--model <name>`, in `-p`, and across restarts. Ported from [claude-code-router](https://ccrdesk.top/en/configuration/fusion-models/)'s Fusion Model concept, with one deliberate difference: CCR is a proxy, so it can only offer vision as a *tool* the model may choose to call — which cannot help a pasted image, already on the wire before the model gets a turn. ClawCodex owns the agent loop, so it substitutes images in place, covering paste, `@file.png`, `Read`, and Bash image output at once. Verified end to end on Terminal-Bench 2.1's `code-from-image` task — transcribing handwritten pseudocode from a PNG and reproducing its output — with `deepseek-v4-flash` + `openai:gpt-5.6-luna` (#787); the base model alone returns a 400 on the same image. **Also in v1.4.0:** GPT-5.6 Sol/Terra/Luna (#773); four more OpenAI-compatible providers — groq, cerebras, baseten, xai — taking the registry to 30 (#784); `/mode` becomes `/permissions` with a three-level picker and Full Access by default (#768); `AskUserQuestion` finally renders a real picker instead of returning JSON to the model (#774); the OpenAI provider now picks its wire protocol from the model rather than the auth mode, which is what makes `gpt-5.6-luna` usable on an API key (#783); cached prompt tokens are billed at the cache rate instead of the full input rate, and OpenRouter's streamed reasoning is no longer discarded (#785, #786); and headless runs stop reporting a cut-short run as a success (#777–#782).
- **2026-07-29 (v1.3.0):** **ClawCodex scores 80.9% on Terminal-Bench 2.1 — a top-tier open-source result on Opus 5 (#720–#725, #747–#754)** — running headless on `claude-opus-5` at `effort=xhigh`, ClawCodex solved **72 of 89** Terminal-Bench 2.1 tasks: **80.9% pass@1** on a single run. On the [public 2.1 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.1) (k=5 averages) that would slot **around third** — behind Claude Code / Fable 5 (83.8%) and Codex / GPT-5.5 (83.1%), statistically level with the 79–80% cluster, and **ahead of Claude Code on Opus 4.8 (78.9%) and Sonnet 5 (74.6%)**. Getting there was open, unglamorous parity work: a Harbor eval adapter (`eval/harbor/`) for three-way ClawCodex-vs-openclaude-vs-Claude-Code runs (#720, #724, #725), then a run of prompt- and reliability-parity fixes — restored task-tool skip conditions and parallel-tool guidance, deferred nonessential initial tools, and recovery of trials lost to empty turns and transport drops (#747–#754). **Also in v1.3.0:** `claude-opus-5` support with an interactive `/effort` fix (#746), bounded persistent memory with a background self-improvement review (#731), a VS Code extension driving the agent-server over stdio (#727), image-paste input with an `[Image #N]` un-attach chip (#761, #762), the `CLAUDE.md → CLAWCODEX.md` context-file rebrand (#732), and transport-retry hardening (#757, #760). Stated plainly: this is a single k=1 pass (binomial 1σ ±4.2pp) against the board's k=5 ± ~1.2pp averages, benchmarked on `main` at #756 (before the v1.3.0 tag), so read it as directional rather than a ranked submission.
- **2026-07-13:** **`/eco` token compression — -80% Bash-output tokens, measured, now a headline (#708, #712)** — a new session toggle compresses the model-bound rendering of every Bash result with deterministic filters ported from [RTK](https://github.com/rtk-ai/rtk)'s method set: failure-focused test summaries (kept error lines are never rewritten), `git`/`pip`/`npm` ceremony stripping, log dedup with `[×N]` counts, and a recoverable head-cap — all behind a **never-worse** guard, with every lossy compression teeing the full output to disk behind a runnable recovery hint (#708). A reproducible benchmark (`eval/eco/`) replays 27 real command outputs through the exact production pipeline and counts real tokenizer tokens: **92,989 → 17,767 (-80%)** corpus-wide, -88% on filter hits, plus an honestly conservative recompute of RTK's own 30-minute-session model (-19% under their averaged assumptions — real sessions are fat-tailed) (#712). Full tables: the [`/eco` section](#eco-benchmark) and [`eval/eco/results/`](eval/eco/results/results.md).
- **2026-07-12 (v1.1.0):** **ClawCodex v1.1.0 — run OpenAI *and* Claude models on your subscription, not metered API billing** — the headline of 1.1.0 is **subscription auth for the two biggest model families**, so you can point ClawCodex at a plan you already pay for. **Sign in with ChatGPT (#698):** `clawcodex login → openai → subscription` (browser, device-code, or import from an existing Codex CLI login) routes requests through the ChatGPT Codex backend's Responses API — `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, and `gpt-5.3-codex-spark` on your Plus/Pro allowance, with encrypted-reasoning replay across turns and **$0** metered cost. **Claude Pro/Max (#697):** `clawcodex login → anthropic → subscription` connects a Claude subscription via OAuth (PKCE) with automatic token refresh and the same $0 accounting; follow-ups repaired the login after Anthropic moved its OAuth endpoints to `platform.claude.com` (#702) and stopped sending adaptive thinking to models that don't support it (#699). A configured API key always wins, and subscription usage reports `billing_mode: subscription`. **More models:** a Meta (`api.meta.ai`) provider with the 1M-context `muse-spark-1.1` reasoning model (#692) and refreshed MiniMax parameters (#696). **Workflow & TUI:** `/plan` mode with implicit plan-mode entry/exit (#676), `--worktree/-w` session isolation for parallel runs in separate git worktrees (#672), the `/memory` picker + `$EDITOR` spawn (#693), config/state directories rebranded `.claude → .clawcodex` with a one-time migration (#678), `/logo` startup color schemes (#677), plus TUI polish — Tab accepts the suggested placeholder (#690), past inputs get the Claude-Code highlight band (#691), clickable agent URLs (#694), and a per-terminal link-open affordance (#701). **Quality:** semantic tool-input coercion with parity validation errors (#700) and looser, Claude-Code-faithful permission granting (#673).
- **2026-07-07:** **`/loop` scheduled tasks now actually fire — full port of Claude Code's session-scoped scheduler (#680)** — the bundled `/loop` skill finally has a real engine behind it: a new `src/scheduled_tasks` module parses standard 5-field cron expressions and fires due prompts **between turns** from the agent-server's idle poll. `CronCreate`/`CronList`/`CronDelete` register real firing jobs (8-char IDs, 50-job cap, deterministic jitter, 7-day recurring expiry with one final fire), and the new **`ScheduleWakeup`** tool drives self-paced `/loop` mode — the model picks each next delay (1 min–1 hr), `stop: true` ends the loop, and a ~20-minute fallback wakeup catches iterations that forget to reschedule. Typed skill slash commands now reach the backend (new `skill_command` control), so `/loop 5m check ci` works from the composer with completion + argument hint; the TUI shows a live countdown indicator (`⟳ loop wakeup in 2m 14s · ⏰ 1 scheduled`) and **Esc while idle stops a waiting loop**. `/clear` drops session tasks, `--resume` restores unexpired ones, `CLAWCODEX_DISABLE_CRON=1` disables the scheduler. 117 new tests; verified live over stdio NDJSON and a real PTY TUI drive (typed dispatch → CronCreate → a real wakeup fire between turns → Esc-stop).
- **2026-07-07:** **Bounded the ESC-cancel chunk queue in OpenAI-compatible streaming (#278)** — `OpenAICompatibleProvider.chat_stream_response`'s worker-thread queue (added in #148) was an unbounded `queue.Queue`. A non-graceful disconnect from a proxy that keeps sending bytes after ESC (and never closes the SDK iterator) let the orphaned worker thread accumulate chunks in memory indefinitely. The queue is now capped at 64 chunks, so `put()` blocks the worker once full instead of growing without bound.
- **2026-07-06 (v1.0.0):** **ClawCodex v1.0.0 — the 1.0 release: goal-directed autonomy, hooks & MCP wired for production, and a hardened permission system** — 86 commits since v0.7.0 (#580–#668) finish wiring the big subsystems end-to-end and graduate ClawCodex to 1.0. **Goal-directed autonomy:** the `/goal` + `/subgoal` completion-condition loop keeps the agent working until an LLM judge confirms the goal is actually met (#664), the new Monitor tool streams long-running shell output with backpressure (#665), background-bash completion notifications (#663), coordinator mode wired end-to-end on the live paths (#634), and `/advisor` token-efficient worker/reviewer pairing restored on the Ink TUI (#668). **Hooks live in production:** configured hooks now actually fire — bootstrap Hooks abstraction (#583), UserPromptSubmit (#597), multi-scope + lifecycle hooks (#595), `if` pre-filters (#643), PreToolUse `permissionDecision` (#655), PermissionRequest hooks at the ask seam (#637), MCP elicitation hooks (#659), and teammate TaskCompleted / TeammateIdle stop hooks (#642). **MCP completion:** OAuth server auth via the `/mcp` flow (#662), live `tools/list_changed` refresh (#598, #604), server instructions injected into the system prompt (#654), and `clawcodex mcp serve` re-exposes ClawCodex tools as an MCP stdio server (#635). **Permission hardening:** readable approval boxes with broadenable, persistent session grants (#608–#611), compound-command permission parity (#622), Bash normalization hardening (#626), `disableBypassPermissionsMode` lockdown (#660), an honest refuse-to-start unsandboxed guard (#658), subprocess secret-scrubbing (#650), and a flag-gated LLM security-classifier lane for auto mode (#589). **TUI maturity:** faithful Claude Code look & feel — diff rendering, tool-call transcript, task list, composer + permission-mode badge, busy line (#612–#616) — plus a minimal vim editing engine (#667), Esc-interrupt with a defanged Ctrl+C (#625), fully editable multi-line input (#621), slash-command argument hints (#631), a persistent session-stats line (#657), and restored `/cost`, `/skills`, and `/model` (#627, #629, #630). **Reliability:** the production compaction pipeline is wired and auto-compact actually applies its result (#587, #607), full retry lane + model fallback + message-history caching (#586), parallel Agent fan-out with the concurrency-cap deadlock fixed (#590), killing a background agent really stops the run (#606), and output styles work end-to-end (#640). Codebase stats: 1,170 Python files, **256,909 lines** (up from 233,520 lines on 2026-06-11).
- **2026-06-30 (v0.7.0):** **ClawCodex v0.7.0 — TUI auto-theming, faithful inline rendering & a Claude-Code-style tool trail** — the Ink TUI now detects your terminal's background color (OSC 11) on startup and selects the light/dark theme to match, so text stays readable on any terminal with no env var needed (#577). Inline mode renders *truly* inline like Claude Code: no screen wipe on launch, and no overlap with prior terminal output on startup or with the returning shell prompt on exit (#573, #575). The tool trail reads Claude-style — workspace-relative paths (`Read(src/foo.ts)`), `Grep(pattern)` labels, and a `Read N lines` result collapse (#574) — and the banner gains a 🦞 mascot with brighter secondary text on dark themes (#576).
- **2026-06-23:** **One-click installer** — `curl -fsSL https://clawcodex.app/install.sh | bash` installs uv (no sudo), provisions Python 3.10+, clones to `~/.clawcodex`, creates a lock-pinned venv, and registers `clawcodex` on PATH; ships status / doctor / verify / update / uninstall subcommands, is safe to re-run, and works on macOS / Linux / WSL.
📚 Older items have moved to the full **[News archive](docs/NEWS.md)**.

***

## 🎯 Why ClawCodex?

**ClawCodex** is a **production-oriented Python rebuild of Claude Code**, ported from the **real TypeScript architecture** and shipped as a **working CLI agent**, not just a source dump.

- **Real Agent Runtime** — tool-calling loop, streaming REPL, session history, and multi-turn execution
- **High-Fidelity Port** — keeps the original Claude Code architecture while adapting it to idiomatic Python
- **Built to Hack On** — readable Python codebase, rich tests, and markdown-driven skill extensibility
- **Multi-LLM providers** — the biggest step forward vs. upstream: Claude Code is built around Claude-series models only; ClawCodex is dedicated to wiring in **all major LLM providers** so you can choose the most **flexible** and **cost-effective** stack for agentic coding

**A real Claude Code-style terminal workflow in Python: stream replies, call tools, fetch context, and extend behavior with skills.**

**🚀 Try it now! Fork it, modify it, make it yours! Pull requests welcome!**

***

## 🏆 SWE-bench Verified — `clawcodex` outperforms `openclaude` on the same model

![SWE-bench Verified — clawcodex vs openclaude on Gemini 2.5 Pro](assets/swebench-verified-gemini.png)

On the full **SWE-bench Verified** split (499 instances, the public agent-coding leaderboard), both agents driven by **Gemini 2.5 Pro** under our standardized harness:

| Agent | Resolved | Unresolved | Error |
|---|---:|---:|---:|
| **clawcodex** | **291 / 499 (58.2%)** | 124 | 84 |
| openclaude | 265 / 499 (53.0%) | 144 | 90 |

- ✅ **Both solved**: 241 &nbsp;&nbsp; 🟢 **Only clawcodex**: 50 &nbsp;&nbsp; 🔵 **Only openclaude**: 24 &nbsp;&nbsp; ❌ **Neither**: 184

Reproduce locally — see [`eval/README.md`](eval/README.md) for the full workflow (cumulative batching, `--predict-workers N`, `--capture-traces`).

***

<a id="eco-benchmark"></a>

## 🌿 `/eco` Token Compression — **-80% Bash-output tokens, measured**

Long agentic sessions drown in tool output: failing test logs, `git` progress spam,
2,000-line listings. Toggle **`/eco`** on and ClawCodex compresses the *model-bound
rendering* of every Bash result with deterministic filters ported from
[RTK](https://github.com/rtk-ai/rtk)'s method set — failure-focused test summaries,
command-scoped ceremony stripping, log deduplication with `[×N]` counts, and a
recoverable head-cap — while the full raw output stays on disk behind a runnable
recovery hint. No model in the loop, no command rewriting, nothing to learn.

```text
$ pytest        # 128 lines → 37 lines, 1,347 → 390 tokens (-71%)
Pytest: 5 failed, 29 passed in 0.04s

1. [FAIL] test_unknown_sku_message
   with pytest.raises(OrderError, match="unknown sku 'gold-bar'"):
   >           o.total()
   tests/test_orders.py:34:
   ⋮
5. [FAIL] test_truncate_one
   >       assert truncate_words("alpha beta", 1) == "alpha..."
   E       AssertionError: assert 'alpha beta...' == 'alpha...'
[full output: ~/.clawcodex/<ws>/<session>/eco/1707_pytest.log]
Command failed with exit code 1
```

**Measured, not estimated.** RTK's README models a 30-minute session and *estimates*
-80%. We ran the experiment instead: a 27-operation corpus of **real command
outputs** — failing `pytest`/`go test`/`jest` runs, `pip`/`npm` installs, git
workflows, repo-scale listings, a 34,000-line system log, captured live (RTK's own
"never synthetic" fixture rule) — replayed through the exact production pipeline,
counting tiktoken `cl100k_base` tokens of the model-bound text with `/eco` off vs on:

| Operation | Filter | Raw tokens | `/eco` tokens | Saved |
|---|---|---:|---:|---:|
| `pytest` (failing run) | failure focus | 1,347 | 390 | **-71%** |
| `pytest -v` (failing run) | failure focus | 1,925 | 392 | **-79%** |
| `pytest -v` (green run) | one-line collapse | 359 | 60 | **-83%** |
| `go test -v ./...` (failing run) | failure focus | 527 | 227 | **-56%** |
| `npx jest` (failing run) | failure focus | 444 | 175 | **-60%** |
| `npm install jest` | ceremony strip | 188 | 8 | **-95%** |
| `pip install flask` | ceremony strip | 514 | 85 | **-83%** |
| `git clone --progress` | ceremony strip | 6,868 | 18 | **-99%** |
| `git push --progress` | ceremony strip | 6,458 | 75 | **-98%** |
| `git status` (dirty tree) | advice strip | 143 | 91 | **-36%** |
| `git log -n 300` | recoverable head-cap | 7,714 | 946 | **-87%** |
| `git diff v1.0.0..v1.1.0 -- src` | recoverable head-cap | 7,561 | 748 | **-90%** |
| `ls -R src` | recoverable head-cap | 9,088 | 225 | **-97%** |
| `cat` (900-line file) | recoverable head-cap | 6,833 | 552 | **-91%** |
| `grep -rn 'def ' src/` | recoverable head-cap | 7,582 | 1,219 | **-83%** |
| `log show --last 90s` (34k lines) | log dedup | 10,512 | 1,977 | **-81%** |
| **Whole corpus (27 operations)** | | **92,989** | **17,767** | **-80%** |

The corpus also includes 8 operations that (correctly) pass through **byte-identical**
— a clean `git status`, `docker ps`, `ruff check` findings, a small failing `go test`,
a 370-line `grep` that sits under the head-cap threshold — because `/eco` guarantees
**never worse**: a compression that doesn't beat the raw rendering is discarded.
Full tables: [`eval/eco/results/results.md`](eval/eco/results/results.md).

<details>
<summary><b>vs RTK's own 30-minute-session model</b> (why our headline is honest)</summary>

<br>

RTK *rewrites commands* into its own CLI (`rtk ls`, `rtk read`, `rtk grep`), so every
operation in its session model compresses. `/eco` deliberately compresses **results
only** — the command the model wrote is the command that runs — and small outputs pass
through untouched. Recomputing RTK's session table with our *measured* ratios (0%
where our corpus shows passthrough at RTK's assumed sizes):

| Operation | Freq | Standard | rtk (estimated) | clawcodex `/eco` (measured) |
|---|---:|---:|---:|---:|
| `ls` / `tree` | 10x | 2,000 | 400 | 2,000 (0%) |
| `cat` / read | 20x | 40,000 | 12,000 | 40,000 (0%) |
| `grep` / `rg` | 8x | 16,000 | 3,200 | 16,000 (0%) |
| `git status` | 10x | 3,000 | 600 | 1,908 (-36%) |
| `git diff` | 5x | 10,000 | 2,500 | 10,000 (0%) |
| `git log` | 5x | 2,500 | 500 | 2,500 (0%) |
| `git add/commit/push` | 8x | 1,600 | 120 | 1,007 (-37%) |
| `cargo test` / `npm test` | 5x | 25,000 | 2,500 | 9,850 (-60%) |
| `ruff check` | 3x | 3,000 | 600 | 3,000 (0%) |
| `pytest` | 4x | 8,000 | 800 | 2,320 (-71%) |
| `go test` | 3x | 6,000 | 600 | 6,000 (0%) |
| `docker ps` | 3x | 900 | 180 | 900 (0%) |
| **Total** | | **~118,000** | **~23,900 (-80%)** | **~95,500 (-19%)** |

Under RTK's *averaged* assumptions (every `cat` ≈ 2,000 tokens, every `ls` ≈ 200) the
honest number for a results-only compressor is **-19%** — those mid-size outputs are
exactly what ClawCodex already handles with Read-tool line caps and 30k-char Bash
truncation. But real sessions aren't averages: they're fat-tailed, and one 2,000-line
`git log`, one failing suite, or one `npm install` blows more context than fifty small
commands. `/eco` targets precisely that tail — which is why the measured number on
real outputs is **-80%**, the same figure RTK estimates, with none of the risk of
rewriting commands.

</details>

**The RTK safety rules, kept** (see [`src/eco/`](src/eco/)):

- **Never worse** — every compressed rendering is token-checked against the exact
  baseline it replaces; the baseline wins ties. Worst case is 0% saved, never negative.
- **Failures survive** — error/failure lines are never rewritten, only ceremony drops;
  a green summary with a non-zero exit code is treated as untrusted and passed through.
- **Everything recoverable** — lossy compressions tee the full output to the session
  dir and append a runnable hint (`[see remaining: tail -n +61 …]`); no tee, no compression.
- **Semantics untouched** — exit codes, `is_error`, images, background tasks, and
  interrupted runs are never altered; any filter exception falls back to passthrough.

`/eco status` shows per-filter savings for the session. Compression stacks with the
[DeepSeek prefix cache](#-deepseek-prefix-cache): the cache makes the stable prefix
nearly free, `/eco` shrinks the fresh suffix every turn actually pays for. Reproduce:

```bash
python3 eval/eco/capture_corpus.py --workdir /tmp/eco-bench   # capture real outputs
.venv/bin/python eval/eco/measure.py                          # replay + count tokens
```

***

## ⭐ Star History

[View star history on star-history.com](https://www.star-history.com/?repos=agentforce314%2Fclawcodex&type=date&legend=top-left)

## ✨ Features

### Streaming Agent Experience

```text
>>> /stream on
>>> Explain tests/test_agent_loop.py
[streaming answer...]
• Read (tests/test_agent_loop.py) running...
  ↳ lines 1-180
>>> /render-last
```

- True API streaming for direct replies plus richer streaming during tool-driven agent loops
- Built-in `/stream` toggle for live output and `/render-last` for clean Markdown re-rendering on demand
- Designed for real terminal demos: streaming text, visible tool activity, and stable fallback behavior

### Programmable Skill Runtime

```md
---
description: Explain code with diagrams and analogies
allowed-tools:
  - Read
  - Grep
  - Glob
arguments: [path]
---

Explain the code in $path. Start with an analogy, then draw a diagram.
```

- Markdown-based `SKILL.md` slash commands
- Supports project skills, user skills, named arguments, and tool limits

### Multi-Provider Support

ClawCodex’s main advantage is **multi-provider support**: while Claude Code targets **Claude** models, we aim to support **every major LLM provider** behind the same agent runtime—so you can swap vendors, regions, and price tiers without giving up tools, skills, or the coding loop. That flexibility is what makes agentic coding practical at scale.

```python
providers = [
    # Native / bespoke wire formats
    "anthropic", "minimax", "deepseek", "zai", "openrouter", "openai", "gemini",
    # OpenAI-compatible vendors
    "nvidia-nim", "atlascloud", "wanjie-ark", "volcengine", "xiaomi-mimo",
    "novita", "fireworks", "siliconflow", "siliconflow-cn", "arcee", "moonshot",
    "huggingface", "together", "stepfun", "deepinfra", "meta",
    "groq", "cerebras", "baseten", "xai",
    # Local servers (no API key required)
    "ollama", "vllm", "sglang",
]  # 30 providers; aliases like `nim`, `kimi`, `hf`, `grok` resolve automatically
```

Any new OpenAI-compatible vendor is a one-row addition to
`src/providers/openai_compatible_specs.py` (base URL + default model + API-key
env vars). API keys resolve from config **or** the provider's standard env var
(e.g. `TOGETHER_API_KEY`, `MOONSHOT_API_KEY`), so most providers work without
editing `config.json`.

### Interactive UI (TypeScript Ink TUI)

The interactive UI is the **TypeScript Ink TUI** — a terminal client that spawns and owns a Python **agent-server** child and talks to it over a pipe (NDJSON). Running `clawcodex` with no mode flags launches it; `clawcodex tui` is the explicit form. (The former in-process Rich REPL and Textual TUI were removed in favor of this single, higher-fidelity client.)

```text
> Hello!
Assistant: Hi! I'm ClawCodex, a Python reimplementation...

> /help                       # Show commands
> /theme dark                 # Switch color theme
> @src/cli.py                 # @-mention a file (fuzzy file index)
> /explain-code qsort.py      # Run a SKILL.md skill (or /skill …)

# Needs Node 18+ and a built ui-tui/dist (the installer builds it); `clawcodex -p` is the no-Node headless path.
```

### Web UI (browser)

`clawcodex web` serves the same agent in a browser tab — same backend, same gateway, and the **same durable sessions** as the TUI and Desktop app, with a chat view, a per-request Trajectory view, live tool cards, permission approvals, and a settings page (provider keys, default provider, approvals, language, output style, recap).

```bash
clawcodex web                 # serve + open http://127.0.0.1:8081
clawcodex web --port 9000     # explicit port (taken → clean refusal, not a neighbor)
clawcodex web --no-open       # print the URL without launching a browser
clawcodex web --build         # rebuild ui-web/dist first (needs Node 18+)
```

The default port is stable (**8081**, scanning upward if taken), and `GET /` inlines the session token — so once the server is up, typing `127.0.0.1:8081` into the address bar is enough. The page hands out that token, so the server binds loopback only; `--allow-remote` exists for deployments that put their own auth in front. First run from a source checkout needs the bundle built once: `clawcodex web --build` (the installer builds the TUI, not the web bundle).

### Complete CLI

```bash
clawcodex                       # Interactive Ink TUI (default)
clawcodex tui                   # Interactive Ink TUI (explicit)
clawcodex web                   # Web UI at http://127.0.0.1:8081 (see Web UI section)
clawcodex login                 # Configure API keys (interactive)
clawcodex logout anthropic      # Remove Claude Pro/Max OAuth credentials
clawcodex logout openai         # Remove ChatGPT subscription OAuth credentials
clawcodex config                # Show ~/.clawcodex/config.json-backed settings
clawcodex --version             # Version string

# Non-interactive / scripting (pipes, CI, agents)
clawcodex -p "Summarize src/cli.py"
clawcodex -p "Hello" --output-format json
clawcodex -p --output-format stream-json --input-format stream-json < events.ndjson

# Overrides for a single run
clawcodex --provider anthropic --model claude-sonnet-4-6 -p "Hi"
clawcodex --max-turns 10 --allowed-tools Read,Grep -p "Find TODOs"

# Permission control (TUI and -p both honor these)
clawcodex --permission-mode plan                       # plan / default / acceptEdits / dontAsk
clawcodex --dangerously-skip-permissions -p "ls"       # bypass all permission checks
clawcodex --allow-dangerously-skip-permissions         # Full Access selectable, but don't start in it
```

### Permissions

An interactive `clawcodex` starts in **Full Access** — it can edit any file and
run any command without asking. Use `/permissions` at any time to pick a level:

| Level | What it allows |
| ----- | -------------- |
| **Ask for approval** | Read files and run read-only commands freely. Editing files, running other commands, or touching anything outside the workspace asks first. |
| **Approve for me** | Auto-accept file edits in the workspace and a small set of safe shell commands. Everything else still asks. |
| **Full Access** *(default)* | Edit any file and run any command without asking, except where you configured a deny or ask rule. |

The level you pick is saved to `permissions.defaultMode` in
`~/.clawcodex/settings.json` and applies to later sessions, so dialing
permissions down sticks. `--permission-mode` and
`--dangerously-skip-permissions` override it for a single run without changing
it.

`clawcodex -p` (headless) is **not** loose by default — it stays in `default`
unless you say otherwise, and a *saved* Full Access does not change that:
persistence dials headless down, never up. Say otherwise explicitly with
`--dangerously-skip-permissions` or `--permission-mode`. The same applies to
any launch that isn't attached to a terminal.

To start in a lower level every time, either pick it once with `/permissions`
or set it directly:

```json
// ~/.clawcodex/settings.json
{ "permissions": { "defaultMode": "default" } }
```

A repo can ship `.clawcodex/settings.json` (or `settings.local.json`) with a
`defaultMode` to *tighten* permissions for anyone who opens it. Repo-supplied
values are restricted to `default` and `dontAsk` — note `dontAsk` denies
anything that would otherwise prompt, so a repo can make the agent refuse
actions rather than ask about them — and are ignored if they'd be looser than
what would otherwise apply, so a cloned repo can never widen yours. (`plan` is
excluded deliberately: combined with bypass availability it is a full bypass,
not a restriction.) Administrators can disable Full Access outright with
`disableBypassPermissionsMode` in `~/.clawcodex/config.json` or managed policy.

`/plan` keeps restraining edits even in a Full Access session — unless you
launched with `--allow-dangerously-skip-permissions` or set
`allowBypassPermissionsMode` in your user config, both of which relax plan mode
by design.

### Claude Pro/Max subscription login

Run `clawcodex login`, choose `anthropic`, then choose `subscription`. The CLI
opens Claude's PKCE authorization page; paste the returned authorization code
back into the prompt. ClawCodex stores the OAuth tokens in
`~/.clawcodex/anthropic-oauth.json` with user-only permissions, refreshes them
automatically, and uses the subscription whenever no Anthropic API key is set.
An explicit `ANTHROPIC_API_KEY` or configured Anthropic API key takes precedence.

> **Important:** Anthropic does not officially support using Claude Pro/Max
> subscriptions from third-party clients. This integration may stop working
> and remains subject to Anthropic's terms. The login flow requires explicit
> confirmation before opening the authorization page.

Use `clawcodex config` to check connection status and
`clawcodex logout anthropic` to delete the stored OAuth credentials.

### ChatGPT subscription login (OpenAI)

Run `clawcodex login`, choose `openai`, then choose `subscription`. Three
login methods are offered: `browser` (opens ChatGPT's PKCE authorization page
and completes via a localhost:1455 redirect — the same flow the Codex CLI
uses), `device-code` (for headless/SSH machines: enter a short code at
auth.openai.com/codex/device), and `import-codex-cli` (shown when a Codex CLI
ChatGPT login exists in `~/.codex/auth.json` — copies it). Tokens are stored
in `~/.clawcodex/openai-oauth.json` with user-only permissions and refresh
automatically. With a subscription connected and no OpenAI API key set,
requests go to the ChatGPT Codex backend (Responses API) and draw on your
plan's allowance — the cost display shows $0. An explicit `OPENAI_API_KEY` or
configured OpenAI API key takes precedence. Subscription models:
`gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex-spark`.

> **Important:** using a ChatGPT plan from third-party clients rides the
> Codex CLI's official OAuth app, remains subject to OpenAI's terms, and may
> stop working. The login flow requires explicit confirmation.

Use `clawcodex config` to check connection status and
`clawcodex logout openai` to delete the stored OAuth credentials.

> **`--dangerously-skip-permissions`** disables every tool permission check
> for the session — the same thing an interactive `clawcodex` now does by
> default (see [Permissions](#permissions)). Recommended only inside sandboxed
> containers/VMs with no internet access. The *flag* is refused when the
> process is running as root/sudo unless `IS_SANDBOX=1` or
> `CLAUDE_CODE_BUBBLEWRAP=1` is set; under root the implicit default quietly
> falls back to `default` instead, so `sudo clawcodex` still starts.

***

## 📊 Status

| Component     | Status     | Count     |
| ------------- | ---------- | --------- |
| REPL Commands | ✅ Complete | Built-ins + `/tools`, `/stream`, `/context`, `/compact`, skills, etc. |
| Tool System   | ✅ Complete | 30+ tools |
| Automated Tests | ✅ Present | Tools, agent loop, providers, parity, REPL, auth, and more |
| Documentation | ✅ Complete | Guides, i18n READMEs, [FEATURE_LIST.md](FEATURE_LIST.md) |

### Core Systems

| System | Status | Description |
|--------|--------|-------------|
| CLI Entry | ✅ | `clawcodex`, `clawcodex tui`, `login`, `config`, `-p` / `--print`, `--version` |
| Interactive UI | ✅ | TypeScript Ink TUI (terminal client over a Python agent-server child); slash commands, @-file mentions, themes, permission dialog |
| Multi-Provider | ✅ | 25 providers — Anthropic, OpenAI, Gemini, Z.ai GLM, Minimax, OpenRouter, DeepSeek, plus an OpenAI-compatible provider registry (NVIDIA NIM, Together, Novita, Fireworks, SiliconFlow, Moonshot/Kimi, DeepInfra, Hugging Face, Volcengine, StepFun, Arcee, AtlasCloud, Xiaomi MiMo, Wanjie Ark) and local servers (Ollama, vLLM, SGLang). Anthropic→OpenAI image / document block translation for vision-capable OpenAI-compat backends; per-provider API-key env-var fallback |
| Session Persistence | ✅ | Save/load sessions locally |
| Agent Loop | ✅ | Tool calling loop with streaming and headless mode |
| Skill System | ✅ | SKILL.md-based slash-command skills with args + tool limits |
| Cancellation / Abort | ✅ | ESC closes in-flight Bash, Grep/Glob, and streaming HTTP within ~50ms across every provider; subagents get isolated `AbortController`s; `Bash` `tool_result` distinguishes timeout from ESC-abort |
| Image Handling | ✅ | TS-parity Read pipeline (magic-byte sniff, resize/compress to API limits); `@image.png` @-mentions inline as `ImageBlock`; pre-API base64 size validation in `BaseProvider._prepare_messages`; binary @-mentions (PDF/zip/docx/...) routed to a Read-tool hint instead of mojibake |
| Context Building | 🟡 | Workspace / git / `CLAWCODEX.md` injection; richer summaries and memory still evolving |
| Permission System | 🟡 | Framework and checks; full integration still in progress |
| MCP | 🟡 | MCP-oriented tools and wiring; full protocol/runtime polish ongoing |

### Tool System (30+ Tools Implemented)

| Category | Tools | Status |
|----------|-------|--------|
| File Operations | Read, Write, Edit, Glob, Grep | ✅ Complete |
| System | Bash execution | ✅ Complete |
| Web | WebFetch, WebSearch | ✅ Complete |
| Interaction | AskUserQuestion, SendMessage | ✅ Complete |
| Task Management | TodoWrite, TaskManager, TaskStop | ✅ Complete |
| Agent Tools | Agent, Brief, Team | ✅ Complete |
| Configuration | Config, PlanMode, Cron | ✅ Complete |
| MCP | MCP tools and resources | 🟡 Tools wired; full client/runtime still evolving |
| Others | LSP, Worktree, Skill, ToolSearch | ✅ Complete |

### Roadmap Progress

- ✅ **Phase 0**: Installable, runnable CLI
- ✅ **Phase 1**: Core Claude Code MVP experience
- ✅ **Phase 2**: Real tool calling loop
- 🟡 **Phase 3**: Context depth, permission integration, `/resume`-class recovery (in progress)
- 🟡 **Phase 4**: MCP runtime depth, plugins, extensibility (tools exist; platform work continues)
- ⏳ **Phase 5**: Python-native differentiators

**See [FEATURE_LIST.md](FEATURE_LIST.md) for detailed feature status and PR guidelines.**

## 🖥️ ClawCodex Desktop

The agent also ships as a **native desktop app** (`ui-desktop/`): streaming
chat with live tool activity and approval prompts, a session sidebar with
resume, side-by-side previews, and settings — no terminal required. It runs
on the same backend, config, and session store as the CLI/TUI.

```bash
# from a source checkout (installs UI deps on first run)
clawcodex desktop
```

The app boots its own backend via `clawcodex serve` (loopback HTTP +
WebSocket gateway over the in-process agent core). See
[`ui-desktop/README.md`](ui-desktop/README.md) for development docs.

## 🌐 ClawCodex Web

The same agent in a browser tab (`ui-web/`): three-column shell with a
project/worktree session tree, streaming replies with reasoning and live tool
cards (terminal, diff, line-numbered read), permission approvals as a composer
takeover, a prompt queue, a context meter, and light/dark themes.

```bash
# from a source checkout (builds the bundle on first run)
clawcodex web --build
clawcodex web            # once built
```

It is not a second server: `clawcodex web` is `clawcodex serve` with the built
`ui-web/dist` mounted on it, so a tab drives the **same in-process agent, the
same gateway socket, and the same saved sessions** as the TUI and the desktop
app. `GET /` inlines the session token so the browser can open the socket —
which is why a non-loopback `--host` is refused unless you pass
`--allow-remote` and put your own auth in front of it. See
[`ui-web/README.md`](ui-web/README.md) for development docs.

## 🚀 Quick Start

### Install

```bash
# Install the latest release from PyPI (recommended). The distribution is
# named clawcodex-cli; the installed command remains `clawcodex`.
pipx install clawcodex-cli
# Or: uv tool install clawcodex-cli
# Or, inside an activated virtual environment: pip install clawcodex-cli

# Run the CLI
clawcodex --help
```

For development from a source checkout:

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex

# Create venv (uv recommended)
uv venv --python 3.11
source .venv/bin/activate      # Windows PowerShell: .venv\Scripts\Activate.ps1

# Install package + entry point (recommended)
uv pip install -e ".[dev]"

# Alternative: requirements only, then editable install
# uv pip install -r requirements.txt && uv pip install -e .
```

Windows note: the CLI runs natively on Windows 10/11 (PowerShell, cmd, or
Windows Terminal). Install [Git for Windows](https://git-scm.com/download/win)
first — the agent's Bash tool executes commands through Git Bash (never the
WSL `System32\bash.exe`), so shell commands behave the same as on
macOS/Linux. `CLAWCODEX_GIT_BASH_PATH` overrides the auto-detected bash.

### Configure

#### Option 1: Interactive (Recommended)

```bash
clawcodex login
# or: python -m src.cli login
```

This flow will:

1. ask you to choose a provider: anthropic / openai / gemini / zai / minimax / openrouter / deepseek, or any OpenAI-compatible vendor (together, novita, fireworks, moonshot, nvidia-nim, siliconflow, deepinfra, huggingface, …) and local servers (ollama / vllm / sglang)
2. ask for that provider's API key
3. optionally save a custom base URL
4. optionally save a default model
5. set the selected provider as default

The configuration file is saved in `~/.clawcodex/config.json`. Example structure:

```json
{
  "default_provider": "deepseek",
  "providers": {
    "anthropic": {
      "api_key": "your-api-key",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-6"
    },
    "openai": {
      "api_key": "your-api-key",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-5.4"
    },
    "zai": {
      "api_key": "your-api-key",
      "base_url": "https://api.z.ai/api/coding/paas/v4",
      "default_model": "glm-5.2"
    },
    "minimax": {
      "api_key": "your-api-key",
      "base_url": "https://api.minimax.io/anthropic",
      "default_model": "MiniMax-M3"
    },
    "openrouter": {
      "api_key": "your-api-key",
      "base_url": "https://openrouter.ai/api/v1",
      "default_model": "deepseek/deepseek-v4-pro"
    },
    "deepseek": {
      "api_key": "your-api-key",
      "base_url": "https://api.deepseek.com",
      "default_model": "deepseek-v4-pro"
    }
  },
  "session": {
    "auto_save": true,
    "max_history": 100
  },
  "settings": {
    "advisor_enabled": false,
    "advisor_model": "claude-sonnet-4-6",
    "advisor_client_mode": false,
    "advisor_provider": "openai",
    "modelLimits": {
      "private-1m-model": {
        "contextWindow": 1000000,
        "maxOutputTokens": 32768
      }
    }
  },
  "env": {
    "TAVILY_API_KEY": "tvly-YOUR-TAVILY-API-KEY"
  }
}
```

The built-in Minimax provider passes an SDK base URL to the Anthropic SDK. Use
`https://api.minimax.io/anthropic` globally or
`https://api.minimaxi.com/anthropic` in China; the SDK appends `/v1/messages`.
The final Messages request URLs are
`https://api.minimax.io/anthropic/v1/messages` and
`https://api.minimaxi.com/anthropic/v1/messages`. The OpenAI-compatible API roots are
`https://api.minimax.io/v1` globally and `https://api.minimaxi.com/v1` in China.

- **`session`** — REPL session persistence: `auto_save` writes each session automatically; `max_history` caps retained turns.
- **`settings`** — the advisor (reviewer) feature. **`advisor_enabled` is the master switch — `false` by default, so the advisor is OFF unless you opt in** (set it to `true`, or run `/advisor <provider>:<model>` which flips it on). `advisor_provider` / `advisor_model` pick the reviewer model; `advisor_client_mode` routes the call via the client.
- **`settings.modelLimits`** — context/output limits for private or gateway models absent from the built-in catalog. Keys may be exact model IDs, model prefixes, or host-qualified IDs such as `localhost:4000:private-model`; exact matches beat prefixes and host-qualified keys beat bare keys within the same match type. Built-in catalog metadata remains authoritative.
- **`env`** — secrets and environment values injected at startup (e.g. `TAVILY_API_KEY` for web search). Managed via `clawcodex config`; keys here are exported into the process environment without overriding anything you already set in your shell.

#### Stream-stall tuning

A streaming request that keeps the connection alive but stops producing content would otherwise hang until something upstream kills it. Two independent bounds catch that, one per wire — they are deliberately separate knobs, because raising one to work around a quirk on your gateway should not loosen the other.

| Variable | Wire | Default | Notes |
| --- | --- | --- | --- |
| `CLAWCODEX_CONTENT_PROGRESS_TIMEOUT_MS` | OpenAI-compatible (OpenRouter, DeepSeek, Z.AI, Groq, local servers, …) | `300000` (5 min) | Max time with **no content, reasoning, or tool-call delta**. Byte-level keepalives do *not* count — that is the point, since they defeat an HTTP read timeout. **Set to `0` to disable.** A malformed value falls back to the default rather than disabling. |
| `CLAUDE_STREAM_IDLE_TIMEOUT_MS` / `CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS` | Anthropic | `90000` / `300000` | Byte-aware idle watchdog; re-arms while raw bytes keep arriving. |

The 300 s default is measured, not guessed: across 727 completed trials in 44 terminal-bench eval jobs, mean seconds per agent turn ran median 14.2 / p99 100.3 / max 183.8, with zero trials above 300 s — and that metric overstates model latency because it includes tool execution. If a slow gateway trips it anyway, the error names the variable.

### Run

```bash
clawcodex                  # Start inline REPL (same as python -m src.cli)
clawcodex --help           # All flags: -p, --provider, --model, …
```

**That's it!** Configure keys, then run the CLI or REPL.

***

## 💡 Usage

### REPL Commands

| Command | Description |
| -------- | ----------- |
| `/` | Show commands and skills |
| `/help` | Help text |
| `/tools` | List tool names from the registry |
| `/tool <name> <json>` | Run a tool directly with JSON input |
| `/stream` | Toggle streaming: `/stream on`, `off`, or `toggle` |
| `/render-last` | Re-render last assistant reply as Markdown |
| `/save` / `/load <id>` | Persist or restore a session |
| `/clear` | Clear conversation (also `/reset`, `/new`) |
| `/skill` | Skill launcher flow |
| `/context` | Workspace / prompt context (when available) |
| `/compact` | Compact or clear conversation (fallback clears if compact unavailable) |
| `/eco` | Toggle Bash-output token compression (`on` / `off` / `status` for per-filter savings) |
| `/exit`, `/quit`, `/q` | Exit |

### Skills (Slash Commands)

Skills are markdown-based slash commands stored under `.clawcodex/skills`. Each skill lives in its own directory and must be named `SKILL.md`.

**1) Create a project skill**

Create:

```text
<project-root>/.clawcodex/skills/<skill-name>/SKILL.md
```

Example:

```md
---
description: Explains code with diagrams and analogies
when_to_use: Use when explaining how code works
allowed-tools:
  - Read
  - Grep
  - Glob
arguments: [path]
---

Explain the code in $path. Start with an analogy, then draw a diagram.
```

**2) Use it in the REPL**

```text
❯ /
❯ /<skill-name> <args>
```

Example:

```text
❯ /explain-code qsort.py
```

**Notes**

- User-level skills: `~/.clawcodex/skills/<skill-name>/SKILL.md`
- Tool limits: `allowed-tools` controls which tools the skill can use.
- Arguments: use `$ARGUMENTS`, `$0`, `$1`, or named args like `$path` (from `arguments`).
- Placeholder syntax: use `$path`, not `${path}`.

### Scheduled tasks (`/loop`)

Run a prompt repeatedly while the session stays open — poll a deployment, babysit a PR, or re-run a skill on a cadence. A port of Claude Code's [`/loop` + scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks).

| What you type | What happens |
| ------------- | ------------ |
| `/loop 5m check the deploy` | The prompt runs on a **fixed schedule** (a recurring cron job) |
| `/loop check the deploy` | **Self-paced mode** — after each iteration the model picks the next delay (1 min–1 hr) via the `ScheduleWakeup` tool and tells you why |
| `/loop 15m` | The built-in **maintenance prompt** (or your `loop.md`) on a fixed schedule |
| `/loop` | The maintenance prompt, self-paced |

Intervals accept `s`/`m`/`h`/`d` as a leading token (`30m check ci`) or a trailing clause (`check ci every 2 hours`). A bare `/loop` looks for `.clawcodex/loop.md`, then `~/.clawcodex/loop.md`, then falls back to the built-in maintenance prompt (continue unfinished work, tend the PR, cleanup passes).

**How it runs** — scheduled prompts fire **between turns**, when the session is idle; a task that comes due mid-turn fires once when the turn ends (no catch-up). Under the hood the model manages jobs with `CronCreate` / `CronList` / `CronDelete` (standard 5-field cron, local timezone, 8-char job IDs, 50 per session) — you can also just ask in natural language ("what scheduled tasks do I have?", "remind me in 45 minutes to check the build"). Recurring jobs auto-expire after **7 days** (one final fire, then self-delete); one-shots delete after firing. Deterministic per-job jitter spreads fire times (recurring: up to 30 min or half the interval; one-shots pinned to `:00`/`:30` fire up to 90 s early).

**Stopping** — press **Esc** while a self-paced loop waits and the pending wakeup is cleared (the loop won't fire again); cron jobs stay until `CronDelete` or expiry. In self-paced mode the model can end the loop itself (`ScheduleWakeup stop: true`); an iteration that neither reschedules nor stops gets one ~20-minute fallback wakeup, then the loop ends. `/clear` drops all session tasks; `/resume` restores unexpired ones. Set `CLAWCODEX_DISABLE_CRON=1` to disable the scheduler entirely.

The TUI shows an indicator above the composer while anything is armed: `⟳ loop wakeup in 2m 14s · ⏰ 1 scheduled`.

***

## 🎨 Demos

**Every app under [`demos/`](demos/) was generated end-to-end by ClawCodex itself** — same CLI you just installed, same agent loop, same tools. No hand-edits 🙂

| Demo | Stack | Description |
| ---- | ----- | ----------- |
| [`demos/crm-app`](demos/crm-app) | React 18 + Vite + Vitest | Mini CRM with contacts, deals, dashboard, and a full test suite |
| [`demos/linkedin-app`](demos/linkedin-app) | React 18 + Vite + React Router | LinkedIn-style feed: profile, network, jobs, messaging |
| [`demos/minecraft-app`](demos/minecraft-app) | React + three.js + @react-three/fiber | Browser voxel sandbox with terrain, mining, HUD, and player controls |
| [`demos/wc26-intro`](demos/wc26-intro) | Static HTML/CSS/JS | FIFA World Cup 2026 intro page — animated hero, live countdown, host nations, 16 stadiums, format, and record facts; built end-to-end with the new Z.ai **GLM-5.2** model |

```bash
cd demos/crm-app   # or linkedin-app / minecraft-app
npm install
npm run dev        # vite dev server
```

`demos/wc26-intro` is a single static page — just open [`demos/wc26-intro/index.html`](demos/wc26-intro/index.html) in your browser.

Want to see how it's done? Open ClawCodex in any empty directory and ask it to build something — they were all generated exactly that way.

***

## 🎓 Why ClawCodex?

### Based on Real Source Code

- **Not a clone** — Ported from actual TypeScript implementation
- **Architectural fidelity** — Maintains proven design patterns
- **Improvements** — Better error handling, more tests, cleaner code

### Python Native

- **Type hints** — Full type annotations
- **Modern Python** — Uses 3.10+ features
- **Idiomatic** — Clean, Pythonic code

### User Focused

- **3-step setup** — Clone, configure (`clawcodex login`), run (`clawcodex`)
- **Interactive config** — Provider, base URL, and default model in one flow
- **Ink TUI** — TypeScript terminal client over a Python agent-server child
- **Scriptable** — `-p` / JSON / NDJSON for automation
- **Session persistence** — Save and reload conversations

***

## Architecture

For the six core abstractions (query loop, tools, tasks, two-tier state,
memory, hooks) and the golden path from user input to model output,
see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). It is the recommended
starting point for new contributors.

The reference for the original Claude Code architecture is at
`claude-code-from-source/book/ch01-architecture.md`; the
chapter-by-chapter port gap analyses and refactoring plans live under
`my-docs/`.

***


## 📦 Project Structure

```text
clawcodex/
├── src/
│   ├── cli.py              # CLI entry (console: clawcodex)
│   ├── entrypoints/        # Headless (-p), agent-server, and Ink-TUI launcher
│   ├── server/             # Direct Connect agent-server (Ink TUI backend)
│   ├── providers/          # Anthropic, OpenAI, Gemini, Z.ai GLM, Minimax, OpenRouter, DeepSeek + OpenAI-compatible registry (openai_compatible_specs.py)
│   ├── agent/              # Conversation, session, prompts
│   ├── tool_system/        # Agent loop, tools, schemas
│   ├── skills/             # SKILL.md loading and skill tool
│   ├── services/           # MCP, compact, IDE bridge, tool execution, …
│   ├── context_system/     # Workspace / git / CLAWCODEX.md context
│   ├── permissions/        # Permission modes and bash parsing
│   ├── hooks/              # Hook types and execution helpers
│   └── command_system/     # Slash commands and substitution
├── typescript/             # Reference / parity source (not required to run Python CLI)
├── tests/                  # pytest suites
├── docs/                   # Guides, i18n READMEs, refactor notes
├── .clawcodex/skills/      # Project-local skills (optional)
├── FEATURE_LIST.md         # Capability matrix and roadmap
└── pyproject.toml          # Package metadata and clawcodex script
```

***


## 🤝 Contributing

**We welcome contributions!**

```bash
# Quick dev setup
pip install -e .[dev]
python -m pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

***

## 📖 Documentation

- **[SETUP_GUIDE.md](docs/guide/SETUP_GUIDE.md)** — Detailed installation
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Development guide
- **[TESTING.md](docs/guide/TESTING.md)** — Testing guide
- **[CHANGELOG.md](CHANGELOG.md)** — Version history
- **[TODOS.md](TODOS.md)** — Known gaps and deferred work

***

## ⚡ Performance

- **Startup**: < 1 second
- **Memory**: < 50MB
- **Response**: Turn-based assistant output with Rich markdown rendering

***

## 🔒 Security

✅ **Basic Local Safety Practices**

- No sensitive data in Git
- API keys obfuscated in config
- `.env` files ignored
- Safe for local development workflows

***

## 📄 License

MIT License — See [LICENSE](LICENSE)

***

## 🙏 Acknowledgments

- Based on Claude Code TypeScript source
- Independent educational project
- Not affiliated with Anthropic

***

<div align="center">

### 🌟 Show Your Support

If you find this useful, please **star** ⭐ the repo!

**Made with ❤️ by ClawCodex Team**

[⬆ Back to Top](#clawcodex)

</div>

***

***
