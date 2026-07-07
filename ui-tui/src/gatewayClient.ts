/**
 * GatewayClient — the gateway adapter the TUI app talks to.
 *
 * The app expects a GatewayClient: an EventEmitter speaking a JSON-RPC-style
 * `tui_gateway` protocol. clawcodex instead has an agent-server that speaks its
 * own NDJSON protocol over stdio, so this class keeps the EXACT public interface
 * the app depends on (start/drain/kill/getLogTail/publishLocalEvent/request,
 * emitting 'event' and 'exit') but:
 *   - spawns `clawcodex agent-server --stdio` (the backend),
 *   - maps clawcodex NDJSON messages → `GatewayEvent`s,
 *   - maps the app's RPCs (prompt.submit, session.interrupt, …) → agent-server
 *     stdin (user messages + control_requests).
 *
 * Phase 1 covers the basic flow (prompt → streamed text response). Tools,
 * permissions and the remaining control RPCs are best-effort stubs refined in
 * Phase 2.
 */
import { type ChildProcess, spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { readdirSync } from 'node:fs'
import { resolve as pathResolve } from 'node:path'
import { createInterface } from 'node:readline'

import type { CostSnapshot, GatewayEvent, GoalSnapshot, PatchHunk, StructuredDiffPayload } from './gatewayTypes.js'
import { formatTotalCost, setLastCostSnapshot } from './lib/costSummary.js'
import type { SessionInfo } from './types.js'

const STARTUP_TIMEOUT_MS = 30_000
const MAX_LOG_LINES = 500
const RPC_TIMEOUT_MS = 5_000

/** Worktree exit RPCs get a LONG deadline: `git worktree remove --force` on a
 *  node_modules-scale tree can far exceed the default 5 s — a timeout there
 *  would misreport "cleanup failed" and SIGTERM the backend mid-removal,
 *  leaving a half-deleted directory. The prompt shows an interim
 *  "Removing worktree…" state while this runs. */
const WORKTREE_RPC_TIMEOUT_MS = 600_000
// clawcodex app version shown in the banner ("clawcodex v{version}"). Keep in
// sync with the installer (install.sh INSTALLER_VERSION).
const CLAWCODEX_VERSION = '1.0.0'

/** Command that launches the clawcodex agent-server (set by the Python launcher). */
function resolveAgentCmd(): string[] {
  const raw = process.env.CLAWCODEX_AGENT_SERVER_CMD?.trim()

  return raw ? raw.split(/\s+/) : ['clawcodex', 'agent-server']
}

function safeJson(v: unknown): string {
  if (typeof v === 'string') {return v}

  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

/** A human label for a permission suggestion's rule(s), e.g. `Bash(ls:*)` or
 *  `Bash(grep:*), Bash(tr:*), …` for a compound command's bundled rules (just
 *  the tool name for a content-less rule). Shown on the "don't ask again"
 *  option so the user sees what it will persist. */
export function describeSuggestionRule(suggestion: any): string | null {
  const rules = Array.isArray(suggestion?.rules) ? suggestion.rules : []

  const labels = rules
    .filter((r: any) => r && r.tool_name)
    .map((r: any) => (r.rule_content ? `${r.tool_name}(${r.rule_content})` : String(r.tool_name)))

  if (labels.length === 0) {return null}

  return labels.length > 3 ? `${labels.slice(0, 3).join(', ')}, …` : labels.join(', ')
}

// The human-reviewable action for a permission prompt: the actual Bash command,
// file path, or URL under review — NOT the full tool-input JSON blob (which the
// box previously dumped verbatim and made the prompt unreadable).
export function approvalCommandText(input: unknown): string {
  if (input && typeof input === 'object') {
    const o = input as Record<string, unknown>

    // pattern before path so a Grep/Glob box shows the search pattern (matching
    // the tool trail label), not the directory it searched.
    for (const key of ['command', 'file_path', 'url', 'pattern', 'path']) {
      if (typeof o[key] === 'string' && o[key]) {return o[key] as string}
    }
  }

  return safeJson(input)
}

/** Pick the salient arg for a tool so the trail label reads `Bash(ls)` /
 *  `Read(package.json)` / `Grep(TODO)` (Claude-style) instead of a bare tool
 *  name. File paths are shown relative to the workspace so the label stays
 *  short; search tools show their pattern rather than the search directory. */
function toolContext(input: any): string {
  if (!input || typeof input !== 'object') {return ''}

  if (input.pattern != null) {return String(input.pattern)}
  const p = input.file_path ?? input.path ?? input.notebook_path

  if (p != null) {return relativizePath(String(p))}
  const v = input.command ?? input.url ?? input.query ?? input.description ?? input.prompt

  return v == null ? '' : String(v)
}

/** Shorten an absolute path to a workspace-relative path (or basename). */
function relativizePath(p: string): string {
  const ws = (process.env.CLAWCODEX_WORKSPACE || process.env.CLAWCODEX_CWD || process.cwd()).replace(/\/+$/, '')

  if (ws && p.startsWith(ws + '/')) {return p.slice(ws.length + 1)}
  const parts = p.split('/')

  return parts[parts.length - 1] || p
}

/** Summarize a tool result for the trail. A successful Read returns
 *  line-numbered file contents (cat -n: `N\t…`), which read as noise when
 *  crammed onto one line, so collapse it to a line count (Claude-style). Only
 *  genuine numbered output is collapsed — errors (is_error) and Read's other
 *  acknowledgements (empty-file / file_unchanged warnings, PDF/image stubs)
 *  aren't `N\t…` text and pass through, so nothing is mislabeled or hidden. */
// Memory bound for retained raw results — render caps (VERBOSE_TRAIL_MAX_*)
// apply separately at display time.
const RESULT_RAW_MAX_CHARS = 48_000

// Full result retained only when the compact summary lost information.
function rawToolResult(formatted: string, full: string): string | undefined {
  const raw = (full ?? '').trim()

  if (!raw || raw === formatted.trim()) {
    return undefined
  }

  return raw.length > RESULT_RAW_MAX_CHARS ? raw.slice(0, RESULT_RAW_MAX_CHARS) + '\n…' : raw
}

// TodoWrite's input IS the todo list — surface it on tool events so the task
// HUD renders (the original never shows todo tool calls inline; the checklist
// under the busy line is the whole UI).
function todosFromInput(name: string | undefined, input: unknown): undefined | unknown[] {
  if (name !== 'TodoWrite' || !input || typeof input !== 'object') {
    return undefined
  }

  const todos = (input as { todos?: unknown }).todos

  return Array.isArray(todos) ? todos : undefined
}

// Per-tool result summaries, matching the original Claude Code transcript
// (tools/*/UI.tsx): Read → "Read N lines", Grep/Glob → "Found N …", Bash →
// first 3 stdout lines + overflow hint, errors → red "Error: …" capped at 10
// lines. Hints reference ctrl+o — the real expand binding (toggles
// /details expanded); the raw output rides tool.complete as result_raw so
// the expanded view can actually show it.
const ERROR_RESULT_MAX_LINES = 10
const BASH_RESULT_MAX_LINES = 3

/** WebSearch display data forwarded by the agent-server (`tool_use_result`
 *  trimmed to the two numbers the original's one-liner needs). */
export type WebSearchDisplay = { durationSeconds?: number; searchCount?: number }

// One marker line per performed search in the model-facing blob
// (web_search.py _map_result_to_api emits `Links: […]` / `No links found.`
// per structured result block). Fallback only — the envelope is authoritative.
const WEB_SEARCH_BLOCK_RE = /^(?:Links: \[|No links found\.$)/gm

// Exact port of WebSearchTool/UI.tsx renderToolResultMessage: "Did N
// search(es) in Xs" (whole seconds at >=1s, else ms). Without the envelope
// (older backend) the duration is unknown and omitted.
function webSearchSummary(result: string, webSearch?: WebSearchDisplay): string {
  const searchCount = webSearch?.searchCount ?? (result.match(WEB_SEARCH_BLOCK_RE)?.length ?? 0)
  const line = `Did ${searchCount} search${searchCount !== 1 ? 'es' : ''}`
  const s = webSearch?.durationSeconds

  return s === undefined ? line : `${line} in ${s >= 1 ? `${Math.round(s)}s` : `${Math.round(s * 1000)}ms`}`
}

export function formatToolResult(
  name: string | undefined,
  result: string,
  isError = false,
  webSearch?: WebSearchDisplay
): string {
  if (isError) {
    let msg = (result ?? '').trim() || 'Tool execution failed'

    if (!/^(Error|Cancelled): /.test(msg)) {
      msg = `Error: ${msg}`
    }

    const lines = msg.split('\n')

    if (lines.length > ERROR_RESULT_MAX_LINES) {
      return [
        ...lines.slice(0, ERROR_RESULT_MAX_LINES),
        `… +${lines.length - ERROR_RESULT_MAX_LINES} lines (ctrl+o to see all)`
      ].join('\n')
    }

    return msg
  }

  if (!result) {return result}

  // The original renders the whole result as ONE line (never the blob —
  // that's tens of wrapped rows of snippets); the full text stays reachable
  // behind ctrl+o via result_raw. Shape-keyed on the envelope too so a
  // mid-turn attach without tool_use bookkeeping still summarizes.
  if (name === 'WebSearch' || webSearch) {
    return webSearchSummary(result, webSearch)
  }

  if (name === 'Read' && /^\s*\d+[\t→ ]/.test(result)) {
    const n = result.split('\n').filter(l => l.length > 0).length

    return `Read ${n} line${n === 1 ? '' : 's'}`
  }

  if (name === 'Grep' || name === 'Glob') {
    if (/^No (files|matches|content)/i.test(result.trim())) {
      return `Found 0 ${name === 'Glob' ? 'files' : 'lines'}`
    }

    const n = result.split('\n').filter(l => l.length > 0).length
    const noun = name === 'Glob' ? (n === 1 ? 'file' : 'files') : n === 1 ? 'line' : 'lines'

    return `Found ${n} ${noun}${n > 0 ? ' (ctrl+o to expand)' : ''}`
  }

  if (name === 'Bash') {
    const trimmed = result.replace(/\s+$/, '')

    if (!trimmed) {
      return '(No output)'
    }

    const lines = trimmed.split('\n')

    // CC parity: when exactly one line overflows, show it instead of a hint.
    if (lines.length <= BASH_RESULT_MAX_LINES + 1) {
      return trimmed
    }

    return [
      ...lines.slice(0, BASH_RESULT_MAX_LINES),
      `… +${lines.length - BASH_RESULT_MAX_LINES} lines (ctrl+o to expand)`
    ].join('\n')
  }

  return result
}

/** clawcodex-backed slash commands (handled via command.dispatch → dispatchSlash).
 *  Drives both the catalog (recognition) and the complete.slash menu.
 *
 *  `hint` is the argument grammar shown dim in the menu and as ghost text
 *  after `/name ` (original CC's Command.argumentHint). Names shadowed by a
 *  TUI-local command (/model, /compact, /bg, /resume, /clear, /exit — the
 *  local registry dispatches first) deliberately carry NO hint here: the
 *  local command's argumentHint is the one that matches actual behavior. */
const SLASHES: ReadonlyArray<{ desc: string; hint?: string; name: string }> = [
  { desc: 'Show available commands', name: '/help' },
  { desc: 'Clear the conversation', name: '/clear' },
  { desc: 'Switch the model', name: '/model' },
  { desc: 'Set the output style', hint: '[<name>]', name: '/output-style' },
  { desc: 'Change the startup logo color scheme', name: '/logo' },
  { desc: 'Set the permission mode', hint: '[default|plan|acceptEdits|dontAsk|bypassPermissions]', name: '/mode' },
  { desc: 'Compact the conversation to save context', name: '/compact' },
  { desc: 'Show context-window usage', name: '/context' },
  { desc: 'Show the total cost and duration of the current session', name: '/cost' },
  { desc: 'Undo recent turns', hint: '[<turns>]', name: '/rewind' },
  { desc: 'Toggle extended thinking', hint: '[on|off|toggle]', name: '/thinking' },
  {
    desc: 'Set reasoning effort (or "ultracode" workflow mode)',
    hint: '[minimal|low|medium|high|auto|ultracode]',
    name: '/effort'
  },
  { desc: 'Switch the provider', hint: '[<provider>]', name: '/provider' },
  {
    desc: 'Configure the advisor reviewer model (consulted mid-task by the worker)',
    hint: '[<provider>:<model> [--client] | --no-client | off|unset]',
    name: '/advisor'
  },
  { desc: 'List running and recent dynamic workflows', name: '/workflows' },
  { desc: 'Search / manage the knowledge base', hint: '[status|list|clear|enable|disable]', name: '/knowledge' },
  { desc: 'Browse and inspect available skills', hint: '[list | inspect <name> | search <query>]', name: '/skills' },
  { desc: 'View or set the plan', hint: '[<text>]', name: '/plan' },
  {
    desc: 'Set a completion condition Claude keeps working toward',
    hint: '[<condition> | status | clear | pause | resume]',
    name: '/goal'
  },
  { desc: 'Add or manage extra criteria on the active goal', hint: '[<text> | remove <n> | clear]', name: '/subgoal' },
  { desc: 'Generate session insights', name: '/insights' },
  { desc: 'List or start background agents', name: '/bg' },
  { desc: 'Resume a past session', name: '/resume' },
  { desc: 'Rename this session', hint: '<name>', name: '/rename' },
  { desc: 'Exit clawcodex', name: '/exit' }
]

type Pending = { reject: (e: Error) => void; resolve: (v: unknown) => void }

/** A workflow slash command reported by the backend (`list_workflow_commands`):
 *  bundled /deep-research plus saved `.claude/workflows/*.py`. */
type WorkflowCommand = { argument_hint?: string; description?: string; name: string }

/** A skill reported by the backend (`list_skills` control). */
type BackendSkill = { category?: string; description?: string; name: string; path?: string }

/** How long a fetched workflow-command list stays fresh. The slash menu
 *  re-queries per keystroke; the TTL keeps that to ~1 RPC per burst while a
 *  workflow authored mid-session (ultracode flow) still shows up promptly. */
const WORKFLOW_CMDS_TTL_MS = 3_000

/** How long a fetched skill list stays fresh. The skills hub inspects per
 *  selection, so a burst of skills.manage RPCs rides one backend disk scan. */
const SKILLS_TTL_MS = 3_000

export class GatewayClient extends EventEmitter {
  private buffered: GatewayEvent[] = []
  private logs: string[] = []
  // The tool-permission request currently awaiting the user's choice.
  private pendingApproval: { input: unknown; request_id: string; suggestions: any[] } | null = null
  // ch13 round-4 — subagent ids already announced via subagent.start, so a
  // second agent_progress emits subagent.progress (not a duplicate start).
  private seenSubagents = new Set<string>()
  // Tool inputs by tool_use id, so tool_result can render an Edit/Write diff.
  private toolInputs = new Map<string, { input: any; name: string }>()
  private msgStarted = false
  private pending = new Map<string, Pending>()
  private pendingExit: null | number | undefined
  private proc: ChildProcess | null = null
  private readyPromise: Promise<void>
  private readyResolve: (() => void) | null = null
  private readyTimer: null | ReturnType<typeof setTimeout> = null
  private reqId = 0
  private sessionId = ''
  private sessionInfo: null | SessionInfo = null
  private subscribed = false
  // Backend skills (skills hub + /skills subcommands), TTL-cached.
  private skills: BackendSkill[] = []
  private skillsFetchedAt = 0
  private skillsTotal = 0
  // Backend workflow commands (slash menu + dispatch), TTL-cached.
  private wfCommands: WorkflowCommand[] = []
  private wfFetchedAt = 0

  constructor() {
    super()
    // The app attaches many 'event' listeners (one per hook); lift the cap.
    this.setMaxListeners(0)
    // Resolves once the backend's system/init has set the session id, so
    // session.create (awaited by the app before it enables the composer) can
    // return a real session_id even if it races the init message.
    this.readyPromise = new Promise<void>(resolve => {
      this.readyResolve = resolve
    })
  }

  // ── lifecycle ────────────────────────────────────────────────────────────
  start(): void {
    const cmd = resolveAgentCmd()
    const cwd = process.env.CLAWCODEX_WORKSPACE || process.env.CLAWCODEX_CWD || process.cwd()
    const env = { ...process.env, PYTHONUNBUFFERED: '1' }

    this.readyTimer = setTimeout(() => {
      this.publish({
        payload: { cwd, python: cmd.join(' '), stderr_tail: this.getLogTail(20) },
        type: 'gateway.start_timeout'
      })
    }, STARTUP_TIMEOUT_MS)

    try {
      this.proc = spawn(cmd[0]!, [...cmd.slice(1), '--stdio', '--workspace', cwd], {
        cwd,
        env,
        stdio: ['pipe', 'pipe', 'pipe']
      })
    } catch (err) {
      this.pushLog(`[spawn error] ${String(err)}`)
      this.handleExit(null, String(err))

      return
    }

    const rl = createInterface({ input: this.proc.stdout! })
    rl.on('line', raw => {
      const line = raw.trim()

      if (!line) {return}

      try {
        this.dispatch(JSON.parse(line))
      } catch {
        this.pushLog(`[protocol] malformed stdout: ${line.slice(0, 200)}`)
      }
    })

    const erl = createInterface({ input: this.proc.stderr! })
    erl.on('line', line => {
      this.pushLog(line)
      this.publish({ payload: { line }, type: 'gateway.stderr' })
    })

    this.proc.on('error', err => {
      this.pushLog(`[proc error] ${String(err)}`)
      this.handleExit(null, String(err))
    })
    this.proc.on('exit', code => this.handleExit(code))
  }

  drain(): void {
    this.subscribed = true

    for (const ev of this.buffered) {this.emit('event', ev)}
    this.buffered = []

    if (this.pendingExit !== undefined) {this.emit('exit', this.pendingExit)}
  }

  getLogTail(limit = 20): string {
    return this.logs.slice(-limit).join('\n')
  }

  kill(_reason = 'requested'): void {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }

    try {
      this.proc?.kill('SIGTERM')
    } catch {
      // best effort
    }

    this.proc = null
  }

  publishLocalEvent(ev: GatewayEvent): void {
    this.publish(ev)
  }

  // ── client → server RPCs ─────────────────────────────────────────────────
  request<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T> {
    const p = (params ?? {}) as Record<string, unknown>

    switch (method) {
      // ── startup handshake ────────────────────────────────────────────────
      case 'commands.catalog': {
        // Await the backend so the catalog can include its workflow commands
        // (/deep-research + saved .claude/workflows) alongside the static set.
        return this.readyPromise
          .then(() => this.fetchWorkflowCommands())
          .catch(() => [] as WorkflowCommand[])
          .then(wf => {
            const pairs = SLASHES.map(s => [s.name, s.desc] as [string, string])
            const canon: Record<string, string> = {}
            const hints: Record<string, string> = {}

            for (const s of SLASHES) {
              canon[s.name] = s.name

              if (s.hint) {hints[s.name] = s.hint}
            }

            for (const w of wf) {
              const name = `/${w.name}`

              if (canon[name]) {continue}
              canon[name] = name
              pairs.push([name, w.description ?? 'Run a dynamic workflow'])

              if (w.argument_hint) {hints[name] = w.argument_hint}
            }

            // skill_count is served lazily from the skills cache (warmed by any
            // /skills use) so the startup catalog doesn't pay a full disk scan.
            return { canon, categories: [], hints, pairs, skill_count: this.skillsTotal, sub: {} } as T
          })
      }

      case 'complete.slash': {
        const text = String(p.text ?? '').toLowerCase() || '/'

        return this.fetchWorkflowCommands()
          .catch(() => [] as WorkflowCommand[])
          .then(wf => {
            const entries = [
              ...SLASHES,
              ...wf
                .filter(w => !SLASHES.some(s => s.name === `/${w.name}`))
                .map(w => ({
                  desc: w.description ?? 'Run a dynamic workflow',
                  hint: w.argument_hint,
                  name: `/${w.name}`
                }))
            ]

            const items = entries
              .filter(s => s.name.toLowerCase().startsWith(text))
              .map(s => ({ display: s.name, hint: s.hint, meta: s.desc, text: s.name }))

            return { items, replace_from: 1 } as T
          })
      }

      case 'complete.path':
        // @-file mentions: serve workspace file completions from disk (the hook
        // computes the replace offset; we just return matching entries).
        return Promise.resolve({ items: this.completePath(String(p.word ?? '')) } as T)
      case 'config.get': {
        // Settings slashes read config; only 'full' maps to clawcodex settings.
        if (String(p.key ?? '') === 'full') {
          return this.controlQuery('get_settings', {}).then(s => (s ?? {}) as T)
        }

        return Promise.resolve({} as T)
      }

      case 'config.set': {
        // Route clawcodex-backed settings to control_requests; display-only prefs
        // (mouse/details/statusbar/skin/…) have no backend and apply locally, so
        // accept them silently.
        const key = String(p.key ?? '')
        const value = p.value

        if (key === 'permission_mode') {
          // The server can reject this (bypassPermissions is gated on
          // availability); reflect its verdict so a settings-panel write
          // doesn't falsely report success while the server refused.
          return this.controlQuery('set_permission_mode', { mode: value })
            .then(r => ({ ok: (r as any)?.ok !== false } as T))
        }

        if (key === 'model') {return this.setModel(String(value ?? '')) as Promise<T>}

        if (key === 'logoColor') {
          return this.controlQuery('set_logo_color', { name: value }).then(r => {
            const ok = (r as any)?.ok === true

            return (ok ? { ok: true, value: String(value ?? '') } : { ok: false }) as T
          })
        }

        if (key === 'effort' || key === 'reasoning') {this.sendControl('set_effort', { effort: value })}
        else if (key === 'provider') {this.sendControl('set_provider', { provider: value })}
        else if (key === 'thinking') {this.sendControl('set_thinking', { action: value })}

        return Promise.resolve({ ok: true } as T)
      }

      case 'permission.cycle':
        // ch13 round-4 — shift+tab: the SERVER computes the guarded next
        // mode (get_next_permission_mode; bypass only when available) from
        // the live mode, so the client can't step into bypassPermissions
        // unconditionally or desync a cursor after /mode.
        return this.controlQuery('cycle_permission_mode', {}).then(r => (r ?? {}) as T)

      case 'session.activate':

      case 'session.create':

      case 'session.resume':
        // clawcodex runs a single agent-server session; hand back its id once
        // system/init has set it. The app then enables the composer.
        return this.readyPromise.then(() => ({ info: this.sessionInfo ?? undefined, session_id: this.sessionId }) as T)

      case 'session.clear':
        // /clear's server half: reset the backend conversation (and its turn
        // odometer) so a "cleared" transcript isn't silently re-fed the old
        // context next prompt. The reply's stats rider refreshes the line.
        return this.controlQuery('clear', {}).then(r => {
          this.publishSessionStats(r)

          return { ok: (r as any)?.ok !== false } as T
        })

      case 'setup.status':
        return Promise.resolve({ provider_configured: true } as T)

      // ── runtime ──────────────────────────────────────────────────────────
      case 'model.options':
        return this.controlQuery('get_settings', {}).then((r: any) => {
          const models: string[] = Array.isArray(r?.available_models) ? r.available_models : []
          const provider = String(r?.provider ?? 'clawcodex')

          return {
            model: r?.model,
            provider,
            providers: [
              {
                authenticated: true,
                is_current: true,
                models,
                name: provider,
                slug: provider,
                total_models: models.length
              }
            ]
          } as T
        })
      case 'prompt.submit': {
        const text = String(p.text ?? '')
        this.msgStarted = false
        this.send({ message: { content: text, role: 'user' }, type: 'user' })

        return Promise.resolve({ ok: true } as T)
      }

      case 'session.active_list':

      case 'session.list':
        // Single agent-server session in the basic port; the switcher/resume
        // list is Phase 2. Resolve locally so the 1.5s poll doesn't spam the
        // backend with list_sessions.
        return Promise.resolve({ sessions: [] } as T)

      case 'session.interrupt':
        this.sendControl('interrupt', {})

        return Promise.resolve({ ok: true } as T)

      // ── --worktree exit flow (long deadline: removal can take minutes) ───
      case 'worktree.exit':
        return this.controlQuery(
          'worktree_exit',
          { action: String(p.action ?? '') },
          WORKTREE_RPC_TIMEOUT_MS
        ).then(r => (r ?? { error: 'no response from backend', ok: false }) as T)

      case 'worktree.status':
        return this.controlQuery('worktree_status', {}, WORKTREE_RPC_TIMEOUT_MS).then(
          r => (r ?? { error: 'no response from backend', ok: false }) as T
        )
      // ── skills hub + /skills subcommands ─────────────────────────────────
      case 'skills.manage': {
        const action = String(p.action ?? 'list')
        const query = String(p.query ?? '').trim().toLowerCase()

        // Community install/browse are Nous-portal features with no clawcodex
        // backend; reject so the hub/command surfaces a real error, not a fake
        // success.
        if (action === 'install' || action === 'browse') {
          return Promise.reject(
            new Error(`/skills ${action}: not supported in clawcodex — add skills under ~/.claude/skills or .claude/skills`)
          )
        }

        return this.fetchSkills().then(skills => {
          if (action === 'inspect') {
            const found = skills.find(s => s.name.toLowerCase() === query)

            return (
              found
                ? { info: { category: found.category, description: found.description, name: found.name, path: found.path } }
                : {}
            ) as T
          }

          if (action === 'search') {
            const results = skills
              .filter(s => s.name.toLowerCase().includes(query) || (s.description ?? '').toLowerCase().includes(query))
              .slice(0, 30)
              .map(s => ({ description: s.description, name: s.name }))

            return { results } as T
          }

          // 'list' (default): group by category for the hub / /skills panel.
          const byCat: Record<string, string[]> = {}

          for (const s of skills) {
            ;(byCat[s.category || 'other'] ??= []).push(s.name)
          }

          for (const names of Object.values(byCat)) {
            names.sort()
          }

          return { skills: byCat, total: this.skillsTotal } as T
        })
      }

      case 'skills.reload':
        // get_all_skills re-scans disk on every call; "reload" just busts the
        // client TTL cache and fetches fresh.
        this.skillsFetchedAt = 0

        return this.fetchSkills().then(
          skills => ({ output: `Re-scanned skills: ${this.skillsTotal || skills.length} available.` }) as T
        )

      // ── slash commands → clawcodex control_requests ──────────────────────
      case 'command.dispatch':
        return this.dispatchSlash(String(p.name ?? ''), p.arg == null ? undefined : String(p.arg)) as Promise<T>
      case 'slash.exec': {
        const raw = String(p.command ?? '').trim()
        const sp = raw.indexOf(' ')
        const name = sp === -1 ? raw : raw.slice(0, sp)
        const arg = sp === -1 ? undefined : raw.slice(sp + 1)

        return this.dispatchSlash(name, arg) as Promise<T>
      }

      // ── tool permission / elicitation responses ──────────────────────────
      case 'approval.respond': {
        const ap = this.pendingApproval
        this.pendingApproval = null

        if (ap) {
          const deny = p.choice === 'deny'
          // 'always' = "don't ask again": send the backend's suggestion AS-IS so
          // its intended SCOPE is preserved — Bash's is a localSettings rule
          // (survives sessions); a file-edit's is a session-scoped acceptEdits
          // setMode; a read's is a content-less allow. The ONLY mutation is the
          // user's optional edit of a Bash rule (git status:* → git:*), which
          // rewrites just that rule's content and nothing else. 'once' → no rule.
          let chosenUpdates: any[] = []
          const first = ap.suggestions?.[0]

          if (!deny && first && p.choice === 'always') {
            const edited = typeof p.rule === 'string' ? p.rule.trim() : ''
            const baseRule = Array.isArray(first.rules) ? first.rules[0] : undefined
            chosenUpdates = [
              edited && baseRule?.rule_content
                ? { ...first, rules: [{ ...baseRule, rule_content: edited }, ...first.rules.slice(1)] }
                : first
            ]
          }

          this.send({
            response: {
              request_id: ap.request_id,
              response: deny
                ? { behavior: 'deny', message: 'Denied by user' }
                : { behavior: 'allow', updatedInput: ap.input, chosen_updates: chosenUpdates }
            },
            type: 'control_response'
          })
        }

        return Promise.resolve({ ok: true } as T)
      }

      case 'clarify.respond':
        this.send({
          response: {
            request_id: String(p.request_id ?? ''),
            response: { action: 'accept', content: { answer: p.answer } }
          },
          type: 'control_response'
        })

        return Promise.resolve({ ok: true } as T)

      default:
        // Unhandled RPC (Phase 2): resolve empty so the app degrades gracefully.
        return Promise.resolve({} as T)
    }
  }

  // Fetch the backend's workflow slash commands, TTL-cached (see
  // WORKFLOW_CMDS_TTL_MS). Degrades to the last-known list on RPC failure.
  private fetchWorkflowCommands(): Promise<WorkflowCommand[]> {
    const now = Date.now()

    if (now - this.wfFetchedAt < WORKFLOW_CMDS_TTL_MS) {return Promise.resolve(this.wfCommands)}
    this.wfFetchedAt = now

    return this.controlQuery('list_workflow_commands', {}).then((r: any) => {
      if (Array.isArray(r?.commands)) {
        this.wfCommands = r.commands.filter((c: any) => typeof c?.name === 'string' && c.name)
      }

      return this.wfCommands
    })
  }

  // Fetch the backend's unified skill set (`list_skills` control), TTL-cached
  // (see SKILLS_TTL_MS). Degrades to the last-known list on RPC failure.
  private fetchSkills(): Promise<BackendSkill[]> {
    const now = Date.now()

    if (now - this.skillsFetchedAt < SKILLS_TTL_MS) {return Promise.resolve(this.skills)}
    this.skillsFetchedAt = now

    return this.controlQuery('list_skills', {}).then((r: any) => {
      if (Array.isArray(r?.skills)) {
        this.skills = r.skills.filter((s: any) => typeof s?.name === 'string' && s.name)
        this.skillsTotal = Number(r.total) || this.skills.length
      }

      return this.skills
    })
  }

  // config.set{model} carries the hermes /model grammar —
  // "<model> [--provider <slug>] [--global|--tui-session]" — verbatim from the
  // picker/slash layer; parsing it is the gateway's job. The callers require a
  // ConfigSetResponse `value` on success (its absence is what "error: invalid
  // response: model switch" reports), so this must round-trip the control
  // rather than fire-and-forget. Scope flags are dropped: the backend persists
  // every switch (agent_server set_model → app-state on_change).
  private setModel(raw: string): Promise<{ value: string; warning?: string }> {
    const tokens = raw.trim().split(/\s+/).filter(Boolean)
    const modelParts: string[] = []
    let provider: string | undefined

    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i]!

      if (tok === '--provider') {
        provider = tokens[++i]

        continue
      }

      if (tok === '--global' || tok === '--tui-session') {
        continue
      }

      modelParts.push(tok)
    }

    const model = modelParts.join(' ')

    return this.controlQuery('set_model', { model, ...(provider ? { provider } : {}) }).then((r: any) => {
      if (r == null) {
        throw new Error('model switch: no response from backend')
      }

      if (r.ok === false) {
        throw new Error(typeof r.error === 'string' && r.error ? r.error : 'model switch failed')
      }

      // Older backends ack {ok:true} without echoing the model.
      return {
        value: typeof r.model === 'string' && r.model ? r.model : model,
        ...(typeof r.warning === 'string' && r.warning ? { warning: r.warning } : {})
      }
    })
  }

  // ── event plumbing ───────────────────────────────────────────────────────
  private controlQuery(
    subtype: string,
    params: Record<string, unknown>,
    timeoutMs: number = RPC_TIMEOUT_MS
  ): Promise<unknown> {
    const requestId = `q${++this.reqId}`

    return new Promise(resolve => {
      this.pending.set(requestId, { reject: () => resolve(null), resolve })
      this.send({ request: { subtype, ...params }, request_id: requestId, type: 'control_request' })
      setTimeout(() => {
        if (this.pending.has(requestId)) {
          this.pending.delete(requestId)
          resolve(null)
        }
      }, timeoutMs)
    })
  }

  // Map a slash command (name + optional arg) to a clawcodex control_request
  // and return a CommandDispatchResponse — `type:'exec'` + human-readable output
  // the app prints, or `type:'send'` for workflow commands whose expanded
  // directive the app submits as a prompt. Unknown commands are offered to the
  // backend as workflow commands (/deep-research, saved .claude/workflows)
  // before reporting they aren't wired.
  private async dispatchSlash(
    name: string,
    arg?: string
  ): Promise<{ output: string; type: string } | { message: string; notice?: string; type: 'send' }> {
    const out = (output: string) => ({ output, type: 'exec' })

    switch (name) {
      case 'advisor': {
        const r = (await this.controlQuery('advisor', { arg: arg ?? '' })) as any

        if (!r || Object.keys(r).length === 0) {return out('advisor: backend not ready')}

        return out(String(r.text ?? r.error ?? 'advisor: no response'))
      }

      case 'clear': {
        const r = (await this.controlQuery('clear', {})) as any

        // The clear control is idle-only — a rejected /clear (active turn)
        // must NOT hide the goal indicator or claim success (critic R1).
        if (!r || r.ok === false) {
          return out(`clear: ${r?.error ?? 'backend not ready'}`)
        }

        this.publishSessionStats(r)

        // Backend /clear also removes any active goal (CC docs/en/goal
        // §Clear a goal). New backends say so via the reply's goal rider;
        // a legacy success reply without the field falls back to an
        // explicit hide — the goal IS gone backend-side either way.
        if ('goal' in r) {
          this.publishGoalState(r)
        } else {
          this.publish({ payload: { goal: null }, type: 'goal.state' })
        }

        return out('Conversation cleared.')
      }

      case 'compact': {
        const r = (await this.controlQuery('compact', { instructions: arg ?? null })) as any

        return out(`Compacted${r?.tokens_saved ? ` (saved ~${r.tokens_saved} tokens)` : ''}.`)
      }

      case 'context': {
        const r = (await this.controlQuery('get_context_usage', {})) as any
        const pct = r?.percentage == null ? '?' : Math.round(r.percentage)

        return out(`Context: ${r?.total_tokens ?? '?'}/${r?.max_tokens ?? '?'} tokens (${pct}%).`)
      }

      case 'cost': {
        // The original /cost prints formatTotalCost over live cost-tracker
        // state (commands/cost/cost.ts:23); clawcodex's accounting lives in
        // the backend bootstrap singleton, so pull a fresh snapshot.
        const r = (await this.controlQuery('cost', {})) as CostSnapshot | null

        if (!r || Object.keys(r).length === 0) {
          return out('Cost totals unavailable (backend not ready).')
        }

        setLastCostSnapshot(r)

        return out(formatTotalCost(r))
      }

      case 'effort': {
        const r = (await this.controlQuery('set_effort', { effort: arg ?? null })) as any

        if (r && r.ok === false) {return out(`effort: ${r.error ?? 'invalid value'}`)}

        if (r?.effort === 'ultracode') {
          return out('Ultracode on: workflow auto-orchestration for this session (reset with /effort high).')
        }

        return out(`Effort: ${r?.effort ?? arg ?? '(unchanged)'}.`)
      }

      case 'mode': {
        // Bare `/mode` is a no-op query, not a set — don't send an empty mode
        // (the server would reject it as invalid). Matches the prior behavior.
        if (arg == null || arg.trim() === '') {return out('Permission mode: (unchanged).')}

        // The server validates the mode and gates bypassPermissions on
        // availability (same guard as the Shift+Tab cycle) — surface its
        // verdict instead of echoing the arg as if it took effect.
        const r = (await this.controlQuery('set_permission_mode', { mode: arg.trim() })) as any

        if (r && r.ok === false) {return out(`mode: ${r.error ?? 'invalid mode'}`)}

        // Only badge the mode the server actually confirmed — a rejected set
        // must not flip the composer's permission-mode indicator.
        const mode = typeof r?.mode === 'string' ? r.mode : arg.trim()

        if (mode) {
          this.publish({ payload: { mode: String(mode) }, type: 'permission.mode' })
        }

        return out(`Permission mode: ${mode}.`)
      }

      case 'model':
        await this.controlQuery('set_model', { model: arg })

        return out(`Model set to ${arg ?? '(unchanged)'}.`)
      case 'output-style': {
        if (arg) {
          const r = (await this.controlQuery('set_output_style', { style: arg })) as any

          if (r?.ok === false) {
            const avail = Array.isArray(r?.available_styles) ? ` Available: ${r.available_styles.join(', ')}.` : ''

            return out(`${r?.error ?? 'Failed to set output style.'}${avail}`)
          }

          return out(`Output style: ${r?.style ?? arg}.`)
        }

        const st = (await this.controlQuery('get_settings', {})) as any
        const avail = Array.isArray(st?.available_output_styles) ? st.available_output_styles : []
        const current = st?.output_style ?? 'default'

        return out(
          avail.length
            ? `Output style: ${current}. Available: ${avail.map((n: string) => (n === current ? `${n} (current)` : n)).join(', ')}.`
            : `Output style: ${current}.`
        )
      }

      case 'provider': {
        const r = (await this.controlQuery('set_provider', { provider: arg })) as any

        return out(`Provider: ${r?.provider ?? arg ?? '(unchanged)'}${r?.model ? ` (model ${r.model})` : ''}.`)
      }

      case 'rewind': {
        const r = (await this.controlQuery('rewind', { turns: arg ? Number(arg) || 1 : 1 })) as any

        return out(`Rewound ${r?.removed ?? 0} turn(s).`)
      }

      case 'thinking': {
        const r = (await this.controlQuery('set_thinking', { action: arg ?? 'toggle' })) as any

        return out(`Thinking ${r?.thinking ? 'on' : 'off'}.`)
      }

      case 'bg': {
        if (arg) {
          const r = (await this.controlQuery('bg_agent', { command: arg })) as any

          return out(`Started background agent ${r?.id ?? ''}.`)
        }

        const r = (await this.controlQuery('bg_list', {})) as any
        const tasks = Array.isArray(r?.tasks) ? r.tasks : []

        return out(tasks.length ? tasks.map((t: any) => `${t.id} [${t.status}] ${t.command}`).join('\n') : 'No background tasks.')
      }

      case 'insights': {
        const r = (await this.controlQuery('insights', {})) as any

        return out(r?.insights ? String(r.insights) : 'No insights available.')
      }

      case 'knowledge': {
        const r = (await this.controlQuery('knowledge', { action: arg || 'status' })) as any

        const bits = [
          r?.enabled != null ? `enabled=${r.enabled}` : '',
          r?.semantic != null ? `semantic=${r.semantic}` : ''
        ].filter(Boolean)

        return out(`Knowledge ${bits.join(' ') || safeJson(r ?? {})}`)
      }

      case 'plan': {
        const r = (await this.controlQuery('plan', { action: arg ? 'set' : 'view', text: arg })) as any

        return out(r?.plan ? `Plan:\n${r.plan}` : 'No plan set.')
      }

      case 'goal': {
        // Hermes /goal contract: SET replies carry a kickoff — the app
        // renders the notice as a system line and submits the condition as
        // the first goal turn ({type:'send'} in createSlashHandler). Every
        // other subcommand (status/clear/pause/resume) is plain exec text.
        const r = (await this.controlQuery('goal', { arg: arg ?? '' })) as any

        if (!r || Object.keys(r).length === 0) {return out('goal: backend not ready')}

        this.publishGoalState(r)

        if (r.ok && typeof r.kickoff === 'string' && r.kickoff) {
          return { message: r.kickoff, notice: typeof r.notice === 'string' ? r.notice : undefined, type: 'send' }
        }

        return out(String(r.text ?? r.error ?? 'goal: no response'))
      }

      case 'subgoal': {
        const r = (await this.controlQuery('subgoal', { arg: arg ?? '' })) as any

        if (!r || Object.keys(r).length === 0) {return out('subgoal: backend not ready')}

        this.publishGoalState(r)

        return out(String(r.text ?? r.error ?? 'subgoal: no response'))
      }

      case 'rename': {
        const r = (await this.controlQuery('rename', { name: arg })) as any

        return out(`Renamed to ${r?.name ?? arg ?? '(unchanged)'}.`)
      }

      case 'resume': {
        if (arg) {
          const r = (await this.controlQuery('resume', { session_id: arg })) as any
          this.publishSessionStats(r)

          // mode_banner: coordinator-mode flip notice (matchSessionMode) —
          // e.g. "Entered coordinator mode to match resumed session."
          const banner = typeof r?.mode_banner === 'string' && r.mode_banner ? `\n${r.mode_banner}` : ''

          return out(`Resumed ${arg} (${r?.count ?? 0} messages).${banner}`)
        }

        const r = (await this.controlQuery('list_sessions', {})) as any
        const ss = Array.isArray(r?.sessions) ? r.sessions : []

        return out(
          ss.length
            ? `Sessions:\n${ss.slice(0, 10).map((s: any) => `${s.session_id} — ${s.preview ?? ''}`).join('\n')}\nUse /resume <id>`
            : 'No saved sessions.'
        )
      }

      case 'skills':
        // Reached only via the slash-worker fallback for unknown subcommands —
        // the TUI-local /skills (ops.ts) owns the hub + list/inspect/search.
        return out('usage: /skills [list | inspect <name> | search <query>] — bare /skills opens the hub')
      case 'workflows': {
        const r = (await this.controlQuery('workflows', {})) as any

        if (r && r.ok === false) {return out(`workflows: ${r.error ?? 'unavailable'}`)}

        return out(String(r?.text ?? 'No workflow runs.'))
      }

      default: {
        // Workflow commands (/deep-research + saved .claude/workflows/*.py):
        // the backend expands the directive; the app submits it as a prompt so
        // the model launches the run via the Workflow tool.
        const r = (await this.controlQuery('workflow_command', { args: arg ?? '', name })) as any

        if (r?.ok && typeof r.prompt === 'string' && r.prompt) {
          return {
            message: r.prompt,
            notice: typeof r.notice === 'string' ? r.notice : undefined,
            type: 'send'
          }
        }

        return out(`/${name} isn't wired into the clawcodex backend yet.`)
      }
    }
  }

  // @-file mention completion, served from the workspace filesystem (shell-style:
  // resolve the dir part of the typed word, list it, filter by the basename).
  private completePath(word: string): Array<{ display: string; meta: string; text: string }> {
    try {
      const cwd = process.env.CLAWCODEX_WORKSPACE || process.env.CLAWCODEX_CWD || process.cwd()
      const stripped = word.startsWith('@') ? word.slice(1) : word
      const slash = stripped.lastIndexOf('/')
      const dirPart = slash === -1 ? '' : stripped.slice(0, slash + 1)
      const base = (slash === -1 ? stripped : stripped.slice(slash + 1)).toLowerCase()
      const absDir = pathResolve(cwd, dirPart || '.')

      return readdirSync(absDir, { withFileTypes: true })
        .filter(e => !e.name.startsWith('.') && e.name.toLowerCase().startsWith(base))
        .slice(0, 50)
        .map(e => {
          const isDir = e.isDirectory()
          const rel = dirPart + e.name + (isDir ? '/' : '')

          return { display: rel, meta: isDir ? 'dir' : 'file', text: rel }
        })
    } catch {
      return []
    }
  }
  // Rich Edit/Write result forwarded by the agent-server (`tool_use_result`
  // on the user envelope, trimmed to the display shape). Shape-detected from
  // the value itself — self-describing type/filePath/structuredPatch — so it
  // works even when the tool_use bookkeeping is empty (mid-turn attach).
  private structuredDiff(value: any): StructuredDiffPayload | undefined {
    if (!value || typeof value !== 'object') {return undefined}
    const kind = value.type

    if (kind !== 'create' && kind !== 'update') {return undefined}

    if (typeof value.filePath !== 'string' || !Array.isArray(value.structuredPatch)) {return undefined}
    const hunks: PatchHunk[] = []

    for (const h of value.structuredPatch) {
      if (!h || typeof h !== 'object' || !Array.isArray(h.lines)) {return undefined}
      hunks.push({
        lines: h.lines.map(String),
        newLines: Number(h.newLines ?? 0),
        newStart: Number(h.newStart ?? 1),
        oldLines: Number(h.oldLines ?? 0),
        oldStart: Number(h.oldStart ?? 1)
      })
    }

    return {
      ...(typeof value.content === 'string' && { content: value.content }),
      filePath: value.filePath,
      ...(typeof value.firstLine === 'string' && { firstLine: value.firstLine }),
      hunks,
      kind
    }
  }

  // WebSearch display data on the same envelope (agent_server trims the
  // structured output to searchCount/durationSeconds). Shape-detected like
  // structuredDiff so it renders without tool_use bookkeeping.
  private webSearchDisplay(value: any): undefined | WebSearchDisplay {
    if (!value || typeof value !== 'object' || value.type !== 'web_search') {return undefined}
    const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)

    return { durationSeconds: num(value.durationSeconds), searchCount: num(value.searchCount) }
  }

  // Legacy fallback (agent-server predating tool_use_result): a fake unified
  // diff from the Edit/Write tool *input* so the app can render at least a
  // colored ```diff block. No line numbers/context — superseded by
  // structuredDiff whenever the backend forwards the real patch.
  private editDiff(name: string, input: any): string | undefined {
    try {
      const file = String(input?.file_path ?? input?.path ?? '')

      if (name === 'Write') {
        const body = String(input?.content ?? '')
          .split('\n')
          .map(l => '+' + l)
          .join('\n')

        return body ? `+++ ${file}\n${body}` : undefined
      }

      if (name === 'Edit') {
        const oldB = String(input?.old_string ?? '')
          .split('\n')
          .map(l => '-' + l)
          .join('\n')

        const newB = String(input?.new_string ?? '')
          .split('\n')
          .map(l => '+' + l)
          .join('\n')

        return `--- ${file}\n+++ ${file}\n${oldB}\n${newB}`
      }
    } catch {
      /* ignore */
    }

    return undefined
  }

  // ── clawcodex NDJSON → clawcodex GatewayEvent ───────────────────────────────
  private dispatch(msg: any): void {
    switch (msg?.type) {
      case 'assistant': {
        const content = msg.message?.content

        if (Array.isArray(content)) {
          for (const b of content) {
            if (b?.type === 'tool_use') {
              this.ensureMsgStart()
              this.toolInputs.set(String(b.id), { input: b.input, name: String(b.name ?? '') })
              this.publish({
                payload: {
                  args_text: safeJson(b.input),
                  context: toolContext(b.input),
                  name: b.name,
                  todos: todosFromInput(b.name, b.input),
                  tool_id: b.id
                },
                type: 'tool.start'
              })
            }
          }
        }

        break
      }

      case 'control_request':
        this.handleServerControl(msg)

        break

      case 'control_response':
        this.resolvePending(msg)

        break

      case 'result':
        this.publish({
          payload: {
            // Running session totals for /cost + the exit summary.
            cost: msg.cost && typeof msg.cost === 'object' ? msg.cost : undefined,
            permission_mode: typeof msg.permission_mode === 'string' ? msg.permission_mode : undefined,
            session_turns: typeof msg.session_turns === 'number' ? msg.session_turns : undefined,
            text: typeof msg.result === 'string' ? msg.result : undefined,
            usage: msg.usage
          },
          type: 'message.complete'
        })
        this.msgStarted = false

        if (msg.is_error || msg.subtype === 'error') {
          this.publish({ payload: { message: String(msg.error ?? msg.result ?? 'error') }, type: 'error' })
        }

        break
      case 'stream_event': {
        const d = msg.event?.delta

        if (d?.type === 'text_delta' && d.text) {
          this.ensureMsgStart()
          this.publish({ payload: { text: d.text }, type: 'message.delta' })
        } else if (d?.type === 'thinking_delta' && d.thinking) {
          this.ensureMsgStart()
          this.publish({ payload: { text: d.thinking }, type: 'thinking.delta' })
        }

        break
      }

      case 'system':
        if (msg.subtype === 'init') {
          this.sessionId = String(msg.session_id ?? '')
          this.sessionInfo = this.toSessionInfo(msg)
          this.readyResolve?.()
          this.publish({ payload: {}, session_id: this.sessionId, type: 'gateway.ready' })
          this.publish({ payload: this.sessionInfo, session_id: this.sessionId, type: 'session.info' })
        } else if (msg.subtype === 'status') {
          if (msg.level === 'error') {
            this.publish({ payload: { message: String(msg.message ?? 'error') }, type: 'error' })
          } else {
            this.publish({ payload: { kind: 'status', text: String(msg.message ?? '') }, type: 'status.update' })
          }
        } else if (msg.subtype === 'task_notification') {
          // A background workflow/agent finished: render the completion banner
          // as a persistent system transcript line ("[bg <id>] ✔ … completed").
          this.publish({
            payload: { task_id: String(msg.task_id ?? 'task'), text: String(msg.message ?? '') },
            type: 'background.complete'
          })
        } else if (msg.subtype === 'goal_status') {
          // /goal verdict line ("✓ Goal achieved" / "↻ Continuing" / "⏸ …").
          // kind:'goal' routes to the hermes-ported handler in
          // createGatewayEventHandler (sys transcript line + brief status).
          this.publish({
            payload: { kind: 'goal', text: String(msg.message ?? '') },
            type: 'status.update'
          })

          // Indicator refresh: every loop transition (continue/done/paused/
          // restored) rides here. Backends without the snapshot field only
          // clear on an explicit goal_active=false — never invent state.
          if ('goal' in msg) {
            this.publishGoalState(msg)
          } else if (msg.goal_active === false) {
            this.publish({ payload: { goal: null }, type: 'goal.state' })
          }
        }

        break
      case 'user': {
        const content = msg.message?.content

        if (Array.isArray(content)) {
          // The backend builds one user message per tool result, so a
          // message-level tool_use_result belongs to the lone block. Consume
          // it on first attach so a hypothetical multi-block message can't
          // pin the same patch onto every result.
          let structured = this.structuredDiff(msg.tool_use_result)
          let webSearch = this.webSearchDisplay(msg.tool_use_result)

          for (const b of content) {
            if (b?.type === 'tool_result') {
              const stored = this.toolInputs.get(String(b.tool_use_id))
              // A failed edit must not render a diff at all — neither the
              // real patch nor a fabricated one for an edit that never ran.
              const isError = Boolean(b.is_error)
              const fullText = typeof b.content === 'string' ? b.content : safeJson(b.content)
              const resultText = formatToolResult(stored?.name, fullText, isError, webSearch)
              // Read shows no expand hint (the summary loses nothing the user
              // needs — the file is in context), so retain nothing for it.
              const expandable = stored?.name !== 'Read'
              this.publish({
                payload: {
                  // error drives the ✗ mark (red bullet + red result rows);
                  // without it a real failure renders as a green success.
                  error: isError ? resultText : undefined,
                  inline_diff:
                    isError || structured || !stored ? undefined : this.editDiff(stored.name, stored.input),
                  name: stored?.name,
                  result_raw: expandable ? rawToolResult(resultText, fullText) : undefined,
                  result_text: resultText,
                  structured_diff: isError ? undefined : structured,
                  todos: isError ? undefined : todosFromInput(stored?.name, stored?.input),
                  tool_id: b.tool_use_id
                },
                type: 'tool.complete'
              })
              structured = undefined
              webSearch = undefined
              this.toolInputs.delete(String(b.tool_use_id))
            }
          }
        }

        break
      }

      case 'agent_progress': {
        // ch13 round-4 — the backend emits rich subagent progress
        // (agent.py ProgressTracker) but the bridge dropped it, so the
        // subagent HUD stayed dark during Task/Agent delegation. Map it to
        // the subagent.* events the app renderer already handles.
        const aid = String(msg.agent_id ?? '')

        if (aid) {
          const payload: any = {
            depth: msg.depth ?? 0,
            goal: msg.description || msg.name || 'subagent',
            subagent_id: aid,
            subagent_type: msg.subagent_type
          }

          if (!this.seenSubagents.has(aid)) {
            this.seenSubagents.add(aid)
            this.publish({ payload: { ...payload, status: 'running' }, type: 'subagent.start' })
          }

          const activity = String(msg.activity ?? '').trim()

          if (activity) {
            // ch13 round-4 — map to the field names the HUD renderer reads
            // (turnController: output_tokens / tool_count), not the backend's
            // raw `tokens`/`tool_use_count` which the renderer ignores.
            this.publish({
              payload: { ...payload, text: activity, output_tokens: msg.tokens, tool_count: msg.tool_use_count },
              type: 'subagent.progress'
            })
          }

          const status = String(msg.status ?? '')

          if (status === 'completed' || status === 'failed' || status === 'killed') {
            this.publish({ payload: { ...payload, status }, type: 'subagent.complete' })
          }
        }

        break
      }
      // keep_alive / streamlined_* → ignored for the basic port
    }
  }

  private ensureMsgStart(): void {
    if (!this.msgStarted) {
      this.msgStarted = true
      this.publish({ type: 'message.start' })
    }
  }

  // Server-initiated control requests (tool permission / elicitation).
  private handleServerControl(msg: any): void {
    const req = msg.request

    if (req?.subtype === 'can_use_tool') {
      // ch13 round-4 — carry the backend's permission SUGGESTIONS so the
      // "don't ask again" choice persists a real rule.
      const suggestions: any[] = Array.isArray(req.suggestions) ? req.suggestions : []
      this.pendingApproval = { input: req.input, request_id: String(msg.request_id ?? ''), suggestions }
      // Show the ACTUAL command/action under review, not the tool name or a raw
      // JSON dump — and carry the editable grant rule separately so the box can
      // offer a broadenable "don't ask again for <rule>" option. Only offer the
      // persistable option when the backend sent a rule.
      // Editable rule only when the suggestion carries exactly ONE rule — a
      // compound command's suggestion bundles several (grep:*, tr:*, …) and
      // is accepted/declined as a set (static label via session_label).
      const suggestionRules = Array.isArray(suggestions[0]?.rules) ? suggestions[0].rules : []
      const ruleContent = suggestionRules.length === 1 ? (suggestionRules[0]?.rule_content ?? null) : null
      this.publish({
        payload: {
          allow_permanent: suggestions.length > 0,
          command: approvalCommandText(req.input),
          rule: ruleContent,
          rule_label: describeSuggestionRule(suggestions[0]),
          // Authoritative per-tool wording for the persist option (e.g. "allow
          // all edits during this session"); the box uses it verbatim for
          // non-Bash tools instead of a generic "don't ask again for <tool>".
          session_label: typeof req.session_label === 'string' ? req.session_label : null,
          tool_name: String(req.tool_name ?? 'tool'),
          // Destructive-command caution (backend-computed) → warning line.
          warning: typeof req.warning === 'string' && req.warning ? req.warning : null
        },
        type: 'approval.request'
      })
    } else if (req?.subtype === 'mcp_elicitation') {
      this.publish({
        payload: { choices: null, question: 'Input requested', request_id: String(msg.request_id ?? '') },
        type: 'clarify.request'
      })
    }
  }

  private handleExit(code: null | number, reason?: string): void {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }

    const err = new Error(reason || `agent-server exited${code === null ? '' : ` (${code})`}`)

    for (const p of this.pending.values()) {p.reject(err)}
    this.pending.clear()

    if (this.subscribed) {this.emit('exit', code)}
    else {this.pendingExit = code}
  }

  private publish(ev: GatewayEvent): void {
    if (ev.type === 'gateway.ready' && this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }

    if (this.subscribed) {this.emit('event', ev)}
    else {this.buffered.push(ev)}
  }

  /** /goal indicator refresh from any carrier with a `goal` snapshot field
   *  (goal/subgoal/clear replies, goal_status events). A carrier WITHOUT the
   *  field (older backend) is a no-op — never invent or drop state on
   *  silence. `goal_rev` rides along so the store can drop stale carriers. */
  private publishGoalState(carrier: unknown): void {
    const c = carrier as { goal?: unknown; goal_rev?: unknown; session_id?: unknown } | null

    if (!c || typeof c !== 'object' || !('goal' in c)) {
      return
    }

    const goal = c.goal && typeof c.goal === 'object' ? (c.goal as GoalSnapshot) : null

    this.publish({
      payload: { goal, rev: typeof c.goal_rev === 'number' ? c.goal_rev : undefined },
      session_id: typeof c.session_id === 'string' ? c.session_id : undefined,
      type: 'goal.state'
    })
  }

  /** Stats-line refresh from a clear/resume reply's rider (session_turns +
   *  cost snapshot). Silently a no-op for replies without the fields. */
  private publishSessionStats(r: unknown): void {
    const reply = r as { cost?: unknown; session_turns?: unknown } | null

    if (typeof reply?.session_turns !== 'number' && !reply?.cost) {
      return
    }

    this.publish({
      payload: {
        cost: reply.cost && typeof reply.cost === 'object' ? (reply.cost as any) : undefined,
        session_turns: typeof reply.session_turns === 'number' ? reply.session_turns : undefined
      },
      type: 'session.stats'
    })
  }

  private pushLog(line: string): void {
    this.logs.push(line)

    if (this.logs.length > MAX_LOG_LINES) {this.logs.shift()}
  }

  private resolvePending(msg: any): void {
    const r = msg.response
    const id = r?.request_id
    const p = id ? this.pending.get(id) : undefined

    if (!p) {return}
    this.pending.delete(id)

    if (r.subtype === 'error') {p.reject(new Error(String(r.error ?? 'error')))}
    else {p.resolve(r.response)}
  }

  private send(obj: unknown): void {
    try {
      this.proc?.stdin?.write(JSON.stringify(obj) + '\n')
    } catch {
      // best effort
    }
  }

  private sendControl(subtype: string, params: Record<string, unknown>): void {
    this.send({ request: { subtype, ...params }, request_id: `c${++this.reqId}`, type: 'control_request' })
  }

  private toSessionInfo(init: any): SessionInfo {
    const toolNames: string[] = Array.isArray(init.tools)
      ? init.tools.map((t: any) => t?.name).filter(Boolean)
      : []

    return {
      cwd: init.cwd,
      model: String(init.model ?? ''),
      permission_mode: typeof init.permission_mode === 'string' ? init.permission_mode : undefined,
      profile_name: init.provider ? String(init.provider) : undefined,
      skills: {},
      tools: { '': toolNames },
      // The app gates "ready" on info.version (useSessionLifecycle:227) and the
      // banner shows it as "clawcodex v{version}", so this is the app version,
      // not the wire protocol_version.
      version: CLAWCODEX_VERSION
    } as SessionInfo
  }
}
