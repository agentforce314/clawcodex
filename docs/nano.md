# Nano mode (`clawcodex --nano`)

A pi-shaped minimal harness profile: **slim, fast, eco**. Under `--nano`,
clawcodex behaves almost like the [pi coding agent](https://pi.dev) —
six tools, a few-hundred-token system prompt, a byte-stable request
prefix, and deterministic output compression. Without the flag, nothing
changes: nano is a default-off process flag consulted at a handful of
chokepoints.

```bash
clawcodex --nano -p "fix the failing test in src/parser.py"   # headless
clawcodex --nano                                              # interactive TUI
clawcodex tui --nano                                          # explicit form
clawcodex web --nano                                          # browser client
clawcodex serve --nano                                        # desktop/web gateway
```

In the interactive TUI, the flag is forwarded to the spawned agent-server
backend, which builds the nano registry and prompt before the first turn.
Permission prompts, Shift+Tab mode cycling, and slash commands work as
usual — nano changes what the *model* sees, not the UI. A mid-session
`/model` or provider switch keeps the nano surface. The status line shows
a `nano` chip beside the model name (e.g. `deepseek-v4-flash nano`),
driven by the backend's `system/init` frame.

The browser client (`clawcodex web --nano`) shows the same fact on three
surfaces — a green `nano` chip beside the composer's model picker (from
the welcome screen on, via `/api/status`), the chip riding the model
segment of the run-stats line, and a `Harness: nano` row in the session
details panel — all driven by the gateway's `session.info.nano`.

## What nano sends

| | nano | default |
|---|---|---|
| Tools | **6** — Read, Bash, Edit, Write, Grep, Glob | 24+ (Agent, TaskV2, Skill, ToolSearch, MCP, …) |
| System prompt | ~300 tokens + your CLAWCODEX.md | ~7,000 tokens (incl. auto-memory doctrine) |
| Tool docs | 1–3 sentences each | full Claude Code-scale docs |
| Fixed payload | **≈ 2,000 tokens** | ≈ 17,000 tokens |
| Per-turn injections | none — byte-stable history | deferred-tools block, reminders, attachments |
| /eco | **on** (never-worse guard) | off |

Measured on terminal-bench 2.1 (`fix-git` + `pypi-server`,
deepseek-v4-flash, identical wheel, k=1): **both tasks solved in both
modes (reward 1.0)** — nano at **$0.0042 total vs $0.0186 (4.4×
cheaper)**, e.g. fix-git: 54.8K vs 282.6K input tokens, $0.00165 vs
$0.01015, 75 s vs 128 s. On the full 89-task suite head-to-head with
the pi harness (both with vision+websearch, deepseek-v4-flash at max
thinking, k=1): **nano 64/89 ($1.31) vs pi 63/89 ($2.01)**; a later full
run of the next round's build (#900, before its last commit) scored 63/89
— read the pair as parity with pi on score. Full
tables: `eval/harbor/RUN_NANO_TB21.md`.

## What nano does differently

- **Skills are listed, not tooled**: the system prompt carries
  name/description/location triples and the Read tool is the loader
  (pi's progressive disclosure). Skills without a file location are
  omitted.
- **Context files**: CLAWCODEX.md as usual; when none exists, nano falls
  back to `AGENTS.override.md` → `AGENTS.md` → `CLAUDE.md` at the
  workspace root (pi's precedence).
- **Edit is pi's ladder**: pass several `{old_string, new_string}` pairs
  in one call via `edits[]`, all matched against the original file;
  near-miss whitespace/Unicode is rescued by a normalized fuzzy match
  that still preserves untouched lines byte-for-byte; CRLF and BOM
  round-trip correctly. The read-first staleness gate stays. The legacy
  single `old_string`/`new_string` form (and `replace_all`) still works.
- **Truncation guard**: tool calls carried by a `max_tokens`-cut
  response are failed with "re-issue with complete arguments" instead of
  executed with possibly-truncated arguments.
- **Compaction keeps the working set**: every auto/manual compaction
  summary ends with cumulative `<read-files>`/`<modified-files>` path
  ledgers, and the summarizer is instructed to update (not discard) a
  previous checkpoint.

## Benchmarking

The Harbor adapter forwards nano with `--ak nano=1`; see
`eval/harbor/RUN_NANO_TB21.md` for terminal-bench 2.1 A/B instructions.

## Current limits

- No MCP servers, plugin/user tools, subagents, or task tools on the
  nano surface — that is the point; use default mode when you need them.
- **No advisor, structurally**: even with `advisor_enabled` in settings
  (host config or a seeded container), a nano session never activates the
  reviewer model — its schema + instructions would break the fixed-payload
  and byte-stability contracts, and a two-model loop is not what a nano
  benchmark measures.
- **One conditional seventh tool**: when a vision model is explicitly
  configured (global config `vision.enabled`, e.g. seeded by the harbor
  adapter's `--ak vision=provider:model`), nano registers
  `vision_analyze` — ask the vision model about a local image. This
  mirrors pi's own terminal-bench extension, which adds the identical
  tool for text-only main models; unconfigured nano stays exactly six.
- `--allowed-tools`/`--disallowed-tools` still filter the six.
- Nano is process-global (the /eco contract): on the TUI's `--stdio`
  transport that is exactly one session; on a multi-session process
  (`agent-server --http`, `clawcodex serve`, `clawcodex web`), `--nano`
  applies to every session it hosts — including sessions created with
  their own provider/model picks. The gateway reports it on
  `/api/status` and stamps every `session.info` with `nano`.

Design rationale and the pi study behind it: the harness comparison in
`my-docs/clawcodex-nano/` (kept out of git) — headline: pi matched or
beat the maximal harnesses on Databricks' real-PR benchmark while
sending ~3× less context per turn.
