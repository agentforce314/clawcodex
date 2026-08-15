# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **ClawCodex Web — the agent in a browser tab (`ui-web/`, `clawcodex web`).**
  A third front end on the existing backend: three-column shell (session tree
  | conversation | details) with drag-resizable columns and a concession chain
  that protects the reading column, streaming replies with collapsible
  reasoning, live tool cards (terminal transcript, unified diff,
  line-numbered file window, generic output), permission approvals as a
  composer takeover, a prompt queue for follow-ups typed mid-turn, a context
  meter with per-category breakdown, slash-command completion, session resume
  from the sidebar, and light/dark/system themes.

  It carries two views. **Chat** is the conversation. **Trajectory** is the
  same run as a metered ledger: every model request and tool call in order, a
  three-lane timeline (input / model / tools) that switches between
  equal-width operations and real elapsed durations, per-step tokens and
  timing (TTFT, generation, throughput, cache-hit rate), and an inspector with
  Summary / Preview / Raw. Model time and tool time are reported separately —
  "41s" is not actionable, "41s of model, 22s of tools" says which half to go
  look at.

  Making that honest needed per-request data the gateway was discarding:
  `_sdk_envelope` now carries the `usage`/`model`/`stop_reason` it already
  computed, and the gateway translates them into a `step.complete` event
  (`result` only ever reported whole-turn totals, so a ten-request turn and a
  one-request turn were indistinguishable). Both changes are additive — a
  message without those fields produces exactly the envelope it always did.

  It is deliberately **not** a second server. `clawcodex web` is
  `clawcodex serve` with the built `ui-web/dist` mounted on it, so a browser
  drives the same in-process agent, the same JSON-RPC gateway (`/api/ws`), and
  the same durable sessions as the TUI and the desktop app. The backend change
  is additive and gated: with no bundle built, `serve` is byte-for-byte the
  desktop backend it has always been. The whole coupling to the protocol lives
  in four client files (wire types, socket client, tool-name vocabulary, and a
  pure events→nodes reducer), so the UI above them knows nothing about
  JSON-RPC.

  `GET /` inlines the session token (the same `__CLAWCODEX_SESSION_TOKEN__`
  global the desktop shell already scrapes) because a browser has no other way
  to learn it — so `clawcodex web` refuses a non-loopback `--host` unless
  `--allow-remote` is passed. Visual design and several structural ideas are
  adapted from the MIT-licensed DeepSeek Harness web client; see
  `ui-web/README.md`.

- **Web chat rendering at reference-client fidelity (`ui-web/`).** A
  side-by-side pass against the DeepSeek Harness client, closing every gap in
  what the transcript shows: terminal output renders its ANSI colors (SGR incl.
  256/truecolor, `\r` progress-bar overwrite) instead of escape-glyph salad;
  math is tokenized in the markdown grammar so `$$\int x\,dx$$` survives
  marked's own escapes; task lists draw a checkbox without the literal `[x]`;
  links keep http/https/mailto only and images must be absolute http(s) (lazy,
  no referrer); streaming freezes settled blocks and highlights each fence
  once, at seal, instead of re-lexing the whole reply per delta; long
  terminal/diff/read/web cards fold their middle and keep the tail (the exit
  error is the point); resumed sessions keep their diffs (reconstructed from
  the stored edit arguments), match/file counts and read summaries; the todo
  row reports `2/6 done · <active item>`; unknown tools disclose an IN/OUT
  card; web tools link their URLs; a turn-fatal error paints once, not as
  bubble and notice; and the flow re-pins to the bottom when content grows
  after layout (async highlight, image loads, card expansion).

  A second pass, from a real run's screenshots: the task registry's rows
  (TaskCreate/TaskUpdate/TaskList) no longer quote their JSON result
  envelopes — a create says its subject, an update says
  `<subject> → completed` (subject recovered from the create that minted the
  id), a list renders the checklist card; no row summary may be a JSON dump
  (machine payloads stay behind the disclosure, pretty-printed), which covers
  MCP tools too. And live thinking stopped being a one-line ticker whose text
  slid left on every delta: it is now a bottom-anchored window onto the tail
  of the thought — the last few lines, wrapped, arriving at reading pace —
  that collapses to the quiet one-line "Thought" row when the model moves on.

