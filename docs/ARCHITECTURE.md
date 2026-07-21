# Architecture

Claw Codex is the Python port of Claude Code. The architecture follows the
**six core abstractions** described in `claude-code-from-source/book/ch01-architecture.md`.
This document maps each abstraction to its canonical Python path so a
reader can navigate from book chapter to source code in one step.

For depth on any subsystem, see the per-chapter gap analyses and
refactoring plans under `my-docs/` (`ch03-state-*`, `ch04-api-layer-*`,
`ch05-agent-loop-*`, etc.).

---

## Six abstractions map

| # | Abstraction | TypeScript reference | Python canonical path | Key entry symbol |
|---|---|---|---|---|
| 1 | **Query Loop** | `typescript/src/query.ts` (~1,919 LOC) | `src/query/query.py` (1,522 LOC) | `query()` — `async def query(params, *, terminal_holder=None) -> AsyncGenerator[Message \| StreamEvent, None]` |
| 2 | **Tool System** | `typescript/src/Tool.ts` + `typescript/src/tools.ts` + `typescript/src/services/tools/` | `src/tool_system/build_tool.py` + `src/tool_system/tools/` (34 tools) | `Tool` dataclass + `build_tool()` factory |
| 3 | **Tasks** | `typescript/src/Task.ts` + `typescript/src/tasks/` + `typescript/src/tools/AgentTool/` | `src/tasks_core.py` (top-level) + `src/tasks/` (per-type) + `src/tool_system/tools/agent.py` | `TaskStatus` Literal; `AgentTool` |
| 4 | **State (two-tier)** | `typescript/src/bootstrap/state.ts` (96 fields) + `typescript/src/state/AppStateStore.ts` (~86 fields) | `src/bootstrap/state.py` (33 fields, see [ch03 plan](../my-docs/ch03-state-refactoring-plan.md)) + `src/state/app_state.py` (5 fields) + `src/utils/store.py` | `_BootstrapState` singleton; `AppState` + `create_app_state_store()` |
| 5 | **Memory** | `typescript/src/memdir/` (8 files incl. team) | `src/memdir/` (6 modules + `__init__.py`) | `find_relevant_memories()`, `memory_scan()` |
| 6 | **Hooks** | `typescript/src/hooks/` + `typescript/src/utils/hooks/`; 27 events | `src/hooks/` (13 modules + `sources/`); 28 events (27 TS + `PostSampling`) | `HookEvent` Literal at `src/hooks/hook_types.py:23` |

The right two columns are the practical entry points. To trace any
chapter from the book into source, start in column 4.

---

## The golden path

User input flows through the system in a fixed sequence. Each arrow is
a function call (or `await`) you can find in the named file.

```text
1. src/cli.py:main                       # console-script entry (pyproject.toml:81)
2. src/cli.py:launch_ink_tui             # interactive entry — spawns the Ink client
3. src/server/agent_server.py            # agent-server the Ink client drives (the
                                         # -p path enters at src/entrypoints/headless.py)
4. src/query/engine.py:QueryEngine       # session-scoped query orchestrator
5. src/query/engine.py:QueryEngine.submit_message
6. src/query/query.py:query              # async generator — the heartbeat
7. ↓ provider call via src/providers/    # streaming model response
8. ↓ tools via src/services/tool_execution/streaming_executor.py
9.    StreamingToolExecutor starts concurrency-safe tools BEFORE the
      model finishes (speculative execution; matches the book's
      §"Tool execution overlaps with model streaming")
10. yields Message | StreamEvent back to the agent-server (src/server/agent_server.py)
11. streamed over the protocol to the TypeScript Ink client for terminal rendering
```

### Terminal discriminated union (PEP 525 note)

Python async generators **cannot return values** (PEP 525). The TS
reference's typed `Terminal` return value is preserved in Python via an
out-parameter pattern: the caller passes a
`src/query/transitions.py:TerminalHolder` to `query()`, and the inner
loop sets `holder.value` to the `Terminal(reason=...)` before the bare
`return`. See `src/query/transitions.py:50-83` for the rationale.

