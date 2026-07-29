# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Permissions are now loose by default and easy to change.** `/mode` is
  renamed `/permissions` (the old name still works as an alias) and bare
  `/permissions` opens a three-option picker — *Ask for approval*, *Approve for
  me*, *Full Access* — instead of requiring a raw mode name. A bare interactive
  `clawcodex` now starts in **Full Access**, equivalent to
  `--dangerously-skip-permissions`. The chosen level persists to
  `permissions.defaultMode` in `~/.clawcodex/settings.json`, so dialing
  permissions *down* survives a relaunch.

  Scope and safety of the new default:

  - Only a real terminal gets it. `--print`/headless is unchanged (`default`) —
    CI and the eval harness drive it — and so is any non-TTY launch, even
    without `-p`. A persisted `defaultMode` applies to every surface, with one
    exception in the safe direction: a saved *Full Access* is interactive-only
    (see below).
  - Explicit intent always wins: `--permission-mode`,
    `--dangerously-skip-permissions`, and a persisted `defaultMode` all outrank
    the implicit default, and `--allow-dangerously-skip-permissions` (which
    means "available without starting in it") suppresses it.
  - Suppressed entirely under a `disableBypassPermissionsMode` lockdown, and
    under root outside a sandbox — where it degrades to `default` rather than
    refusing to start, and also clamps a persisted `bypassPermissions` and
    settings-granted bypass availability.
  - **Plan mode still restrains.** Choosing Full Access sets the mode only and
    never grants engine bypass *availability*, which is what relaxes `plan`, so
    `/plan` keeps asking in a full-access session.
  - Repository settings files (`.clawcodex/settings.json` and
    `settings.local.json` — both are committable, whatever the `.local` name
    suggests) may only supply `default` or `dontAsk`, and only when that would
    not loosen what applies anyway. A cloned repo cannot widen your
    permissions. `plan` is excluded on purpose: with bypass availability it is
    a full bypass, not a restriction.
  - A saved Full Access applies only to interactive sessions. Persistence
    exists so dialing *down* survives a relaunch; it never dials `-p` up.
  - `permissions.allowBypassPermissionsMode` is now read from the user config
    only. It was also read from `<git-root>/.clawcodex/config.local.json`,
    which — despite the `.local` name — is a committable repo file, so a
    checked-out repository could grant itself bypass *availability* and thereby
    turn `/plan` into a write-anywhere bypass. Operators who kept that key in a
    repo-local config must move it to `~/.clawcodex/config.json`.
  - Running as root outside a sandbox now also drops bypass availability, and
    ignores a *saved* Full Access. An explicit `--permission-mode` is still
    honored there; only the implicit and saved paths are clamped.
  - `setMode` arriving inside a permission-ask reply (`chosen_updates`) is now
    gated by the same capability as `/permissions`, and refused entirely for
    persisted destinations — previously it was an ungated second door to
    `bypassPermissions` for any non-TUI agent-server client.

## [1.3.0] - 2026-07-29

### Added