- **Native Windows support for the CLI.** ClawCodex now runs first-class on
  Windows 10/11 — PowerShell, cmd, and Windows Terminal — with no WSL
  required. The port follows the playbook of a reference Windows-supporting
  CLI (shell resolution, tree-kill semantics, tiered CI, LF-pinned
  checkouts) rather than ad-hoc `if windows` patches:
  - A new platform layer (`src/utils/shell_platform.py`) resolves **Git
    Bash** for the Bash tool — env override (`CLAWCODEX_GIT_BASH_PATH` /
    `CLAUDE_CODE_GIT_BASH_PATH`), then derivation from `git.exe`, then
    well-known install dirs — and explicitly refuses the WSL launcher
    shims (`System32\bash.exe`, WindowsApps), which would run commands
    inside a Linux VM against the wrong filesystem. Shell commands keep
    their POSIX semantics on every platform; hooks, autofix, and
    statusline commands route through the same bash.
  - Process **tree** kills work on Windows (`taskkill /T /F`, the
    `tree-kill` approach) — abort/timeout/TaskStop paths no longer rely on
    POSIX `os.killpg`/`SIGKILL`, which crashed with `AttributeError`.
    Background bash no longer passes `start_new_session` (a `ValueError`
    on Windows).
  - Persistent-cwd tracking uses `pwd -W` under Git Bash so `cd` in
    compound commands round-trips real Windows paths; the Bash tool's
    `cwd` argument accepts drive-letter absolute paths; `@`-mentions and
    `CLAWCODEX.md` `@includes` resolve `C:\...` paths.
  - Real file locking on Windows (`msvcrt` region locks) for the server
    lockfile, swarm mailbox, and transcript writer — the Windows CRT's
    `O_APPEND` emulation is not atomic, so concurrent appends previously
    could *lose* lines there.
  - Windows-correct config roots (`%ProgramData%\ClawCodex` for managed
    policy; `%TEMP%` instead of a literal `/tmp` for spill dirs), NTFS
    case-insensitive path containment in the permission layer, and a
    JSON-encoded agent-server command so interpreter paths containing
    spaces (`C:\Program Files\...`) survive the TUI handoff.
- **`install.ps1` — the native Windows one-click installer**
  (PowerShell 5.1+):

  ```powershell
  irm https://clawcodex.app/install.ps1 | iex
  ```

  Mirrors `install.sh` end to end: git prerequisite check (plus a Git
  Bash runtime check), user-local `uv` install, Python 3.10+
  provisioning, clone to `%USERPROFILE%\.clawcodex\clawcodex`,
  lock-pinned `uv sync`, `clawcodex` shims (cmd + Git Bash) in
  `%USERPROFILE%\.local\bin`, registry user-PATH update, Node
  provisioning, and the Ink TUI build — with the same agent-friendly
  lifecycle subcommands (`status` / `doctor` / `verify` / `update` /
  `uninstall`), `-DryRun`, `-LogFile`, ownership-marker-gated uninstall,
  and the grep-friendly `DONE:` exit summary. `install.sh` on a native
  Windows shell now points at it.
- **Windows CI**: the full test suite runs on `windows-latest` alongside
  Ubuntu on every PR (`fail-fast: false`, LF checkouts enforced by a new
  `.gitattributes`). The `linux_only` pytest marker is now actually
  enforced by a collection hook instead of being documentation.
- **ClawCodex Desktop on Windows.** The Electron app builds, runs, and
  packages natively on Windows 10/11: `npm run dist:win:nsis` produces an
  unsigned NSIS installer (`ClawCodex-<version>-win-x64.exe`) with the
  crab icon and version metadata stamped via rcedit and node-pty's conpty
  binaries staged; `clawcodex desktop` launches the dev app (the bare
  `npm` spawn now resolves `npm.cmd`). The packaged app boots the same
  `%USERPROFILE%\.clawcodex\clawcodex` backend the CLI installers create
  — one shared install, config, and session store across CLI, TUI, and
  Desktop. Verified live end to end on Windows 11: NSIS silent install →
  app boot → backend spawn from the venv → healthy loopback gateway.
- Desktop fixes that surfaced during the port: the in-app updater's
  relaunch resolver looked for the app under the upstream `apps/desktop/`
  layout (never matching this repo's `ui-desktop/`, so updates always
  ended in "reinstall the GUI"), and its relaunch handoff — a bash
  script — is now explicitly skipped on Windows in favor of the honest
  manual-restart state; a hashbang in `scripts/stage-native-deps.mjs`
  broke vitest collection of the packaging tests; the `fmt` script's
  single quotes matched nothing under cmd.exe.

### Fixed