`TerminalReason` is a `Literal[...]` of eleven values
(`src/query/transitions.py:22-34`): `blocking_limit`, `image_error`,
`model_error`, `aborted_streaming`, `prompt_too_long`, `completed`,
`stop_hook_prevented`, `aborted_tools`, `hook_stopped`, `max_turns`,
`tool_failure_loop` — the full set from TS `query/transitions.ts:1-12`.
`tool_failure_loop` is produced by the tool-failure-loop guard
(`src/query/tool_failure_loop_guard.py`, port of TS
`query/toolFailureLoopGuard.ts`): when consecutive tool batches contain
only failures and the same failure signature, error category, or file
path recurs `CLAUDE_CODE_TOOL_FAILURE_LOOP_THRESHOLD` (default 3)
times, the loop yields an explanatory API-error assistant message and
stops instead of burning turns to `max_turns`.

---

## Permission system

Claw Codex implements the same 7 permission modes as Claude Code:

| Mode | Behavior | External? |
|------|----------|-----------|
| `bypassPermissions` | All allowed, no prompts, no logging. Internal/testing. | yes |
| `dontAsk` | All allowed, logged. No user prompts. | yes |
| `auto` | Transcript classifier (LLM) decides allow/deny. | no (internal) |
| `acceptEdits` | File edits auto-approved; other mutations prompt. | yes |
| `default` | Standard interactive mode. User approves each action. | yes |
| `plan` | Read-only. All mutations blocked. | yes |
| `bubble` | Escalate decision to parent agent (sub-agent mode). | no (internal) |

Definitions live at `src/permissions/types.py:13-47`. The resolution
chain (hook rule → `tool.check_permissions` → mode-based decision) is
implemented in `src/permissions/check.py` (489 LOC) with the mode-cycle
helper at `src/permissions/cycle.py`.

---

## Hooks

The TS reference defines 27 lifecycle events
(`typescript/src/entrypoints/sdk/coreTypes.ts:25`); the Python
implementation defines 28 (`src/hooks/hook_types.py:23`) — the 27 TS
events plus an extra `PostSampling`. The 4 TS execution types (shell,
prompt, agent, http) map to 3 Python executors (`exec_prompt_hook.py`,
`exec_agent_hook.py`, `exec_http_hook.py`); the shell-hook flow is
subsumed under the bash tool path.

---

## Memory

Three tiers, matching the book chapter:

1. **Project-level** — `CLAWCODEX.md` files in the repo (loaded by
   `src/memdir/memory_scan.py`).
2. **User-level** — `~/.clawcodex/projects/<project-slug>/memory/MEMORY.md` (loaded by
   `src/memdir/paths.py`).
3. **Team-level** — shared via symlinks. **Currently unported**; see
   [ch11 plan](../my-docs/) when it lands.

Relevance selection is LLM-driven via
`src/memdir/find_relevant_memories.py`.

---

## Multi-provider

The book describes TS Claude Code's multi-cloud routing: one vendor
(Anthropic) across four clouds (Direct API, AWS Bedrock, Google Vertex AI,
Azure Foundry), all transparent to the loop via `getAnthropicClient()`.

**Claw Codex routes differently.** The Python `get_provider_class()`
factory at `src/providers/__init__.py:173` selects between six
**vendors**, one cloud each:

- Anthropic (Direct API)
- OpenAI
- GLM (Zhipu)
- MiniMax
- DeepSeek
- OpenRouter (multi-vendor proxy)

All implement the `BaseProvider` protocol at `src/providers/base.py`.
The factory pattern is the same as TS; the routing dimension is
different.

### Not currently supported

The Anthropic-cloud routing dimension (AWS Bedrock, Google Vertex AI,
Azure Foundry) has **no Python equivalent**. A user setting
`ANTHROPIC_BEDROCK_PROVIDER=1` will not get Bedrock routing — that
shim does not exist in `src/providers/`. The
[ch04 plan](../my-docs/ch04-api-layer-refactoring-plan.md) tracks the
gap if/when it becomes a product priority.

---

## Audit-only scaffolding (not on the golden path)

The TS↔Python parity-reporting CLI lives at `scripts/audit/`
(relocated from `src/` top level in ch01 round-2 P3). Run it via
`python -m scripts.audit.main <sub>` (exercised by
`tests/test_porting_workspace.py`); the production `clawcodex`
console script never touches any of it. The relocation rationale
is in
[my-docs/ch01-architecture-round2-plan.md](../my-docs/ch01-architecture-round2-plan.md).

Nothing remaining at `src/` top level is audit-only. If you see a
top-level `src/<name>.py`, it is on the production path.
