# clawcodex-tui (TypeScript Ink client)

A standalone TypeScript **Ink** TUI client for the clawcodex **Python** agent
backend. It connects to a running `clawcodex agent-server` over the Direct
Connect (`cc://`) NDJSON-over-WebSocket protocol, renders the streamed agent
output, and handles tool-permission prompts. All agent logic — the model loop,
tool execution, permissions, MCP, hooks — runs in the Python backend; this
process only renders and collects input.

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
| `src/client.ts` | Direct Connect client: `POST /sessions` + NDJSON-over-WS |
| `src/sdkMessageAdapter.ts` | SDK message → transcript entries |
| `src/App.tsx` | Ink UI: transcript, live stream, permission prompt, input |
| `src/cli.tsx` | Entry point — parses `cc://`/`http://` URL, renders `App` |
| `scripts/smoke.ts` | Headless connectivity smoke (one turn + permission) |

## Prerequisites

- Node ≥ 18 (or [Bun](https://bun.sh), recommended — runs the TS directly).
- A running Python backend: `clawcodex agent-server` (see `../docs/agent-server.md`).

## Install

```bash
cd ui-tui
bun install         # or: npm install
```

## Run

Start the backend in one terminal (it prints a `cc://` URL):

```bash
clawcodex agent-server
# → agent-server: connect a TUI with  cc://127.0.0.1:53884
```

Then connect the TUI in another terminal:

```bash
# with Bun (no build step):
bun run src/cli.tsx cc://127.0.0.1:53884

# or build + run with Node:
npm run build && node dist/cli.js cc://127.0.0.1:53884
```

If the server was started with `--token T`, pass `--token T` (or set
`CLAWCODEX_TUI_TOKEN`). Use `--cwd DIR` to set the session working directory.

### Keys

- type + Enter — send a prompt
- `y` / `n` — allow / deny a tool-permission prompt
- `Esc` — interrupt the in-flight turn
- `Ctrl-C` — quit

## Typecheck / smoke

```bash
npm run typecheck                                   # tsc --noEmit
bun run scripts/smoke.ts http://127.0.0.1:<port>    # against a live server
```

## Status & scope

This client implements the data plane (init, streaming text, assistant, tool
results, result) and the permission round-trip + interrupt control ops — enough
to drive a real session. It is intentionally minimal (a focused port, not the
full 5000-line reference REPL). Richer UX (slash commands, history, MCP/hook
surfaces, autocomplete from `system/init` tool schemas) is the Phase-4 follow-up
described in the proposal.
