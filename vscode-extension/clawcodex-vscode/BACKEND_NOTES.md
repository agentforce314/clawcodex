# Backend notes: how clawcodex-vscode talks to clawcodex

Design decisions and wire contracts between this extension and the clawcodex backend.
Useful when debugging the chat, extending the protocol layer, or auditing what the
extension reads and writes on disk.

## 1. Chat backend: `agent-server --stdio`

The chat spawns

```
clawcodex agent-server --stdio --workspace <cwd> --permission-mode <mode>
                       [--provider <p>] [--model <m>]
```

— the same backend the bundled Ink TUI uses — and speaks its NDJSON protocol over
stdin/stdout: `system/init`, `assistant`/`user` message envelopes, `stream_event`
`content_block_delta` (`text_delta` | `thinking_delta`), `control_request` subtype
`can_use_tool` for permissions, and a per-turn terminal `result`.

clawcodex's headless mode (`clawcodex -p --output-format stream-json ...`) was
deliberately **not** used: it auto-denies interactive permission asks and emits a
reduced event set (no `control_request`, no per-turn `result`, no thinking deltas), so
the permission cards, thinking indicator, and turn lifecycle in this extension only
function against the agent-server.

## 2. Client→server RPCs

Permission prompts flow server→client (`control_request` → the extension answers with
`control_response`). The same envelope pair also runs in the opposite direction: the
client may send `control_request {request_id, request: {subtype}}` and the server
answers with `control_response {response: {request_id, response}}`.
`ProcessManager.sendControlRequest` correlates replies by `request_id` (pending-RPC map
with timeout); matched replies are consumed, everything else flows to the chat handler.
Used for:

- `interrupt` — **abort**. Fire-and-forget (the server sends no reply); the server
  emits `result/cancelled`. A signal-based kill is wrong here — it would take down the
  whole server and its session state instead of just the in-flight turn.
- `resume {session_id}` — session resume after spawn (there is no `--resume` spawn
  flag). The webview restores the transcript from disk while this control reloads the
  server-side conversation. A `{ok:false, error}` reply surfaces as an error banner.
- `list_sessions` — available (used opportunistically); the primary session list is
  disk-based so Resume works before any server is running.

## 3. Permission replies

`allow-session` persists rules by echoing the request's `suggestions` back as
`chosen_updates` — that is the field the agent-server's permission resolver consumes
and persists. The permission card renders the server's companion fields: `warning`
(destructive-command caution), `session_label` (exact grant wording, rendered as
"Yes, …"), and `plan` (ExitPlanMode approval body). All are escaped before entering
the DOM.

## 4. Sessions on disk

Saved sessions live as flat JSON files at `~/.clawcodex/sessions/<id>.json`
(`session_id`, `updated_at` in epoch **seconds**, `preview`, `name` (nullable),
`message_count`, `model`, `cwd`, `conversation.messages`), honoring
`$CLAWCODEX_CONFIG_DIR`. There is **no `~/.claude` fallback** — clawcodex never shares
state with a Claude Code install. The workspace filter realpaths both sides (the server
stores resolved paths; VS Code hands out unresolved ones — think `/var` vs
`/private/var`); if the workspace itself can't be resolved the list degrades to
show-all rather than an empty resume picker. The scan parses every session file
(parity with the server's own `list_sessions`) and caps only the OUTPUT — with a flat
store, an input-side cap could push all of the current workspace's sessions out of the
scanned window whenever other projects were touched more recently, silently emptying
Resume. `listSessions` runs async off the UI thread, so the full scan is affordable.

## 5. Provider integration

clawcodex providers live in `~/.clawcodex/config.json` (`default_provider`,
`providers.<name>` with `api_key` / `base_url` / `default_model`) with per-provider
env-var key fallback, and are selected via `--provider`/`--model`:

- `clawcodex.provider` / `clawcodex.model` settings become chat spawn flags; the
  `Open Provider Config` command opens the config read-only.
- The Control Center's provider badge derives from that config plus known env-key
  candidates (`setting` / `config` / `env` / `unknown` sources — `unknown` is shown
  honestly rather than guessed). The extension reads the config; it never writes it
  (credential setup belongs to the CLI).
- The workspace-level file is `<workspace>/.clawcodex/settings.json` (project
  settings): Found/Missing/Invalid/Unreadable states, a file watcher, and an open
  action.

## 6. Streaming-state derivation

The agent-server emits only `content_block_delta` stream events — no
`message_start`/`content_block_start`. Streaming state (status-bar spinner, typing
indicator) flips on the first delta or tool activity of a turn; the thinking block
shows on the first `thinking_delta` and hides when text starts or the turn ends.

## 7. stderr policy

The agent-server reserves stdout for JSON frames and logs diagnostics to stderr. Only
stderr lines matching `/error|traceback|exception|fatal|critical/i` surface as chat
errors — substring on purpose, so `ValueError:`-style lines match; everything else is
log noise and dropped.

## 8. New Chat cold start

"New chat" kills the process and respawns on the next message, so each fresh chat pays
the agent-server cold start (provider + tool registry + MCP build) before the first
token. Safe: user messages sent before init queue server-side. The server's `clear`
control could keep the process warm; kill+respawn was kept for lifecycle simplicity
and robustness.

## 9. Smaller notes

- `permissionMode` exposes `default | acceptEdits | plan | bypassPermissions | auto`.
  `dontAsk` exists server-side but is deliberately not exposed in the settings enum.
- `system` subtypes beyond `init`/`status` (e.g. goal status carriers) are ignored —
  they are TUI concerns. Data-only `status` frames (no `message`) don't touch the
  status pill.
- `result.session_id` updates the tracked session id every turn, so a crash-restart
  resumes the file the server was actually writing.
- `rate_limit` / `tool_progress` message types are not emitted by the agent-server and
  have no renderer cases.
- Tests run under plain `node --test` with a local functional `vscode` stub
  (`test/register-vscode-stub.js`). Requires **Node ≥21** (the test-runner glob); the
  extension itself runs on the VS Code host's Node.
