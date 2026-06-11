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

## ⚡ Quick Install

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+
pip install -r requirements.txt

python -m src.cli login   # writes config to ~/.clawcodex/config.json

python -m src.cli         # start the REPL
```

***

## 📰 News

- **2026-05-29:** **Codebase stats** — Total Python files: 977 files; Total Lines of Python Code: **213,777 lines** (up from 183,768 lines on 2026-05-21; ~+30k lines from the remote-bridge parity port (phases 0–18) plus the `/buddy` companion subsystem and the CLI transport layer).
- **2026-05-29:** **Remote-bridge parity + CLI transports (#200–#226)** — full port of the remote-control bridge across phases 0–18: bridge API client, child-CLI session runner, env-less v2 orchestrator, multi-session daemon, worktree spawn, in-place reconnect, perpetual mode with crash recovery, JWT refresh, and the v1 `WebSocketTransport` / `SerialBatchEventUploader` write path with hybrid dispatch. Plus the CLI transport factory, coalescing worker-state uploader, and `RemoteIO` bridge (#226); new `/buddy` virtual-companion command — hatch / pet / status / mute (#225).
- **2026-05-21:** **Codebase stats** — Total Python files: 890 files; Total Lines of Python Code: **183,768 lines** (up from 177,428 lines on 2026-05-16; net −4 files from the `src/tool_system/agent_loop.py` consolidation into `src/query/query.py`).
- **2026-05-21:** **`/advisor` token-efficient coding mode (#181–#193)** — pair a cheap worker (`haiku-4-5`, $1/$5 per Mtok) with an expensive reviewer (`opus-4-7`, $5/$25) consulted only at decision points; ~6× cheaper than opus-only on typical sessions. Explicit `<provider>:<model>` syntax, cross-provider routing (e.g. `deepseek/deepseek-v4-pro` worker + `claude-opus-4-7` advisor via litellm), and live worker/advisor token + USD cost in the status bar.
- **2026-05-16:** **Codebase stats** — Total Python files: 894 files; Total Lines of Python Code: **177,428 lines** (up from 167,034 lines on 2026-05-14; ~+10.4k lines in two days, mostly ESC-cancellation hardening + image-handling parity).
- **2026-05-16:** **Image-handling parity (Tier C, #149/#154/#155/#156)** — Read tool TS image pipeline (sniff, resize/compress, base64 cap); `@image.png` mentions inline as real `ImageBlock`s instead of mojibake; cross-provider Anthropic `image`/`document` → OpenAI `image_url`/`file` translation; pre-API base64 size validation in `BaseProvider`.
- **2026-05-16:** **Subagent + Bash reliability** — custom subagents now discovered from `.claude/agents/` (#151); Bash `tool_result` distinguishes timeout from ESC-abort so the model can tell the two apart (#152); async-subagent `AbortController` isolation pinned by regression tests so a parent's ESC doesn't fire a sibling's abort listeners (#153); cancelled `tool_result` reliably surfaces `REJECT_MESSAGE` on the production path (#150).
- **2026-05-15 to 2026-05-16:** **ESC cancellation hardening across providers (#144–#148)** — mid-stream cancellation closes the streaming HTTP response within ~50ms for every supported provider (Anthropic, OpenAI, GLM, Minimax, DeepSeek, OpenRouter); shared `StreamAbortGuard` helper extracted; LiteLLM worker-thread iteration fixes long-tail hangs on OpenAI-compat backends.
- **2026-05-15:** **SWE-bench Verified result** — `clawcodex` resolves **291/499 (58.2%)** vs `openclaude` **265/499 (53.0%)** on Gemini 2.5 Pro under the standardized harness (see [`eval/`](eval/) for the comparison framework — cumulative batching, parallel `--predict-workers`, full stream-json trace capture).
- **2026-05-15:** **Native Gemini provider** — `src/providers/gemini_provider.py` via the `google-genai` SDK. Schema sanitization keeps OpenAI-style tool definitions compatible with Gemini's stricter `Schema` (`oneOf`/`additionalProperties` stripped, polymorphic params coerced to `string`).
- **2026-05-14:** **Codebase stats** — Total Python files: 837 files; Total Lines of Python Code: **167,034 lines**.
- **2026-05-14:** **ESC cancellation latency fix (#130)** — pressing ESC now cancels in-flight Bash commands and streaming responses within ~50ms, on top of the diff color-bar full-width render fix (#129) and the bypass-permissions outside-paths fix (#128).
- **2026-05-12:** **Bootstrap + architecture docs** — new architecture overview at [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); production bootstrap port (memoized `init()`, trust boundary, unified `launch_repl(args)`, `--bare` fast path, schema migration runner); main query loop and agent loop routed through `dispatch_full` with deferred tool loading.
- **2026-05-11 (v0.5.0):** **ClawCodex v0.5.0 released** — rebrand to ClawCodex across user-visible UI; reactive state subsystem ported (signals, store, session context, cost tracker, 1h cache eligibility); API layer hardened (output-token cap, request-id injection, message-level cache breakpoints, Haiku fast path, watchdog + non-streaming fallback, retry-with-stream); agent loop foundation (typed terminal, media recovery, blocking-limit guards, token budget, stop hooks, model fallback, continuation nudge); refreshed README screenshot.
- **2026-05-10:** **MCP subsystem landed** — full Model Context Protocol support with OAuth wiring, HTTPS, XSS hardening, and async I/O; passed a multi-pass security review.
- **2026-05-10:** **Performance pass** — startup profiler, prompt-cache plumbing with sticky latches, bitmap + async search indexing with score-bound pruning, cold-start latency reduction, streaming hardening.
- **2026-05-10:** **CCR remote bridge (phases 0–5)** — Direct Connect via `cc://` and `cc+unix://`, Bridge v2 transport, Remote Session viewer, CCR upstream proxy.
- **2026-05-10:** **Standalone REPL modules** — vim mode, transcript search, IME cursor, terminal hyperlinks, frame metrics, thinking widget, per-tool permission specialization, output-styles frontmatter; REPL transcript readability and ANSI diff backgrounds.
- **2026-05-08:** **Hooks system** — snapshot-based executor with workspace-trust gate, expanded event/source taxonomy, schema validation, environment injection.
- **2026-05-08:** **Multi-agent coordination** — typed task state machine, JSONL transcript writer, agent task lifecycle, progress tracker, task-notification routing, `SendMessage` peer DMs, swarm primitives, background resume, coordinator mode + worker agents, permission forwarding bridge; full test suite green on `main`.
- **2026-05-07:** **Auto-memory + concurrency** — ported the persistent auto-memory subsystem (user / feedback / project / reference types); concurrency-orchestrator and tool-execution parity with the TypeScript reference; permission-system gaps closed.
- **2026-05-06:** **Subagent parity** — fork-subagent path ported; subagent async lifecycle aligned with TypeScript reference.
- **2026-05-05:** **Docs polish** — added Quick Install section under header; documented the config path written by `clawcodex login`.
- **2026-04-30:** **Skills subsystem parity** — Skills (project + user, named args, tool limits) brought to parity with the TypeScript reference.
- **2026-04-29:** **REPL spinner UX** — show elapsed time and token count in the REPL spinner row.
- **2026-04-27:** **New demo** — Adopt Me-style pet game (React + Vite + Vitest), generated end-to-end by ClawCodex.
- **2026-04-26:** **Reliability fix** — DeepSeek thinking-mode replay failures and noisy permission prompts resolved.
- **2026-04-25:** **DeepSeek support** — direct DeepSeek provider (V4 Pro / Flash via `api.deepseek.com`) plus OpenRouter route.
- **2026-04-25:** **API validation fix** — `AskUserQuestion` options schema fixed for API validation.
- **2026-04-23:** **Generated demos** — CRM, LinkedIn, and Minecraft demo apps (all generated by ClawCodex itself) and a Demos section in the README.
- **2026-04-21:** **Permissions wiring** — `--dangerously-skip-permissions` wired through every entrypoint (REPL, TUI, `-p`).
- **2026-04-20:** **Initial public release** — first commit with project source, docs, tests, and build config.

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
providers = ["anthropic", "openai", "glm", "minimax", "openrouter", "deepseek"]  # OpenAI-compatible & GLM APIs; more can be added
```

### Interactive REPL (default) and Textual TUI (opt-in)

The **default** interactive UI is the inline **prompt_toolkit + Rich** REPL (transcript in scrollback, tool-aware status row). Use **`clawcodex --tui`** or the **`/tui`** slash command inside the REPL to launch the **Textual** in-app experience when you want it.

```text
>>> Hello!
Assistant: Hi! I'm ClawCodex, a Python reimplementation...