- Added the `claude-opus-5` model, and wired `--effort` end to end (the
  interactive `/effort` path on Anthropic was also fixed) (#722, #746).
- Added bounded persistent memory with a background self-improvement review
  fork (#731).
- Added a VS Code extension (`vscode-extension/clawcodex-vscode`) driving the
  agent-server over stdio, with interrupt/suggestion/chosen-updates contracts
  (#727).
- Added image-paste input: paste an image at the prompt and it attaches as a
  content block, shown as an `[Image #N]` chip that un-attaches on demand
  (#761, #762).
- Added a Harbor eval harness (`eval/harbor/`) with clawcodex, openclaude, and
  latest-Claude-Code subscription adapters for terminal-bench 2.0/2.1 three-way
  comparisons, ATIF trajectory emission, and per-step token/cost accounting
  (#720, #724, #725, #736–#738).

### Changed

- Renamed the project context file `CLAUDE.md` → `CLAWCODEX.md` (clean break,
  no fallback) (#732).
- Tuned the agent prompt and headless harness for reliability and parity with
  the reference: restored dropped task-tool skip conditions, parallel-tool
  guidance, and dropped instruction qualifiers; deferred nonessential initial
  tools; and recovered trials lost to empty turns and transport drops
  (#743–#745, #747–#754).
- Reworked the TUI header box to the reference's element allocation (#764).
- Honor runtime 1M-context model limits end to end (#730).
- Activated `--allowedTools`/`--disallowedTools`, which were a silent no-op
  (#739).

### Fixed

- Ping-aware stream watchdog to stop spurious `NonZeroAgentExitCodeError` on
  large-context agentic runs (#734).
- Recover trials lost to empty turns, transport drops, and a headless-only
  tool; preserve thinking and reduce headless overhead (#735, #742).
- Enable interactive TaskV2 and harden Bash execution (#741).
- Retry transient transport failures instead of aborting the run, and stop
  `Read` from leaking image base64 into context (#757, #760).
- Cap `mcp` below 2.0 (2.0.0 removed `mcp.client.websocket`) (#763).
- Correct the DeepSeek V4 `max_output_tokens` to the documented 384K (#758).
- Nudge off a stalled background task instead of polling forever (#759).

## [1.2.1] - 2026-07-16

### Changed

- The PyPI distribution is named `clawcodex-cli` because PyPI reserves
  `clawcodex` as confusingly similar to the unrelated `claw-codex` project.
  The installed executable remains `clawcodex`.

## [1.2.0] - 2026-07-16

### Added

- Added the `/eco` token-compression mode for reducing verbose Bash tool
  output while preserving actionable information.
- Added Anthropic Claude Opus 4.8 and Claude Fable 5 model configurations.

### Changed

- Session history and transcripts (`sessions/`, `transcripts/`) now honor
  `$CLAWCODEX_CONFIG_DIR`, consistent with config, memory, skills, and auth.
  Default users are unaffected (still under `~/.clawcodex/`). If you set the
  override *after* accumulating sessions, `/resume` and session search look
  under the new root — move the old `~/.clawcodex/sessions` and
  `~/.clawcodex/transcripts` if you want them to carry over. The
  `# Environment` system-prompt hint now points at the resolved root so the
  model finds session history in either configuration.
- Updated OpenAI and prompt-toolkit dependency minimums to the versions used
  by the current implementation.

### Fixed

- Corrected MiniMax pricing tiers and model modalities.
- Prevented Anthropic OAuth identity migration from rewriting paths under
  `.clawcodex`.
- Restored Claude Code-compatible formatting for thrown tool errors.

## [1.1.0] - 2026-07-12

### Added

- **Sign in with ChatGPT — use OpenAI models on a ChatGPT Plus/Pro
  subscription instead of metered API billing** (#698). `clawcodex login →
  openai → subscription` runs an OAuth login (browser loopback,
  device-code, or import from an existing Codex CLI login) and routes
  requests through the ChatGPT Codex backend's Responses API. Subscription
  models: `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex-spark`, with
  encrypted-reasoning replay across turns; a configured `OPENAI_API_KEY`
  always wins, and subscription usage reports `billing_mode: subscription`
  (billed as `$0`).
- **Claude Pro/Max subscription login** (#697). `clawcodex login →
  anthropic → subscription` connects a Claude subscription over OAuth
  (PKCE), with automatic token refresh, `mcp_`-prefixed tool adaptation,
  and the same `$0` accounting.
- **Meta provider + `muse-spark-1.1`** (#692) — the `api.meta.ai`
  OpenAI-compatible reasoning model with a 1M-token context window, added
  as a one-row `ProviderSpec`.
- **`/plan` mode** with implicit plan-mode entry/exit
  (`EnterPlanMode`/`ExitPlanMode`), ported from the reference (#676).
- **`--worktree` / `-w` session isolation** — run parallel sessions in
  isolated git worktrees, wired through the launcher, backend, and TUI
  (#672).
- **`/loop` scheduled tasks now actually fire — full port of Claude Code's
  session-scoped scheduler** (docs/en/scheduled-tasks). A new
  `src/scheduled_tasks` engine parses standard 5-field vixie cron
  expressions (wildcards, steps, ranges, lists, dow 0/7=Sunday, dom/dow OR
  semantics, local timezone) and fires due prompts between turns from the
  agent-server worker's idle poll. `CronCreate`/`CronList`/`CronDelete`
  register real firing jobs (8-char IDs, 50-job cap, deterministic per-job
  jitter, 7-day recurring expiry with a final fire, one-shots self-delete);
  the new `ScheduleWakeup` tool drives self-paced `/loop` mode (delay
  clamped 60–3600 s, `stop: true` ends the loop, one ~20-minute fallback
  wakeup when an iteration forgets to reschedule). Esc while idle clears a
  pending loop wakeup; `/clear` drops all session tasks; `/resume` restores
  unexpired ones; `CLAWCODEX_DISABLE_CRON=1` disables the scheduler.
- **Typed skill slash commands reach the backend:** the TUI's slash
  dispatch falls back from workflow commands to a new `skill_command`
  control that expands bundled/disk skills through the same path the
  model-side Skill tool uses — so `/loop 5m check ci` (and any
  user-invocable skill) now works typed from the composer, with `/loop`
  listed in the completion menu.
- **TUI scheduled-task indicator:** a persistent `⟳ loop wakeup in 2m 14s ·
  ⏰ 1 scheduled` line above the composer (cronStore + CronIndicator), fed
  by new `cron_status` events that also render fire/stop/restore lines in
  the transcript.
- **Link-opening gesture is now discoverable per terminal.** Apple Terminal
  has no OSC 8 hyperlink support (still true on macOS 26), and the default
  inline mode deliberately leaves the mouse to the terminal — so the only
  way to open an agent-printed link there is Apple Terminal's own URL
  detection: hold ⌘ and double-click the URL. That gesture was undocumented
  and undiscoverable, reading as "links are broken". The TUI now prints a
  one-time dim tip under the first assistant message that contains a URL
  (Apple Terminal only), and `?` quick help / `/help` list the
  terminal-appropriate gesture (`Cmd+click link` in OSC 8 terminals,
  `Cmd+double-click URL` in Apple Terminal, omitted when unknown). VS Code
  (`TERM_PROGRAM=vscode` — also Cursor/Windsurf) is now recognized as an
  OSC 8 terminal: xterm.js has handled `Cmd+click` hyperlinks since 2022,
  and `supportsHyperlinks` is exported from `@clawcodex/ink` for app-level
  use.
- **`/memory` now opens memory files in your `$EDITOR` from the TUI** — the
  full port of openclaude's memory-file picker (`commands/memory/` +
  `MemoryFileSelector`). Typing `/memory` opens a picker overlay listing the
  memory hierarchy (synthetic **User memory** `~/.clawcodex/CLAUDE.md` and
  **Project memory** rows first, then every loaded CLAUDE.md / rules file /
  `@`-import, each with its "Saved in …" / "@-imported" description), served
  by a new `memory_targets` control over the shared `build_memory_options`
  enumeration. Selecting a file ensure-creates it (exclusive-create preserves
  existing content), suspends the TUI to the alternate screen, and spawns
  `$VISUAL`/`$EDITOR` (bare `code`/`subl` get their wait flags, the TS
  `EDITOR_OVERRIDES`); on return the TS-verbatim "Opened memory file at …"
  line lands in the transcript and a `memory_edited` control busts the
  backend's memory-file cache so the very next turn re-reads the edited
  content. Previously `/memory` wasn't wired into the TUI at all — the
  Python `InteractiveCommand` port existed but had no reachable surface.

### Fixed

- **Claude subscription login repaired** (#702). The Anthropic OAuth login
  migrated off `console.anthropic.com` to `platform.claude.com`, and the
  token exchange sent no `User-Agent` — so `urllib`'s default signature was
  Cloudflare bot-blocked (`error code: 1010`) before it reached OAuth.
  Updated the token/authorize/redirect endpoints and scopes to the current
  upstream config (subscriber authorize base `claude.com/cai/oauth/authorize`)
  and send a genuine `User-Agent`.
- **Adaptive thinking is only sent to models that support it** (#699).
  Requests previously sent `thinking={"type":"adaptive"}` to every Claude
  4.x model, which the API rejects for all but Opus 4.6/4.7 and Sonnet 4.6
  ("adaptive thinking is not supported on this model"). Models that support
  thinking but not adaptive now get a token budget instead; `output_config`
  effort is gated to the models that accept it.
- **Semantic tool-input coercion + parity validation errors** (#700) —
  string-coerce boolean/number tool arguments (mirroring the reference's
  `semanticBoolean`/`semanticNumber`) and format schema-validation failures
  to match `formatZodValidationError`.
- Bounded the ESC-cancel worker-thread queue in `OpenAICompatibleProvider.chat_stream_response` (`src/providers/openai_compatible.py`) to `maxsize=64`. Previously an unbounded `queue.Queue` let an orphaned worker accumulate chunks in memory indefinitely when a proxy kept sending bytes after abort without closing the SDK iterator (#278).
- **URLs the agent prints are clickable again.** The TUI markdown renderer
  (`ui-tui/src/components/markdown.tsx`) replaced every visible URL with a
  remote-fetched page `<title>` (silently HTTP-GETting each URL the agent
  printed) or a slug-derived label, leaving the real URL only in OSC 8
  metadata. Terminals without OSC 8 support (e.g. Apple Terminal) strip
  that metadata, so the URL was invisible, unclickable, and uncopyable —
  and in the default inline mode the TUI never captures the mouse, so
  terminal-native detection over the visible text is the only affordance
  that works everywhere. Bare URLs now render verbatim, `[label](url)`
  renders as `label (url)`, the stealth title fetch is gone, and links
  remain OSC 8-wrapped for terminals with first-class hyperlink support
  (Cmd+click in VS Code/iTerm2, plain click in fullscreen mode).

### Changed

- **Directory rebrand: clawcodex state now lives under `~/.clawcodex/` and
  `<project>/.clawcodex/` everywhere.** The subsystems that still read/wrote
  the real Claude Code harness's `~/.claude/` and `./.claude/` (user skills,
  agents, workflows, hooks settings, auto-memory, MCP config + OAuth tokens,
  CLAUDE.md/rules enumeration, output styles, plugins, uploads, project
  `config.json`, bridge worktrees + pointer, `--worktree` sessions, tool
  results, startup-perf, `loop.md`, `debug.log`) were repointed to the
  clawcodex-branded locations. Sharing directories with the Claude Code
  harness meant inheriting and mutating another tool's live state.
- Env overrides renamed: `CLAUDE_CONFIG_DIR` → `CLAWCODEX_CONFIG_DIR`,
  `CLAUDE_MANAGED_CONFIG_DIR` → `CLAWCODEX_MANAGED_CONFIG_DIR`; managed
  defaults unified to `/etc/clawcodex`. The old `CLAUDE_*` variables are
  intentionally ignored (honoring the other harness's override would
  re-couple the two tools' state).
- `--worktree` sessions are created under `.clawcodex/worktrees/`;
  pre-rebrand worktrees under `.claude/worktrees/` are still resumed and
  removable in place (git registers them by absolute path).
- New worktrees no longer receive a copy of the repo's
  `.claude/settings.local.json` (a foreign harness's permission grants);
  only `.clawcodex/settings.local.json` is propagated.

### Added

- One-time startup migration copying legacy `~/.claude` state
  (skills — size-capped per skill, agents, workflows, outputStyles,
  plugins, rules, `CLAUDE.md`, per-project `memory/`) into `~/.clawcodex`.
  Copy-only and destination-absent-only: nothing under `~/.claude` is ever
  modified, and existing `~/.clawcodex` files always win. Marker:
  `~/.clawcodex/.claude-migration.json`.
- `clawcodex migrate [--user-only|--project-only]` — re-attempts the user
  migration and migrates the current project's `.claude/` config dirs into
  `./.clawcodex/` (settings files and worktrees are deliberately skipped).

### Migration notes

- User-scope MCP servers previously stored in `~/.claude/config.json` are
  NOT migrated (that file is shared with the real Claude Code harness and
  clawcodex's entries can't be told apart) — re-add them with
  `clawcodex mcp add --scope user`. MCP OAuth tokens live in the OS
  keychain and are unaffected.
- `settings.json` / `settings.local.json` are never migrated: on a machine
  with both tools they hold the other harness's live permission grants and
  hooks. Copy them manually only if they were written for clawcodex.

## [0.1.0] - 2026-04-19

### Added

#### Core Features
- Multi-provider support for Anthropic, OpenAI, and GLM (Zhipu AI)
- Interactive REPL with prompt-toolkit integration
- Rich interactive terminal output
- Session persistence and management
- Configuration management with basic API key obfuscation

#### CLI Commands
- `clawcodex` - Start the interactive REPL
- `clawcodex login` - Interactive API key configuration
- `clawcodex config` - View current configuration
- `clawcodex --version` - Show version information

#### Provider Implementations
- **Anthropic Provider**: Claude integration with chat + streaming interfaces
- **OpenAI Provider**: GPT integration with chat + streaming interfaces
- **GLM Provider**: GLM integration with chat + streaming interfaces

#### REPL Features
- Command history with persistent storage
- Auto-suggestions from history
- Slash commands: `/help`, `/exit`, `/clear`, `/save`, `/load`, `/multiline`
- Skill slash commands backed by `SKILL.md`
- Syntax highlighting with Rich library
- Tab completion and multi-line input support

#### Configuration System
- JSON-based configuration storage
- Base64-encoded API keys for basic obfuscation
- Provider-specific settings (API key, base URL, default model)
- Session auto-save option

#### Session Management
- Unique session ID generation
- Conversation history tracking
- Session save/load functionality
- Conversation clear operation

#### Code Quality
- Type hints for all public functions
- Abstract base class for provider implementations
- Data classes for structured data (ChatMessage, ChatResponse)
- Error handling and validation

#### Testing
- Unit tests for core components
- Integration tests for providers
- End-to-end tests for REPL functionality
- Test coverage for configuration management

### Technical Details

#### Architecture
- Modular provider system with base abstraction
- Conversation management with message history
- Configuration management layer
- REPL engine with prompt-toolkit

#### Dependencies
- `anthropic>=0.18.0` - Anthropic SDK
- `openai>=1.0.0` - OpenAI SDK
- `zhipuai>=2.0.0` - Zhipu AI SDK
- `prompt-toolkit>=3.0.0` - Interactive REPL
- `rich>=13.0.0` - Terminal formatting
- `python-dotenv>=1.0.0` - Environment variables

#### File Structure
```
src/
├── providers/          # LLM provider implementations
│   ├── base.py        # Abstract base class
│   ├── anthropic_provider.py
│   ├── openai_provider.py
│   └── glm_provider.py
├── repl/              # Interactive REPL
│   └── core.py
├── agent/             # Session management
│   ├── session.py
│   └── conversation.py
├── config.py          # Configuration management
└── cli.py             # CLI commands
```

### Known Limitations

- Context building is still in early MVP form and needs deeper project summarization
- Permission enforcement exists as a framework but is not fully integrated everywhere
- `/resume`, `/compact`, and `/doctor` are not implemented yet
- The current CLI uses turn-based output even though providers expose streaming interfaces

### Migration Notes

This is the initial MVP release. No migration needed.

### Future Roadmap

- [ ] Context enrichment and project-memory improvements
- [ ] Full permission integration
- [ ] `/resume`, `/compact`, `/doctor`
- [ ] Token usage and cost tracking
- [ ] MCP and plugin-system enhancements

---

## Release Notes

### v0.1.0 - MVP Release

This is the first public release of ClawCodex, a complete reimplementation of Claude Code. This MVP includes:

- Full multi-provider support
- Interactive REPL
- Session management
- Configuration system
- Tool system and agent loop foundations
- Type-safe implementation

The focus was on building a solid foundation with clean architecture, comprehensive testing, and good developer experience. All core features are working and tested.

**Special Thanks**: This project is inspired by Claude Code and aims to provide an open-source alternative for learning and experimentation.

---

[Unreleased]: https://github.com/agentforce314/clawcodex/compare/v1.2.1...HEAD
[1.2.1]: https://github.com/agentforce314/clawcodex/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/agentforce314/clawcodex/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/agentforce314/clawcodex/compare/v1.0.0...v1.1.0
[0.1.0]: https://github.com/agentforce314/clawcodex/releases/tag/v0.1.0
