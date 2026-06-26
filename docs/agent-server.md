# Agent server + TypeScript Ink TUI client

This implements the TUI redesign from `my-docs/tui-interface-redesign/`: run the
mature **TypeScript Ink TUI** as a thin **client** of the **Python** agent
backend, over the existing Direct Connect (`cc://`) protocol. The Python side
runs the agent loop, tools, permissions, MCP, and hooks; the TUI renders and
collects input. Because both speak the same NDJSON-over-WebSocket protocol, this
is "finish Direct Connect," not a new bridge.

```
┌── terminal ─────────────────┐         ┌── Python: clawcodex agent-server ──┐
│ TS Ink TUI (ui-tui/)│  cc://  │ DirectConnectServer (src/server/)  │
│  • renders streamed turn    │ <─────> │  • make_spawn_agent → query()      │
│  • permission prompt (y/n)  │  NDJSON │  • serves can_use_tool round-trip  │
│  • prompt input             │   / WS  │  • runs ALL tools (same filesystem)│
└─────────────────────────────┘         └────────────────────────────────────┘
```

## Components

| Piece | Path |
|---|---|
| Agent server (real `SpawnAgent` driving `query()`) | `src/server/agent_server.py` |
| Direct Connect server (HTTP `/sessions` + WS pump) | `src/server/server.py` (pre-existing) |
| CLI entrypoint | `src/entrypoints/agent_server_cli.py` → `clawcodex agent-server` |
| Python Direct Connect client (used by tests) | `src/server/direct_connect_manager.py` (pre-existing) |
| End-to-end tests | `tests/server/test_agent_server_e2e.py` |
| TypeScript Ink client | `ui-tui/` |

## Build / run

### Quick start — one command

```bash
cd ui-tui && bun install && cd ..   # one-time: install the TUI deps
clawcodex tui                               # starts the backend + TUI together
```

`clawcodex tui` launches the **Ink TUI as the parent**, which **spawns + owns
the Python agent-server as a child** (the hermes-agent route): the client starts
`clawcodex agent-server` itself on an ephemeral loopback port + per-launch token,
reads its `cc://` URL, connects, and tears the child down on exit (the backend
runs with `--exit-on-parent`, so it also dies if the TUI crashes). It auto-detects
a runner (`bun`, else a built `node` dist); override with `CLAWCODEX_TUI_CMD`,
point at the client with `--tui-dir`, or use `--print-connect` to run only the
server and print the `cc://` URL + token.

> Why a server at all? The TUI is TypeScript and the engine is Python — two
> runtimes that can't share memory, so they talk over the Direct Connect
> WebSocket protocol. `clawcodex tui` just manages both ends for you. The
> two-process pieces below are for running them separately (e.g. attaching the
> reference `claude open cc://…` client, or remote use).

### Separate processes

#### 1. Backend — `clawcodex agent-server`

```bash
clawcodex agent-server                 # ephemeral port, default provider/model
clawcodex agent-server --port 8791 \
  --provider anthropic --model claude-opus-4-8 \
  --permission-mode default --token "$(openssl rand -hex 16)"
```

On start it prints the URLs to connect to:

```
agent-server: protocol v0.1.0
agent-server: listening on http://127.0.0.1:8791  (POST /sessions)
agent-server: connect a TUI with  cc://127.0.0.1:8791
```

It uses the same provider/credential resolution as headless mode, so configure
credentials with `clawcodex login` first. The server binds `127.0.0.1` only; use
`--token` to require a bearer token on `POST /sessions`.

### 2. Frontend — the Ink TUI

```bash
cd ui-tui
bun install
bun run src/cli.tsx cc://127.0.0.1:8791          # Bun: no build step
# or: npm install && npm run build && node dist/cli.js cc://127.0.0.1:8791
```

See `ui-tui/README.md` for keys and options.

### Alternative frontend

The reference `claude open cc://127.0.0.1:8791` path (the original TS Direct
Connect client) targets the same protocol; whether it renders a full local turn
unchanged is a Phase-3 verification item (see proposal §11 Q6).

## Wire protocol (v0.1.0)

NDJSON, one JSON object per line. `system/init` carries `protocol_version`;
clients refuse a mismatched major.

- **server → client:** `system/init` (model, tools+schemas, permission_mode,
  protocol_version), `stream_event` (token deltas), `assistant`, `user`
  (tool results), `result` (usage/num_turns), `control_request{can_use_tool}`.
- **client → server:** `user` (prompt), `control_response` (permission reply),
  `control_request{interrupt | set_permission_mode | set_model | get_settings |
  get_context_usage}`.

A permission ask is a synchronous round-trip: the server's permission handler
(run on a worker thread so it never blocks the WS loop) emits a
`can_use_tool` request and blocks until the client's `control_response`, with a
server-side default-deny timeout so a dead client can't wedge a tool.

## Tests

```bash
# Python (server + agent-server)
PYTHONPATH="$PWD" python -m pytest tests/server/ -o addopts="" -q

# TypeScript typecheck + live smoke
cd ui-tui && npm run typecheck
bun run scripts/smoke.ts http://127.0.0.1:<port>   # against a running server
```

`tests/server/test_agent_server_e2e.py` drives the **real** Direct Connect
client against the **real** server + agent-server with a stubbed provider (no
network): streaming turn, permission allow/deny, interrupt, and the control-op
round-trip. The cross-language path (TS client ↔ Python server) is covered by
`ui-tui/scripts/smoke.ts`.

## What is and isn't done

**Implemented + tested:**
- Python agent-server: full data plane (init+schemas, live streaming, assistant,
  tool results, result+usage), permission round-trip, interrupt, `set_*`/`get_*`
  control ops, protocol versioning, `clawcodex agent-server` CLI.
- TypeScript Ink client: protocol + client + adapter + minimal REPL UI; typechecks
  and passes a live cross-language smoke.

**Follow-ups (per the proposal):**
- **Phase 4 — thick-surface parity:** slash commands, history/resume, MCP/hook
  surfaces, queued prompts, autocomplete seeded from `system/init` tool schemas
  (+ dynamic refresh on MCP/mode/model change).
- **Phase 5 — packaging:** ship the TS client as a bundled binary alongside the
  pip package; cross-language conformance tests against the protocol schema.
- **Phase 6 — cutover:** default to the TS TUI with the Textual UI as a fallback;
  triage the Python-TUI bug backlog (R/L/P rubric) to confirm the payoff.
- **Transport:** v1 is localhost WebSocket (reuses `src/server/server.py`); a
  stdio variant (no listener, better trust boundary) is an evaluated option.
