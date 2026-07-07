# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[0.1.0]: https://github.com/agentforce314/clawcodex/releases/tag/v0.1.0
