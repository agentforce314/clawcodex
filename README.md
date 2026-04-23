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

</div>

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
providers = ["anthropic", "openai", "glm", "minimax"]  # OpenAI-compatible & GLM APIs; more can be added
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
| Multi-Provider | ✅ | Anthropic, OpenAI, Zhipu GLM, Minimax |
| Session Persistence | ✅ | Save/load sessions locally |
| Agent Loop | ✅ | Tool calling loop with streaming and headless mode |
| Skill System | ✅ | SKILL.md-based slash-command skills with args + tool limits |
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

1. ask you to choose a provider: anthropic / openai / glm / minimax
2. ask for that provider's API key
3. optionally save a custom base URL
4. optionally save a default model
5. set the selected provider as default

The configuration file is saved in in `~/.clawcodex/config.json`. Example structure:

```json
{
  "default_provider": "glm",
  "providers": {
    "anthropic": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-6"
    },
    "openai": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-5.4"
    },
    "glm": {
      "api_key": "base64-encoded-key",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "default_model": "zai/glm-5"
    },
    "minimax": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.minimaxi.com/anthropic",
      "default_model": "MiniMax-M2.7"
    }
  }
}
```

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

## 📦 Project Structure

```text
clawcodex/
├── src/
│   ├── cli.py              # CLI entry (console: clawcodex)
│   ├── entrypoints/        # Headless (-p) and TUI bootstraps
│   ├── repl/               # Inline REPL (prompt_toolkit + Rich)
│   ├── tui/                # Textual UI (--tui, /tui)
│   ├── providers/          # Anthropic, OpenAI, GLM, Minimax
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