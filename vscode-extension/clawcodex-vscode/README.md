# Clawcodex VS Code Extension

A practical VS Code companion for clawcodex with a project-aware **Control Center**,
predictable terminal launch behavior, and a **Chat** panel backed by the clawcodex
agent-server — streaming responses, tool activity, thinking indicators, and interactive
permission prompts included.

## Features

- **Chat in the Activity Bar (and as an editor panel)**:
  - spawns `clawcodex agent-server --stdio` per session and speaks its NDJSON protocol
  - streaming text and thinking deltas, tool-use cards with inputs/outputs, per-turn
    usage stats
  - **interactive permission prompts** (Allow / Deny / "Yes, allow …" persistent grants),
    including plan-mode approval with the plan body
  - session history: list, search, and resume saved sessions from `~/.clawcodex/sessions`
  - abort generation via the server's graceful `interrupt` control
- **Real Control Center status**:
  - whether the configured `clawcodex` command is installed
  - the launch command being used and the launch cwd that will be used for terminals
  - the current workspace folder and whether `.clawcodex/settings.json` exists in it
  - a conservative provider summary derived from `~/.clawcodex/config.json` and known
    provider env keys — `unknown` is shown instead of guessing
- **Project-aware launch behavior**:
  - `Launch Clawcodex` launches from the active editor's workspace when possible
  - falls back to the first workspace folder when needed
  - avoids launching from an arbitrary default cwd when a project is open
- **Built-in dark theme**: `Clawcodex Terminal Black`

## Requirements

- VS Code `1.95+`
- `clawcodex` available on your PATH:

  ```bash
  curl -fsSL https://clawcodex.app/install.sh | bash
  ```

## Commands

- `Clawcodex: Open Control Center`
- `Clawcodex: Launch in Terminal` / `Clawcodex: Launch in Workspace Root`
- `Clawcodex: New Chat` / `Clawcodex: Open Chat Panel` (`Ctrl/Cmd+Shift+L`)
- `Clawcodex: Resume Session` / `Clawcodex: Abort Generation`
- `Clawcodex: Open Project Settings` (workspace `.clawcodex/settings.json`)
- `Clawcodex: Open Provider Config` (`~/.clawcodex/config.json`)
- `Clawcodex: Open Repository` / `Clawcodex: Open Setup Guide`

## Settings

- `clawcodex.launchCommand` (default: `clawcodex`) — terminal launch command; its first
  word is also the executable used to spawn the chat backend
- `clawcodex.terminalName` (default: `Clawcodex`)
- `clawcodex.permissionMode` (default: `acceptEdits`) — `default` | `acceptEdits` |
  `plan` | `bypassPermissions` | `auto`, passed to the agent-server
- `clawcodex.provider` / `clawcodex.model` — chat-session overrides; empty uses
  `~/.clawcodex/config.json` defaults

Provider credentials are configured by clawcodex itself (`clawcodex` → `/login`, or edit
`~/.clawcodex/config.json`). The extension reads that config for status display but never
writes it.

## Development

From this folder:

```bash
npm run test    # node --test with a local vscode stub (Node 21+)
npm run lint    # syntax-check every shipped source file
npm run e2e     # live protocol test against a real `clawcodex agent-server` (see script)
```

To package (optional):

```bash
npm run package
```

See `BACKEND_NOTES.md` for how this extension talks to the clawcodex backend — the
agent-server wire contracts, the on-disk session/config formats it reads, and the
design decisions behind them.
