# Port notes: openclaude-vscode → clawcodex-vscode

This extension is a port of openclaude's `vscode-extension/openclaude-vscode` (the
in-repo reference under `typescript/`, not shipped with clawcodex) onto the clawcodex
backend. The module layout, webview UI, launch-target resolution, and test structure
follow the reference; the wire protocol and provider integration are clawcodex-native.
This file records every deliberate divergence.

## 1. Chat backend: `agent-server --stdio`, not `--print` stream-json

The reference spawns `openclaude --print --input-format=stream-json
--output-format=stream-json --include-partial-messages`. clawcodex has that flag surface
too (`clawcodex -p ...`), but its headless mode **auto-denies** interactive permission
asks and emits a reduced event set (no `control_request`, no per-turn `result`, no
thinking deltas). The functional equivalent of what the reference consumes is the
clawcodex **agent-server** — the same backend the bundled Ink TUI uses:

```
clawcodex agent-server --stdio --workspace <cwd> --permission-mode <mode>
                       [--provider <p>] [--model <m>]
```

It speaks the same SDK wire family the reference extension was built for: `system/init`,
`assistant`/`user` message envelopes, `stream_event` `content_block_delta`
(`text_delta` | `thinking_delta`), `control_request` subtype `can_use_tool` for
permissions, and a per-turn terminal `result`.

## 2. Client→server RPCs (new vs the reference)

In the reference, `control_response` only ever flowed client→server (permission replies).
The agent-server also uses the reverse direction: the client may send
`control_request {request_id, request: {subtype}}` and the server answers with
`control_response {response: {request_id, response}}`. `ProcessManager.sendControlRequest`
correlates replies by `request_id` (pending-RPC map with timeout); matched replies are
consumed, everything else flows to the chat handler. Used for:

- `interrupt` — **abort**. Fire-and-forget (no reply); the server emits
  `result/cancelled`. The reference sent SIGINT, which here would kill the whole server
  and its session state.
- `resume {session_id}` — session resume after spawn. There is no `--resume` spawn flag;
  the webview restores the transcript from disk while this control reloads the
  server-side conversation. A `{ok:false, error}` reply surfaces as an error banner.
- `list_sessions` — available (used opportunistically); the primary session list is
  disk-based so Resume works before any server is running.

## 3. Permission replies

`allow-session` persists rules by echoing the request's `suggestions` back as
`chosen_updates` — the reference's `updatedPermissions` field is ignored by this server.
The permission card renders the server's extra fields: `warning` (destructive-command
caution), `session_label` (exact grant wording, rendered as "Yes, …"), and `plan`
(ExitPlanMode approval body). All are escaped before entering the DOM.

## 4. Sessions on disk

`~/.clawcodex/sessions/<id>.json` (flat JSON: `session_id`, `updated_at` in epoch
**seconds**, `preview`, `name` (nullable), `message_count`, `model`, `cwd`,
`conversation.messages`) replaces the reference's `~/.openclaude/projects/<dir>/*.jsonl`.
`$CLAWCODEX_CONFIG_DIR` is honored; there is **no `~/.claude` fallback** — clawcodex
never shares state with a Claude Code install. The workspace filter realpaths both sides
(the server stores resolved paths; VS Code hands out unresolved ones — think `/var` vs
`/private/var`); if the workspace itself can't be resolved the list degrades to
show-all rather than an empty resume picker. The scan parses every session file (server
parity with `_list_saved_sessions`) and caps only the OUTPUT — with a flat store, an
input-side cap could push all of the current workspace's sessions out of the scanned
window whenever other projects were touched more recently, silently emptying Resume.
`listSessions` runs async off the UI thread, so the full scan is affordable.

## 5. Provider integration replaces the Azure/Foundry subsystem

openclaude selects providers via env injection (`CLAUDE_CODE_USE_OPENAI`, `OPENAI_*`,
Azure wizard + SecretStorage). clawcodex providers live in `~/.clawcodex/config.json`
(`default_provider`, `providers.<name>`) with env-var key fallback, and are selected via
`--provider`/`--model`. Accordingly:

- Removed: the four Azure commands, `azure.*` settings, SecretStorage key,
  `useOpenAIShim`, and all terminal env injection.
- Added: `clawcodex.provider` / `clawcodex.model` settings (chat spawn flags), the
  `Open Provider Config` command, and config-based provider detection for the Control
  Center (`setting` / `config` / `env` / `unknown` sources). The extension reads the
  config; it never writes it (credential setup belongs to the CLI).
- The workspace "profile" concept maps to `<workspace>/.clawcodex/settings.json`
  (project settings): same Found/Missing/Invalid/Unreadable states, watcher, and open
  action, without openclaude's profile-name allowlist.

## 6. Streaming-state derivation

The agent-server emits only `content_block_delta` stream events — no
`message_start`/`content_block_start`. Streaming state (status-bar spinner, typing
indicator) flips on the first delta or tool activity of a turn; the thinking block shows
on the first `thinking_delta` and hides when text starts or the turn ends.

## 7. stderr policy

The agent-server reserves stdout for JSON frames and logs diagnostics to stderr. Instead
of forwarding every line as an error (reference behavior), only lines matching
`/error|traceback|exception|fatal|critical/i` surface — substring on purpose, so
`ValueError:`-style lines match.

## 8. New Chat cold start

"New chat" keeps the reference's kill+respawn architecture, so each fresh chat pays the
agent-server cold start (provider + tool registry + MCP build) before the first token.
Safe: user messages sent before init queue server-side. The server's `clear` control
could keep the process warm; not adopted, to keep the reference lifecycle.

## 9. Smaller notes

- `permissionMode` gains `auto` (a real clawcodex mode). `dontAsk` exists server-side
  but is deliberately not exposed in the settings enum.
- `system` subtypes beyond `init`/`status` (e.g. goal status carriers) are ignored —
  they are TUI concerns.
- `result.session_id` updates the tracked session id every turn, so a crash-restart
  resumes the file the server was actually writing.
- The reference's `--continue` (continue most recent) has no agent-server equivalent
  and was dropped; Resume Session covers the workflow.
- The reference's `rate_limit` and `tool_progress` webview cases were dropped — the
  agent-server does not emit those message types.
- Tests run under plain `node --test` with a local functional `vscode` stub
  (`test/register-vscode-stub.js`) instead of bun's `mock.module` + openclaude's shared
  env mutex. Requires **Node ≥21** (the test-runner glob); the extension itself runs on
  the VS Code host's Node.