- The stdio agent-server pins `\n` framing on Windows (text-mode stdout
  would otherwise emit `\r\n` NDJSON), and the startup profiler no longer
  crashes on import when the environment lacks a resolvable home
  directory.
- Session-runner kill status is classified correctly on Windows
  (`TerminateProcess` exit code 1 after a requested kill reads as
  *interrupted*, matching POSIX `-SIGTERM`), and the stream watchdog's
  force-close actually wakes a reader parked in `recv` (WinSock
  `shutdown` alone never does).

## [1.5.0] - 2026-08-08

### Added

- **ClawCodex Desktop — a native desktop app** (`ui-desktop/`, #802–#805).
  The full agent in a polished Electron window: streaming chat with live
  tool activity and reasoning, permission approvals with
  once/session/always grants, a session sidebar with resume (same durable
  store as the TUI), side-by-side previews, settings, and the official
  pixel-art crab as the app icon and in-app brand mark.

  ```
  clawcodex desktop
  ```

  launches it from a checkout (installs UI deps on first run; `--no-dev`
  builds once and launches Electron directly). Under the hood the app
  spawns **`clawcodex serve`** — a new loopback HTTP + WebSocket gateway
  (`/api/*` REST + JSON-RPC `/api/ws`) that runs sessions on the same
  in-process agent core as the TUI, so both surfaces share one config, one
  session store, and one set of skills. Ported from the reference desktop
  implementation (~310K lines of TypeScript across ~1,500 files) and fully
  rebranded, verified live end-to-end (boot → real chat turn → streamed
  reply rendered), with macOS packaging producing a DMG/zip via
  electron-builder.

### Fixed

- The desktop app's `lib/` source directories are tracked again — the root
  `.gitignore`'s Python-oriented `lib/` pattern had silently excluded 180
  renderer source files from the initial import; fresh clones failed at
  boot (#806).
- Desktop default UI scale is 100% (Chromium actual size) instead of the
  reference's dense 90% preset, which read too small on typical displays;
  per-install zoom choices persist (#807).
- A second `clawcodex desktop` now explains the app is already running
  (dev port probe) instead of dying on a vite stack trace (#808).

## [1.4.0] - 2026-08-02

### Added

- **Fusion models — give a text-only model vision.** Some strong reasoning
  models cannot see images at all: `deepseek-v4-pro` rejects any image content
  block outright (`400 unknown variant \`image_url\`, expected \`text\``), so
  pasting a screenshot, `@`-mentioning an image, or letting `Read` return one
  ended the turn. A *fusion model* pairs that base model with a second,
  vision-capable one: every image is sent to the vision model first, and its
  description is what the base model reads.

  ```
  /fusion create deepseek-v4-pro-V deepseek:deepseek-v4-pro openrouter:google/gemini-2.5-flash
  /model deepseek-v4-pro-V
  ```

  Ported from [claude-code-router]'s Fusion Model concept, keeping its
  `new model = base model + capability` shape and its create/save/manage
  lifecycle. One deliberate difference: CCR is a proxy, so it can only offer
  vision as a tool the model may choose to call — which cannot help a *pasted*
  image, already on the wire before the model gets a turn. clawcodex owns the
  agent loop, so it does what CCR's docs describe directly, substituting images
  in place. Every entry point is covered at once: paste, `@file.png`, `Read` on
  an image, and `Bash` image output.

  - `/fusion` lists, creates, deletes, and enables/disables saved models; they
    are stored in `~/.clawcodex/config.json` under `fusionModels` (user-level
    only, so a checked-out repo cannot redirect where your screenshots go).
  - A saved fusion model behaves like a normal model: it appears in the
    `/model` picker, works as `--model <name>` interactively and in `-p`, and
    **survives a restart** — selecting one persists the fusion *name*, and
    startup resolves it back to the pair. Deleting or disabling a fusion
    model that was the persisted choice falls back to the provider default
    instead of leaving a dangling name on the wire.
  - Each distinct image is described once per session, so replayed history
    does not re-pay for the same screenshot. Vision failures degrade to a note
    naming the cause rather than killing the turn.
  - Requires the vision half to actually support images. Notably `glm-5.2` does
    **not** — Z.ai's vision models are the separate `*v` family (`glm-4.5v`,
    `glm-5v-turbo`), which is what CCR's own `GLM-5.2 + GLM-5V-Turbo` example
    refers to. `deepseek-v4-pro`, `deepseek-v4-flash`, `glm-5.2`, and `glm-5.1`
    are now marked vision-less in the model table.

  Fusion models, the `/fusion` command and the shared persisted-model
  resolution below all land in #771.

  [claude-code-router]: https://ccrdesk.top/en/configuration/fusion-models/

- **GPT-5.6 (Sol / Terra / Luna)** (#773). OpenAI's current frontier generation is
  three durable capability tiers on one generation rather than a size ladder:
  Sol is the flagship, Terra balances capability against cost, Luna is the
  cheap high-volume tier, and `gpt-5.6` is OpenAI's alias for Sol. All four are
  offered by the direct OpenAI provider and by OpenRouter (which also carries
  `-pro` variants of each tier).

  Each carries a real `ModelConfig` (1.05M context / 128K output). Without one
  they resolved through the prefix fallback to the 272K catch-all, which fires
  auto-compact roughly three quarters of a window early. The bare `gpt-5.6`
  alias is deliberately registered *after* `gpt-5.5`: its prefix base is `gpt`,
  so listing it first would make it the catch-all for every unknown `gpt` id
  and hand them a 1.05M window — over-estimating overflows the context, while
  under-estimating only compacts early.

  Not changed: `default_model` stays `gpt-5.4`, matching how Anthropic defaults
  to `claude-sonnet-4-6` while listing `claude-fable-5` first — the default is a
  cost-sensible tier, not the frontier. The ChatGPT-subscription allow-list
  (`SUBSCRIPTION_MODELS`) is also untouched, since which models that backend
  serves is a wire fact that has to be observed rather than assumed.

- **`AskUserQuestion` actually asks.** The tool was advertised but never
  wired: its raw JSON payload was returned to the model as the tool result,
  so the model saw a blob instead of the user seeing a picker. The TUI now
  renders a real multiple-choice dialog and sends the choice back (#774).

- **Four more OpenAI-compatible providers** — `groq`, `cerebras`, `baseten`
  and `xai` — bringing the registry to 30. Each ships a curated model list
  that live `/models` discovery extends rather than replaces (#784).

- **Fusion models are runnable under the Terminal-Bench harness.** A fusion
  model lives in global config and is selected by name, so a fresh eval
  container could not resolve one; `--ak fusion=<base>+<vision>` seeds the
  record and the base provider (#787).

### Fixed

- **Reasoning effort never reached the wire for any OpenAI-compatible
  provider.** `--effort` was emitted only on the Anthropic branch, so every
  DeepSeek/OpenRouter/GLM run silently ignored it — including benchmark runs
  that reported an effort setting in their config and sent nothing (#776).

- **The first-party OpenAI provider chose its wire protocol from the auth
  mode**, not the model: an API key meant Chat Completions, which rejects
  tools outright for some reasoning models (`gpt-5.6-luna` 400s even with no
  effort set). Protocol now follows the model and auth only picks the route,
  which is what makes those models usable on an API key at all (#783).

- **Cached prompt tokens were billed at the full input rate.** `prompt_tokens`
  includes tokens served from the cache, and the cached count was dropped, so
  a heavily-cached turn over-reported its cost several-fold. Both wires now
  split cache reads out. The same change surfaced that OpenRouter's streamed
  reasoning was discarded entirely — it sends `delta.reasoning`, and only
  `reasoning_content` was read (#785).

- **`result.usage` omitted cumulative cache tokens**, so anything pricing it
  billed the cached portion at nothing, and `/goal`'s token budget saw a
  fraction of what had been spent. Turn cost is now read from the cost
  tracker, which prices each response individually — pricing the aggregate
  crosses a per-request tier boundary no single request came near (#786).

- **Headless runs reported success after stopping early.** A cut-short run,
  a loop-guard kill, and a plan-mode trap all surfaced as
  `subtype: "success"`; `/goal` then treated the result as evidence of
  progress and re-ran on cancels and errors (#777, #778, #779, #780).

- **A rejected image ended the turn instead of being recovered.** The
  "too many images" path is now classified and retried, and the reactive
  recovery lane — dead since a typed error stopped matching a string-only
  gate — runs again (#781, #782).

- **TUI:** the header box lost its right border and could lose the border
  entirely on first paint (#769, #770); the scrollbar stretched its sibling
  and blanked the transcript on terminal resize (#775).

- **OpenRouter's curated model list offered ids OpenRouter had delisted** (#773). The
  OpenAI section still led with `openai/gpt-5` / `openai/gpt-4o` / `openai/o1`
  while the gateway had moved on to the `gpt-5.6` family, and
  `openai/o1-mini` had been removed upstream entirely — so the /model picker
  offered a row that fails at request time, the same "offers something you
  cannot select" shape as the picker bug above. Refreshed against
  `https://openrouter.ai/api/v1/models`: the OpenAI block now leads with
  `gpt-5.6-luna/terra/sol` (and their `-pro` tiers), keeps the codex line and
  the o-series, and drops the delisted ids. Batch (`:batch`), image, audio,
  and embedding variants are deliberately excluded — none of them serves an
  interactive agent turn.

  Validating the rest of the list caught five more dead ids:
  `anthropic/claude-3.5-sonnet`, `anthropic/claude-3.5-haiku`,
  `google/gemini-2.0-flash`, `meta-llama/llama-3.1-405b-instruct`,
  `deepseek/deepseek-v3.2-speciale`, and `x-ai/grok-2`. All are replaced with
  live successors; every id in the catalogue now resolves.

  The list had also been duplicated verbatim between
  `PROVIDER_INFO["openrouter"]` (which feeds `login` and the picker) and
  `OpenRouterProvider._curated_models()` (which feeds discovery's curation).
  Two lists for one set drift the moment someone edits one of them, and the
  drift reads as a model one surface offers and the other drops, so the
  provider now reads the registry.

- **`/model` listed one provider instead of every configured one** (#772). Step 1 of
  the picker showed a single row — `anthropic · 22 models` — no matter how many
  providers were set up. `model.options` was a stub: it called the
  `get_settings` control, which describes only the provider the session is
  *currently* on, and synthesized a hardcoded one-element array from it. The
  26-entry registry (`PROVIDER_INFO`, hand-written provider classes plus the
  data-driven OpenAI-compatible specs) was never enumerated, and no backend
  control exposed it, so the row on screen was the active provider echoed back
  at itself. A new `list_model_providers` control returns the real catalog,
  ordered active-first, then providers that can authenticate, then the rest.

  Three paths behind it had never been reachable, and two were broken:

  - Selecting a model from another provider was refused. `set_model` will not
    point the live provider at a foreign model id — a cross-provider switch
    needs the registry rebuild only `set_provider` performs — so its refusal
    now carries a machine-readable `provider_mismatch` and the client re-drives
    the switch through `set_provider` before re-applying the model. If that
    second step fails it rolls back and reports which provider the session
    actually ended on.
  - `model.save_key` was never wired and fell through to the adapter's default
    case, so inline API-key entry always answered "failed to save key". Keys
    now persist where `clawcodex login` writes them.
  - `model.disconnect` was likewise unwired, so `^d` silently did nothing.

  Disconnect deletes only environment-variable names a provider *owns*. The
  candidate list is a resolution preference, not a claim of ownership —
  `nvidia-nim` accepts `DEEPSEEK_API_KEY` — so treating it as a delete-set
  would destroy a credential set for something else, including the provider the
  session is running on. Ownership follows the primary candidate, contested
  names are reported rather than removed, and a key the live session
  authenticates through is never touched. A shell export sitting behind a
  config key is detected before deletion, since removing the config copy makes
  the provider look disconnected while the export restores it on next launch.

  Known limitation: when no provider is configured at all the session fails to
  start, and the `init_error` guard short-circuits every control — including
  this one — so the picker cannot yet be used to paste a first key. It now
  reports that reason instead of inventing a provider row.

- **`--model`/`/model` selection is now resolved from one rule at every
  entrypoint** (#771). The persisted `/model` choice was applied only in the
  interactive agent-server, and only *after* the provider was constructed
  (`_build_runtime`'s post-construction `provider.model = ...`). Headless
  (`-p`) ignored it entirely, so a `/model` switch had to be re-stated with
  `--model` on every subsequent run. Both entrypoints now share
  `settings.get_persisted_model` and TS's precedence — explicit `--model`,
  then the persisted choice, then the provider default
  (`main.tsx:1984`: `userSpecifiedModel ?? getUserSpecifiedModelSetting() ?? null`)
  — resolved *before* construction, which is also what lets a persisted
  fusion model decide which provider to build. The cross-provider staleness
  guard (`settings.model_provider` must match) is unchanged; a fusion model
  is exempt because its record names its own provider.

  `apply_persisted_model` (post-construction, no callers) is replaced by
  `get_persisted_model`.

### Changed

- **Permissions are now loose by default and easy to change** (#768). `/mode` is
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
