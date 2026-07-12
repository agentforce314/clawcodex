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

# 🐋🔥 DeepSeek Prefix Cache

# Run long agentic coding sessions for *pennies*

### Cache-hit input bills at **`~$0.0435` / 1M tokens** — about **230× cheaper** than Claude Fable 5 (`$10` / 1M).

ClawCodex keeps your request prefix **byte-stable**, so DeepSeek's prompt cache covers your whole
`system + tools + history` span across turns. **The longer you code, the more you save.**

</div>

***

## ⚡ Quick Install

**One line** — installs `uv`, Python 3.10+, and puts `clawcodex` on your PATH:

```bash
curl -fsSL https://clawcodex.app/install.sh | bash
```

Then configure a provider and start coding:

```bash
clawcodex login   # interactive provider + API key setup → ~/.clawcodex/config.json
clawcodex --dangerously-skip-permissions         # start the REPL in any project
```

The installer also ships `clawcodex` lifecycle helpers — `doctor` (diagnose your
environment), `verify` (health-check the install), `update`, and `uninstall`. It is
re-run-safe and works on macOS, Linux, and WSL. To pass flags through the pipe, use
`curl -fsSL https://clawcodex.app/install.sh | bash -s -- --dry-run`.

<details>
<summary><b>Or install manually from source</b></summary>

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+
pip install -r requirements.txt

python -m src.cli login   # writes config to ~/.clawcodex/config.json

python -m src.cli --dangerously-skip-permissions   # start the REPL
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