>>> /help          # Show commands
>>> /tools         # List registered tools
>>> /tui           # Hand off to the Textual TUI
>>> /stream on     # Live response rendering
>>> /save          # Save session
>>> Tab            # Auto-complete
>>> /explain-code qsort.py   # Run a SKILL.md skill (or /skill …)

# Multi-line input: Shift+Enter, Meta/Alt+Enter, or `\` then Enter for newline; plain Enter submits.
```

### Complete CLI

```bash
clawcodex                       # Inline REPL (default)
clawcodex --tui                 # Textual TUI
clawcodex --stream              # REPL with live rendering
clawcodex login                 # Configure API keys (interactive)
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
| CLI Entry | ✅ | `clawcodex`, `login`, `config`, `-p` / `--print`, `--tui`, `--stream`, `--version` |
| Interactive REPL | ✅ | Default inline REPL; optional Textual TUI; history, tab completion, multiline |
| Multi-Provider | ✅ | Anthropic, OpenAI, Zhipu GLM, Minimax, OpenRouter, DeepSeek — including Anthropic→OpenAI image / document block translation for vision-capable OpenAI-compat backends |
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

1. ask you to choose a provider: anthropic / openai / glm / minimax / openrouter / deepseek
2. ask for that provider's API key
3. optionally save a custom base URL
4. optionally save a default model
5. set the selected provider as default

