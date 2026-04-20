# Claude Code Source Code Learning Guide — Index

## Project Overview
- **Project**: Claude Code (Anthropic's official AI programming assistant)
- **Scale**: 1,900+ files, 512,000+ lines of TypeScript code
- **Tech Stack**: TypeScript + React/Ink (terminal UI) + Bun (runtime)
- **Architecture**: Agent-based, Tool-augmented LLM system

---

## Learning Roadmap

| Chapter | File | Topic | Core Files | Est. Time |
|---------|------|-------|------------|-----------|
| 01 | [01_architecture_overview.md](01_architecture_overview.md) | Global Architecture Overview | main.tsx, App.tsx, QueryEngine.ts | 30min |
| 02 | [02_tool_system.md](02_tool_system.md) | Tool System | Tool.ts, tools.ts, GlobTool.ts | 45min |
| 03 | [03_permission_security.md](03_permission_security.md) | Permission & Security | permissions.ts, bashSecurity.ts | 45min |
| 04 | [04_query_loop_api.md](04_query_loop_api.md) | Query Loop & API (Advanced) | query.ts, StreamingToolExecutor.ts | 50min |
| 04b | [04b_context_management.md](04b_context_management.md) | Context Management: Recall, Compression & Progressive Disclosure | context.ts, autoCompact.ts, toolSearch.ts | 40min |
| 05 | [05_multi_agent_system.md](05_multi_agent_system.md) | Multi-Agent System + Coordinator (Advanced) | AgentTool.tsx, runAgent.ts, coordinatorMode.ts | 50min |
| 06 | [06_mcp_extensions.md](06_mcp_extensions.md) | MCP, Skills & Extensions | mcp/client.ts, skills/, bundledSkills.ts | 45min |
| 07 | [07_prompt_engineering.md](07_prompt_engineering.md) | Prompt Engineering Deep Dive | prompts.ts, BashTool/prompt.ts | 60min |
| 08 | [08_voice_buddy.md](08_voice_buddy.md) | Voice Mode & Buddy System | voiceStreamSTT.ts, useVoice.ts, buddy/ | 30min |

**Total ~6 hours** for a systematic understanding of Claude Code's core architecture.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   Claude Code Architecture                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ CLI/REPL │    │ Bridge/IDE   │    │ SDK/Headless     │  │
│  │ (Ink UI) │    │ (VS Code)    │    │ (Programmatic)   │  │
│  └────┬─────┘    └──────┬───────┘    └────────┬─────────┘  │
│       │                 │                      │            │
│       └─────────────────┼──────────────────────┘            │
│                         │                                   │
│                  ┌──────▼──────┐                            │
│                  │  App State  │  (Ch.01)                   │
│                  └──────┬──────┘                            │
│                         │                                   │
│                  ┌──────▼──────┐                            │
│                  │ QueryEngine │  (Ch.04)                   │
│                  │  query()    │                            │
│                  └──┬───┬──┬──┘                            │
│                     │   │  │                                │
│          ┌──────────┘   │  └──────────┐                    │
│          │              │              │                    │
│   ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐            │
│   │ Tool System │ │ Claude   │ │ Permission  │            │
│   │ (Ch.02)     │ │ API      │ │ System      │            │
│   └──────┬──────┘ └──────────┘ │ (Ch.03)     │            │
│          │                      └─────────────┘            │
│   ┌──────┴──────────────────────────────┐                  │
│   │                                     │                  │
│   │  Built-in Tools    MCP Tools        │                  │
│   │  ├── Bash          ├── mcp__db__*   │                  │
│   │  ├── Read          ├── mcp__api__*  │                  │
│   │  ├── Edit          └── ...          │                  │
│   │  ├── Agent (Ch.05)                  │                  │
│   │  └── ...     MCP (Ch.06)            │                  │
│   └─────────────────────────────────────┘                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Suggested Learning Paths

### Beginner Path (2 hours)
1. Chapter 01 → Chapter 02 → Chapter 04
2. Understand: Entry point → Tool definitions → Query loop

### Security Research Path (1.5 hours)
1. Chapter 03 → Chapter 02 (permission section)
2. Understand: Permission model → Command security → Sandbox

### Agent Research Path (2.5 hours)
1. Chapter 05 → Chapter 04 → Chapter 06
2. Understand: Agent architecture + Coordinator → Query loop → MCP/Skills extensions

### Prompt Research Path (1 hour)
1. Chapter 07
2. Understand: System prompts → Tool prompts → Compression prompts → Cache optimization

### Special Features Path (30 minutes)
1. Chapter 08
2. Understand: Voice hold-to-talk → WebSocket STT → Buddy deterministic generation

### Complete Path (6 hours)
1. Read chapters 01 → 08 in order

---

## Key File Reference

| File | Size | Function |
|------|------|----------|
| `src/main.tsx` | 785KB | CLI entry, Commander.js command definitions |
| `src/Tool.ts` | 28KB | Tool interface definitions |
| `src/tools.ts` | 16KB | Tool registry |
| `src/query.ts` | 67KB | Core query loop |
| `src/QueryEngine.ts` | 45KB | Query engine wrapper |
| `src/context.ts` | 6KB | Context management |
| `src/tools/BashTool/bashSecurity.ts` | 100KB | Command security analysis |
| `src/tools/BashTool/bashPermissions.ts` | 96KB | Command permission checks |
| `src/tools/AgentTool/AgentTool.tsx` | ~50KB | Agent tool implementation |
| `src/tools/AgentTool/runAgent.ts` | 34KB | Agent lifecycle |
| `src/services/mcp/client.ts` | 116KB | MCP client |
| `src/services/mcp/config.ts` | ~20KB | MCP configuration management |
