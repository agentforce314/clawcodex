# clawcodex-tui (TypeScript Ink client)

A standalone TypeScript **Ink** TUI client for the clawcodex **Python** agent
backend, talking the Direct Connect (`cc://`) NDJSON-over-WebSocket protocol.
All agent logic — the model loop, tool execution, permissions, MCP, hooks — runs
in the Python backend; this process renders the streamed output and collects
input.

**Architecture (the hermes-agent route): the TUI is the parent and spawns +
owns the Python backend as a child.** Run with no URL and the client spawns
`clawcodex agent-server` itself (`src/spawnBackend.ts`), reads the child's
`cc://` URL, connects, and tears the child down on exit — the backend even dies
if the TUI crashes (it runs with `--exit-on-parent`, exiting on stdin EOF when
the parent's pipe closes). Pass a `cc://` URL to instead **attach** to an
already-running server:

```text
  clawcodex tui                      node ui-tui/dist/cli.js
    └─ spawns ─▶  node ui-tui  ─ spawns ─▶  python -m src.entrypoints.agent_server_cli
                  (TUI, parent)             (agent-server, child it owns)
```

This is **Phase 3** of the TUI redesign (`my-docs/tui-interface-redesign/`).
It is kept deliberately separate from the reference implementation under
`typescript/` (which is the original Claude Code source). The wire protocol and
client logic are ported from `typescript/src/server/directConnectManager.ts` and
`typescript/src/remote/sdkMessageAdapter.ts`, implemented against the npm `ink`
package rather than the reference tree's vendored Ink fork.

## Layout

| File | Purpose |
|---|---|
| `src/protocol.ts` | Wire types shared with the Python server + helpers |
| `src/client.ts` | Direct Connect client: `POST /sessions` + NDJSON-over-WS + control ops |
| `src/sdkMessageAdapter.ts` | SDK messages → transcript entries (text, tool calls, tool results) |
| `src/markdown.tsx` | Dependency-free Markdown → Ink renderer (streaming-tolerant) |
| `src/theme.ts` | Color palette + glyphs |
| `src/slashCommands.ts` | Slash-command registry + matching |
| `src/components/` | `Message`, `Spinner`, `StatusBar`, `PermissionDialog`, `SlashMenu` |
| `src/App.tsx` | Ink UI: transcript, live markdown stream, spinner, slash menu, permission, status |
| `src/cli.tsx` | Entry point — parses `cc://`/`http://` URL, renders `App` |
| `scripts/smoke.ts` | Headless connectivity smoke (one turn + permission) |

## Prerequisites

- Node ≥ 18 (or [Bun](https://bun.sh), recommended — runs the TS directly).
- For attach mode only: a running `clawcodex agent-server` (see `../docs/agent-server.md`).

## Install

```bash
cd ui-tui
bun install && npm run build   # or just `bun install` to run the TS directly
```

## Run

### Spawn mode (default) — one command, the TUI owns the backend

```bash
clawcodex tui          # spawns node ui-tui, which spawns the python agent-server
```

The TUI starts the backend itself (no URL needed). Under the hood it runs the
client with no URL; the client spawns `clawcodex agent-server` on an ephemeral
loopback port + per-launch token, then connects. First launch shows
`starting agent-server…` for ~20s while the Python agent stack imports.

### Attach mode — connect to an already-running server

```bash
clawcodex agent-server                       # terminal 1 → prints a cc:// URL + token
node dist/cli.js cc://127.0.0.1:<port> --token <token>   # terminal 2 (or: bun run src/cli.tsx …)
```

Pass `--token T` (or set `CLAWCODEX_TUI_TOKEN`) to match the server's `--token`.
Use `--cwd DIR` to set the session working directory.

### Keys

- type + Enter — send a prompt
- `/` — open the slash-command menu (`↑↓` select · `tab` complete · enter run)
- `y` / `n` — allow / deny a tool-permission prompt
- `Esc` — interrupt the in-flight turn
- `Ctrl-C` — quit

Slash commands: `/help`, `/clear`, `/model <name>`, `/mode <default|acceptEdits|plan|…>`, `/quit`.

## Typecheck / smoke

```bash
npm run typecheck                                   # tsc --noEmit
bun run scripts/smoke.ts http://127.0.0.1:<port>    # against a live server
```

## Status & scope

This is a **purpose-built Claude-Code-style TUI** (the hermes-agent approach —
own client + own rendering, not a fork of the reference REPL). It renders:
streaming **Markdown** assistant output, **tool calls** (`⏺ Bash(…)`) + indented
results (`⎿`), an animated **working spinner**, a **slash-command menu** with
tab-complete, a bordered **permission dialog**, and a live **status bar**
(model · mode), over the Direct Connect protocol. Verified end-to-end against
`clawcodex agent-server` (streamed turn + usage).

Still ahead (grow incrementally): multiline input, input history, `@`-file
mentions, autocomplete seeded from `system/init` tool schemas, session
resume/history, and richer tool-result rendering.