- **2026-07-07:** **`/loop` scheduled tasks now actually fire — full port of Claude Code's session-scoped scheduler (#680)** — the bundled `/loop` skill finally has a real engine behind it: a new `src/scheduled_tasks` module parses standard 5-field cron expressions and fires due prompts **between turns** from the agent-server's idle poll. `CronCreate`/`CronList`/`CronDelete` register real firing jobs (8-char IDs, 50-job cap, deterministic jitter, 7-day recurring expiry with one final fire), and the new **`ScheduleWakeup`** tool drives self-paced `/loop` mode — the model picks each next delay (1 min–1 hr), `stop: true` ends the loop, and a ~20-minute fallback wakeup catches iterations that forget to reschedule. Typed skill slash commands now reach the backend (new `skill_command` control), so `/loop 5m check ci` works from the composer with completion + argument hint; the TUI shows a live countdown indicator (`⟳ loop wakeup in 2m 14s · ⏰ 1 scheduled`) and **Esc while idle stops a waiting loop**. `/clear` drops session tasks, `--resume` restores unexpired ones, `CLAWCODEX_DISABLE_CRON=1` disables the scheduler. 117 new tests; verified live over stdio NDJSON and a real PTY TUI drive (typed dispatch → CronCreate → a real wakeup fire between turns → Esc-stop).
- **2026-07-07:** **Bounded the ESC-cancel chunk queue in OpenAI-compatible streaming (#278)** — `OpenAICompatibleProvider.chat_stream_response`'s worker-thread queue (added in #148) was an unbounded `queue.Queue`. A non-graceful disconnect from a proxy that keeps sending bytes after ESC (and never closes the SDK iterator) let the orphaned worker thread accumulate chunks in memory indefinitely. The queue is now capped at 64 chunks, so `put()` blocks the worker once full instead of growing without bound.
- **2026-07-06 (v1.0.0):** **ClawCodex v1.0.0 — the 1.0 release: goal-directed autonomy, hooks & MCP wired for production, and a hardened permission system** — 86 commits since v0.7.0 (#580–#668) finish wiring the big subsystems end-to-end and graduate ClawCodex to 1.0. **Goal-directed autonomy:** the `/goal` + `/subgoal` completion-condition loop keeps the agent working until an LLM judge confirms the goal is actually met (#664), the new Monitor tool streams long-running shell output with backpressure (#665), background-bash completion notifications (#663), coordinator mode wired end-to-end on the live paths (#634), and `/advisor` token-efficient worker/reviewer pairing restored on the Ink TUI (#668). **Hooks live in production:** configured hooks now actually fire — bootstrap Hooks abstraction (#583), UserPromptSubmit (#597), multi-scope + lifecycle hooks (#595), `if` pre-filters (#643), PreToolUse `permissionDecision` (#655), PermissionRequest hooks at the ask seam (#637), MCP elicitation hooks (#659), and teammate TaskCompleted / TeammateIdle stop hooks (#642). **MCP completion:** OAuth server auth via the `/mcp` flow (#662), live `tools/list_changed` refresh (#598, #604), server instructions injected into the system prompt (#654), and `clawcodex mcp serve` re-exposes ClawCodex tools as an MCP stdio server (#635). **Permission hardening:** readable approval boxes with broadenable, persistent session grants (#608–#611), compound-command permission parity (#622), Bash normalization hardening (#626), `disableBypassPermissionsMode` lockdown (#660), an honest refuse-to-start unsandboxed guard (#658), subprocess secret-scrubbing (#650), and a flag-gated LLM security-classifier lane for auto mode (#589). **TUI maturity:** faithful Claude Code look & feel — diff rendering, tool-call transcript, task list, composer + permission-mode badge, busy line (#612–#616) — plus a minimal vim editing engine (#667), Esc-interrupt with a defanged Ctrl+C (#625), fully editable multi-line input (#621), slash-command argument hints (#631), a persistent session-stats line (#657), and restored `/cost`, `/skills`, and `/model` (#627, #629, #630). **Reliability:** the production compaction pipeline is wired and auto-compact actually applies its result (#587, #607), full retry lane + model fallback + message-history caching (#586), parallel Agent fan-out with the concurrency-cap deadlock fixed (#590), killing a background agent really stops the run (#606), and output styles work end-to-end (#640). Codebase stats: 1,170 Python files, **256,909 lines** (up from 233,520 lines on 2026-06-11).
- **2026-06-30 (v0.7.0):** **ClawCodex v0.7.0 — TUI auto-theming, faithful inline rendering & a Claude-Code-style tool trail** — the Ink TUI now detects your terminal's background color (OSC 11) on startup and selects the light/dark theme to match, so text stays readable on any terminal with no env var needed (#577). Inline mode renders *truly* inline like Claude Code: no screen wipe on launch, and no overlap with prior terminal output on startup or with the returning shell prompt on exit (#573, #575). The tool trail reads Claude-style — workspace-relative paths (`Read(src/foo.ts)`), `Grep(pattern)` labels, and a `Read N lines` result collapse (#574) — and the banner gains a 🦞 mascot with brighter secondary text on dark themes (#576).
- **2026-06-24 (v0.6.0):** **ClawCodex v0.6.0 — interactive TUI REPL parity** — a batch of input ports brings the Python REPL to parity with the ink reference: a working slash-command menu (execute / complete / filter like the ink REPL), the sparkle spinner with a live token + elapsed busy row, context-aware prompt footer hints (interrupt / bash / grammar), the `?` shortcuts help panel, an `@` file-mention dropdown with in-place splice, double-press Ctrl+C / Ctrl+D to exit, Ctrl+R history search + double-Esc clear-draft, a `[Pasted text #N +K lines]` large-paste placeholder, and the completed command queue (drain queued prompts + dim preview). Login docs now list all 25 providers (#383).
- **2026-06-23:** **One-click installer** — `curl -fsSL https://clawcodex.app/install.sh | bash` installs uv (no sudo), provisions Python 3.10+, clones to `~/.clawcodex`, creates a lock-pinned venv, and registers `clawcodex` on PATH; ships status / doctor / verify / update / uninstall subcommands, is safe to re-run, and works on macOS / Linux / WSL.
- **2026-06-21:** **18 new LLM providers — the registry grows 7 → 25 (#377)** — a data-driven `ProviderSpec` registry adds 18 OpenAI-compatible backends (nvidia-nim, fireworks, together, moonshot/Kimi, novita, siliconflow, deepinfra, stepfun, arcee, huggingface, volcengine, xiaomi-mimo, atlascloud, wanjie-ark, plus local ollama / vllm / sglang) alongside the hand-written providers; alias-aware config resolution, standard env-var key fallback (e.g. `TOGETHER_API_KEY`), and keyless local servers.
- **2026-06-18:** **DeepSeek prefix-cache exploitation — a HUGE token-cost win (#363)** — ClawCodex now keeps its request prefix **byte-stable** across turns so DeepSeek's automatic prompt-prefix cache covers the entire `system + tools + history` span. Per-request-volatile sections (env, the mutable `MEMORY.md` body, plan-mode, etc.) are relocated to a trailing `<system-reminder>` *after* the conversation history, so the cached prefix never breaks even when memory/env change. We also register DeepSeek's **1M-token context window**, map its prompt-cache usage onto the Anthropic `cache_read_input_tokens` convention, and surface a per-model **prompt-cache hit-rate** + cost in `/cost`. **Why this is enormous — the token economics:** Claude Fable 5 runs **$10 / $50** per 1M input/output tokens, while **DeepSeek-V4-Pro is just $0.435 / $0.87** — already **~23× cheaper on input** and **~57× cheaper on output**. And because **cache-hit input is billed at only 10%** of the normal input rate, the long, context-heavy sessions that agentic coding actually produces pay just **~$0.0435 per 1M input tokens** — roughly **230× cheaper than Fable 5 input**. The token efficiency ClawCodex unlocks here is **HUGE**. Everything is gated to the `deepseek` provider — every other provider's request is byte-for-byte unchanged. Follow-up: truncated tool-call argument JSON is now best-effort recovered in the shared OpenAI-compatible layer, so an interrupted DeepSeek stream keeps its partial tool args instead of dropping them to `{}` (#364).
- **2026-06-16:** **Z.ai GLM-5.2 support (#343)** — new `zai` provider for Z.ai's OpenAI-compatible GLM Coding Plan (`https://api.z.ai/api/coding/paas/v4`), shipping `GLM-5.1` and the `GLM-5.2` preview; GLM-5.2 delivers coding capability comparable to Claude Opus 4.7. First app built end-to-end with GLM-5.2 — a [FIFA World Cup 2026 intro page](demos/wc26-intro/index.html) (animated hero + live countdown, three host nations, 16 stadiums, tournament format, and record-breaking facts).
- **2026-06-11:** **Codebase stats** — Total Python files: 1,093 files; Total Lines of Python Code: **233,520 lines** (up from 213,777 lines on 2026-05-29; ~+19.7k lines from the interactive command-system batch, the dynamic workflow engine + `/deep-research`, and the Tavily web-tooling refresh).
- **2026-06-10 to 2026-06-11:** **Dynamic workflow engine + `/deep-research` (#262–#264, #266–#271)** — Python workflow engine core (`agent()`/`parallel()`/`pipeline()`/`phase()`, journaling, resume) wired end-to-end: Workflow tool, `/workflows` TUI dialog + status-line pill, per-agent retry, worktree isolation, result delivery, and the bundled `/deep-research` harness registered as a slash command. Reliability: LLM read timeout applied centrally to all openai-compatible providers (#269), parallel agents no longer serialize on the event loop (#270), and the deep-research synthesize step forbids tools so the report-writer can't loop (#271). Follow-ups: workflow max-turns cap fix (#272), deep-research verdict-enum fix (#273), rich `/workflows` live monitor with phase progress + per-agent stats (#287).
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
    "huggingface", "together", "stepfun", "deepinfra",
    # Local servers (no API key required)
    "ollama", "vllm", "sglang",
]  # 25 providers; aliases like `nim`, `kimi`, `hf` resolve automatically
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

### Complete CLI

```bash
clawcodex                       # Interactive Ink TUI (default)
clawcodex tui                   # Interactive Ink TUI (explicit)
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

# Permission control (REPL, TUI, and -p all honor these)
clawcodex --permission-mode plan                       # plan / acceptEdits / dontAsk
clawcodex --dangerously-skip-permissions -p "ls"       # bypass all permission checks
clawcodex --allow-dangerously-skip-permissions         # allow /permission-mode bypass later
```

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
> for the session. Recommended only inside sandboxed containers/VMs with no
> internet access. The flag is refused when the process is running as
> root/sudo unless `IS_SANDBOX=1` or `CLAUDE_CODE_BUBBLEWRAP=1` is set.

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
| Context Building | 🟡 | Workspace / git / `CLAUDE.md` injection; richer summaries and memory still evolving |
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

## 🚀 Quick Start

### Install

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex

# Create venv (uv recommended)
uv venv --python 3.11
source .venv/bin/activate

# Install package + entry point (recommended)
uv pip install -e ".[dev]"

# Alternative: requirements only, then editable install
# uv pip install -r requirements.txt && uv pip install -e .
```

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
    "advisor_provider": "openai"
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
- **`env`** — secrets and environment values injected at startup (e.g. `TAVILY_API_KEY` for web search). Managed via `clawcodex config`; keys here are exported into the process environment without overriding anything you already set in your shell.

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
│   ├── context_system/     # Workspace / git / CLAUDE.md context
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