The configuration file is saved in in `~/.clawcodex/config.json`. Example structure:

```json
{
  "default_provider": "anthropic",
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
    "glm": {
      "api_key": "your-api-key",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "default_model": "zai/glm-5"
    },
    "minimax": {
      "api_key": "your-api-key",
      "base_url": "https://api.minimaxi.com/anthropic",
      "default_model": "MiniMax-M2.7"
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
    "advisor_model": "claude-sonnet-4-6",
    "advisor_client_mode": false,
    "advisor_provider": "openai"
  },
  "env": {
    "TAVILY_API_KEY": "tvly-YOUR-TAVILY-API-KEY"
  }
}
```

- **`session`** — REPL session persistence: `auto_save` writes each session automatically; `max_history` caps retained turns.
- **`settings`** — the advisor model used for background helpers (`advisor_provider` / `advisor_model`, and `advisor_client_mode` to route via the client).
- **`env`** — secrets and environment values injected at startup (e.g. `TAVILY_API_KEY` for web search). Managed via `clawcodex config`; keys here are exported into the process environment without overriding anything you already set in your shell.

### Run

```bash
clawcodex                  # Start inline REPL (same as python -m src.cli)
clawcodex --help           # All flags: --tui, -p, --provider, --model, …
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
| `/tui` | Switch to the Textual TUI |
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



***

## 🎨 Demos

**Every app under [`demos/`](demos/) was generated end-to-end by ClawCodex itself** — same CLI you just installed, same agent loop, same tools. No hand-edits 🙂

| Demo | Stack | Description |
| ---- | ----- | ----------- |
| [`demos/crm-app`](demos/crm-app) | React 18 + Vite + Vitest | Mini CRM with contacts, deals, dashboard, and a full test suite |
| [`demos/linkedin-app`](demos/linkedin-app) | React 18 + Vite + React Router | LinkedIn-style feed: profile, network, jobs, messaging |
| [`demos/minecraft-app`](demos/minecraft-app) | React + three.js + @react-three/fiber | Browser voxel sandbox with terrain, mining, HUD, and player controls |

```bash
cd demos/crm-app   # or linkedin-app / minecraft-app
npm install
npm run dev        # vite dev server
```

Want to see how it's done? Open ClawCodex in any empty directory and ask it to build something — these three were generated exactly that way.

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
- **Inline or TUI** — Default terminal-native REPL; opt-in Textual UI
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
│   ├── entrypoints/        # Headless (-p) and TUI bootstraps
│   ├── repl/               # Inline REPL (prompt_toolkit + Rich)
│   ├── tui/                # Textual UI (--tui, /tui)
│   ├── providers/          # Anthropic, OpenAI, GLM, Minimax, OpenRouter, DeepSeek
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