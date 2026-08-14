# ClawCodex Web

The browser client. Same agent, same gateway, same sessions as the TUI and the
desktop app — a third front end, not a second product.

```bash
clawcodex web --build      # build the bundle, then serve and open a browser
clawcodex web              # once built
```

## What it is

`clawcodex web` is not a separate server. It is `clawcodex serve` — the
existing JSON-RPC gateway the desktop app already talks to — with this app's
built `dist/` mounted on it. So a browser tab drives the same in-process agent
the TUI does, over the same socket, against the same saved sessions.

```
  browser  ──HTTP/WS──▶  clawcodex serve  ──▶  in-process agent (src/server/agent_server.py)
   ui-web                /api/ws                 the same one the TUI runs
```

The whole coupling to the backend lives in four files:

| File | Responsibility |
| --- | --- |
| `src/gateway/protocol.ts` | The wire vocabulary, as types. The one place the client states what the server says. |
| `src/gateway/client.ts` | The socket: request/response by id, pushes by type, reconnect with backoff. |
| `src/gateway/tool-vocabulary.ts` | ClawCodex tool names → the names the tool cards are keyed by (mirrors the server's own table, for rehydrated transcripts). |
| `src/state/transcript.ts` | Gateway events → renderable nodes. Every rule about what the reader sees is a pure function here. |

Everything above those is ordinary UI and knows nothing about JSON-RPC.

## Layout

```
src/
  gateway/       protocol types, socket client, token/backend discovery
  state/         stores (nanostores), actions, the transcript reducer, theme
  layout/        three-column solver + AppFrame (drag handles, concession chain)
  sidebar/       project/worktree/session tree
  conversation/  chat flow, message + tool + reasoning rows, composer, approvals
  details/       right-hand column: session facts, files touched, tool runs
  ui/            primitives (buttons, cards, code/diff/terminal blocks) + markdown
  styles/        design tokens, typography, scrollbars, shiki wiring
```

Two structural rules hold throughout:

- **The conversation column owns exactly one scrollport**, holding both the
  transcript and the sticky composer seat. That is why a wheel gesture over the
  input card still scrolls the conversation.
- **One width axis.** `--cc-chat-content-width` sizes the transcript and the
  dock cards; the input card is exactly that plus 32px, at every viewport. The
  relation is declared once, on the conversation root.

## Development

```bash
npm install
npm run typecheck
npm run test
npm run build          # → dist/, what `clawcodex web` serves

# Live reload against a running backend:
clawcodex serve --host 127.0.0.1 --port 8317 --token dev
npm run dev            # http://127.0.0.1:5175/?token=dev  (proxies /api to 8317)
```

`CLAWCODEX_WEB_DIST=/path/to/dist` points the server at a bundle elsewhere; it
is authoritative, so a path with no bundle in it means "no bundle" rather than
a silent fall back to the checkout's.
`CLAWCODEX_WEB_SOURCEMAP=1` builds with sourcemaps (off by default: they more
than double the bundle, and only a developer with devtools open fetches them).

## Serving and the token

The gateway is token-gated, and a browser has no way to learn that token on its
own — so `GET /` serves this app with the token inlined as
`window.__CLAWCODEX_SESSION_TOKEN__` (the same global the desktop shell already
scrapes to adopt a running backend). The client reads it, or takes a `?token=`
from the URL and strips it from the address bar.

That page hands out the token, which is safe exactly as long as the server is
reachable from this machine only. `clawcodex web` therefore **refuses a
non-loopback `--host`** unless you pass `--allow-remote` and put your own
authentication in front of it.

## Packaging

A `pip`-installed ClawCodex does not ship a built bundle yet: `clawcodex web`
looks for `ui-web/dist` in a source checkout, then for a packaged
`src/server/web_dist`, and tells you how to build one when it finds neither.
`clawcodex web --build` runs the npm build for you (Node required).

## Notice

The visual design and several structural ideas are adapted from the
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) web client,
which is MIT licensed:

> MIT License — Copyright (c) 2026 DeepSeek

Adapted here: the design-token architecture (raw palette → semantic aliases →
surface-specific roles, with only the aliases moving between themes), the
three-column concession solver, the single-scrollport conversation column with
its sticky composer seat and shared width axis, and the tool-card family
(terminal / diff / read / generic). The DeepSeek branding, the cordis plugin
runtime, and the client module system are not used; the protocol layer is
ClawCodex's own gateway, which is a different contract entirely.
