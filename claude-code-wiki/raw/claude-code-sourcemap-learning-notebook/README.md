# Claude Code Sourcemap Learning Notebook

[![GitHub](https://img.shields.io/badge/GitHub-dadiaomengmeimei-blue?logo=github)](https://github.com/dadiaomengmeimei/claude-code-sourcemap-learning-notebook)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🌐 [中文版 / Chinese Version](README_CN.md)

**Reverse-engineering Claude Code's 512K+ lines of TypeScript — extracting the architecture, design decisions, and 11 transferable patterns that make a production AI Agent work.**

Not just a code walkthrough. An architectural analysis that explains *why* each design choice was made, with alternatives compared.

## What You'll Learn

- 🔄 **Query Loop** — Streaming tool execution, read-write lock concurrency, 3-layer AbortController cascade
- 🛠️ **Tool System** — `buildTool()` factory + Zod schema as prompt, 40+ tools with 18+ feature flags
- 🔒 **5-Layer Security** — Permission rules → mode → tool checks → path safety → macOS Seatbelt sandbox
- 🤖 **Multi-Agent** — Coordinator orchestration, default isolation with explicit sharing, cache-friendly forking
- 🔌 **MCP & Skills** — 3-layer skill architecture, bundled skill lazy extraction, `/simplify` 3-agent parallel review
- 🧠 **Prompt Engineering** — 7 categories with 40+ prompt files, static/dynamic separation for cache optimization
- 🎙️ **Voice & Buddy** — Record-first-connect-later latency elimination, deterministic generation

## Chapters

| # | Topic | Core Source Files | Time |
|---|-------|-------------------|------|
| [00](zh-CN/00_index.md) | Index & Architecture Overview | — | 10min |
| [01](zh-CN/01_architecture_overview.md) | Global Architecture | `main.tsx`, `App.tsx`, `QueryEngine.ts` | 30min |
| [02](zh-CN/02_tool_system.md) | Tool System | `Tool.ts`, `tools.ts`, `GlobTool.ts` | 45min |
| [03](zh-CN/03_permission_security.md) | Permission & Security | `permissions.ts`, `filesystem.ts` | 45min |
| [04](zh-CN/04_query_loop_api.md) | Query Loop & API ⭐ | `query.ts`, `StreamingToolExecutor.ts` | 50min |
| [05](zh-CN/05_multi_agent_system.md) | Multi-Agent + Coordinator ⭐ | `AgentTool.tsx`, `runAgent.ts` | 50min |
| [06](zh-CN/06_mcp_extensions.md) | MCP, Skills & Extensions | `mcp/client.ts`, `skills/` | 45min |
| [07](zh-CN/07_prompt_engineering.md) | Prompt Engineering | `prompts.ts`, `BashTool/prompt.ts` | 60min |
| [08](zh-CN/08_voice_buddy.md) | Voice Mode & Buddy | `voiceStreamSTT.ts`, `buddy/` | 30min |

**Total ~5.5 hours** for a systematic understanding of Claude Code internals.

> Course content available in [Chinese (zh-CN/)](zh-CN/) and [English (en/)](en/).

## Learning Paths

| Path | Chapters | Time |
|------|----------|------|
| **Quick Start** | 00 → 01 → 02 → 04 | 2h |
| **Security Focus** | 00 → 03 → 02 | 1.5h |
| **Agent Research** | 00 → 05 → 04 → 06 | 2.5h |
| **Prompt Research** | 00 → 07 | 1h |
| **Full Course** | 00 → 01 → ... → 08 | 5.5h |

## Key Discoveries

| Chapter | Most Valuable Finding |
|---------|----------------------|
| 01 | React/Ink terminal UI — CLI can use React |
| 02 | `buildTool()` factory + Zod schema doubles as prompt |
| 03 | 5-layer defense; bypass mode still has safety floor (.git/.claude protected) |
| 04 | `StreamingToolExecutor` concurrent execution, 5-layer compression pipeline |
| 05 | Coordinator star topology, fork prompt cache sharing, 10-step finally cleanup |
| 06 | Skills 3-layer architecture, `/simplify` 3-agent parallel review |
| 07 | 40+ prompt files with static/dynamic separation, Meta-Prompt generates Agents |
| 08 | Record-first-connect-later eliminates latency, Buddy Bones not persisted |

## 11 Transferable Design Patterns

Extracted from Ch.04 & Ch.05 — applicable to any Agent system:

**From Query Loop:** Optimistic Recovery · Layered Degradation · State Machine + Transition Log · Read-Write Lock Concurrency · Immutable Config Snapshot · Hierarchical Cancellation

**From Multi-Agent:** Capability-based Security · Cache-Friendly Forking · Deterministic Cleanup · Star Topology Orchestration · Monotonic Permission Narrowing

> Each pattern includes Claude Code implementation details and universal applications (DB, K8s, microservices, etc.) in the chapter notes.

## Sister Project: nano-claude-code

> **1,646 lines of TypeScript · 15 files · 4 dependencies**

[nano-claude-code](https://github.com/dadiaomengmeimei/nano-claude-code) rewrites Claude Code's core as a minimal, hackable implementation you can actually run and modify.

| | Claude Code | nano-claude-code |
|---|---|---|
| Files | ~1,900 | **15** |
| Lines | 512,000+ | **1,646** |
| Dependencies | 50+ | **4** |
| Tools | 40+ | **6** |

**Read the notebook to understand the architecture. Run nano-claude-code to build your own.**

👉 [github.com/dadiaomengmeimei/nano-claude-code](https://github.com/dadiaomengmeimei/nano-claude-code)

## Links

- [Claude Code Official Docs](https://docs.anthropic.com/en/docs/claude-code)
- [MCP Protocol Specification](https://modelcontextprotocol.io/)
- [This Project on GitHub](https://github.com/dadiaomengmeimei/claude-code-sourcemap-learning-notebook)

## License

MIT. The analyzed Claude Code source code is copyrighted by Anthropic. This project is for educational and research purposes only.

---

**If this helps you, give it a ⭐! Follow [@dadiaomengmeimei](https://github.com/dadiaomengmeimei) for more AI source code analysis.**
