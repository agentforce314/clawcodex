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

import type { GatewayEvent } from './gatewayTypes.js'
import type { SessionInfo } from './types.js'

const STARTUP_TIMEOUT_MS = 30_000
const MAX_LOG_LINES = 500
const RPC_TIMEOUT_MS = 5_000
// clawcodex app version shown in the banner ("clawcodex v{version}"). Keep in
// sync with the installer (install.sh INSTALLER_VERSION).
const CLAWCODEX_VERSION = '0.7.0'

/** Command that launches the clawcodex agent-server (set by the Python launcher). */
function resolveAgentCmd(): string[] {
  const raw = process.env.CLAWCODEX_AGENT_SERVER_CMD?.trim()
  return raw ? raw.split(/\s+/) : ['clawcodex', 'agent-server']
}

function safeJson(v: unknown): string {
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

/** Pick the salient arg for a tool so the trail label reads `Bash(ls)` /
 *  `Read(package.json)` / `Grep(TODO)` (Claude-style) instead of a bare tool
 *  name. File paths are shown relative to the workspace so the label stays
 *  short; search tools show their pattern rather than the search directory. */
function toolContext(input: any): string {
  if (!input || typeof input !== 'object') return ''
  if (input.pattern != null) return String(input.pattern)
  const p = input.file_path ?? input.path ?? input.notebook_path
  if (p != null) return relativizePath(String(p))
  const v = input.command ?? input.url ?? input.query ?? input.description ?? input.prompt
  return v == null ? '' : String(v)
}

/** Shorten an absolute path to a workspace-relative path (or basename). */
function relativizePath(p: string): string {
  const ws = (process.env.CLAWCODEX_WORKSPACE || process.env.CLAWCODEX_CWD || process.cwd()).replace(/\/+$/, '')
  if (ws && p.startsWith(ws + '/')) return p.slice(ws.length + 1)
  const parts = p.split('/')
  return parts[parts.length - 1] || p
}

/** Summarize a tool result for the trail. A successful Read returns
 *  line-numbered file contents (cat -n: `N\t…`), which read as noise when
 *  crammed onto one line, so collapse it to a line count (Claude-style). Only
 *  genuine numbered output is collapsed — errors (is_error) and Read's other
 *  acknowledgements (empty-file / file_unchanged warnings, PDF/image stubs)
 *  aren't `N\t…` text and pass through, so nothing is mislabeled or hidden. */
function formatToolResult(name: string | undefined, result: string, isError = false): string {
  if (!result || isError) return result
  if (name === 'Read' && /^\s*\d+\t/.test(result)) {
    const n = result.split('\n').filter(l => l.length > 0).length
    return `Read ${n} line${n === 1 ? '' : 's'}`
  }
  return result
}

/** clawcodex-backed slash commands (handled via command.dispatch → dispatchSlash).
 *  Drives both the catalog (recognition) and the complete.slash menu. */
const SLASHES: ReadonlyArray<{ desc: string; name: string }> = [
  { desc: 'Show available commands', name: '/help' },
  { desc: 'Clear the conversation', name: '/clear' },
  { desc: 'Switch the model', name: '/model' },
  { desc: 'Set the permission mode', name: '/mode' },
  { desc: 'Compact the conversation to save context', name: '/compact' },
  { desc: 'Show context-window usage', name: '/context' },
  { desc: 'Undo recent turns', name: '/rewind' },
  { desc: 'Toggle extended thinking', name: '/thinking' },
  { desc: 'Set reasoning effort', name: '/effort' },
  { desc: 'Switch the provider', name: '/provider' },
  { desc: 'Search / manage the knowledge base', name: '/knowledge' },
  { desc: 'View or set the plan', name: '/plan' },
  { desc: 'Generate session insights', name: '/insights' },
  { desc: 'List or start background agents', name: '/bg' },
  { desc: 'Resume a past session', name: '/resume' },
  { desc: 'Rename this session', name: '/rename' }
]

type Pending = { reject: (e: Error) => void; resolve: (v: unknown) => void }

export class GatewayClient extends EventEmitter {
  private buffered: GatewayEvent[] = []
  private logs: string[] = []
  // The tool-permission request currently awaiting the user's choice.
  private pendingApproval: { input: unknown; request_id: string } | null = null
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
      if (!line) return
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
    for (const ev of this.buffered) this.emit('event', ev)
    this.buffered = []
    if (this.pendingExit !== undefined) this.emit('exit', this.pendingExit)
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
        const pairs = SLASHES.map(s => [s.name, s.desc] as [string, string])
        const canon: Record<string, string> = {}
        for (const s of SLASHES) canon[s.name] = s.name
        return Promise.resolve({ canon, categories: [], pairs, skill_count: 0, sub: {} } as T)
      }
      case 'complete.slash': {
        const text = String(p.text ?? '').toLowerCase() || '/'
        const items = SLASHES.filter(s => s.name.toLowerCase().startsWith(text)).map(s => ({
          display: s.name,
          meta: s.desc,
          text: s.name
        }))
        return Promise.resolve({ items, replace_from: 1 } as T)
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
        if (key === 'model') this.sendControl('set_model', { model: value })
        else if (key === 'permission_mode') this.sendControl('set_permission_mode', { mode: value })
        else if (key === 'effort' || key === 'reasoning') this.sendControl('set_effort', { effort: value })
        else if (key === 'provider') this.sendControl('set_provider', { provider: value })
        else if (key === 'thinking') this.sendControl('set_thinking', { action: value })
        return Promise.resolve({ ok: true } as T)
      }
      case 'session.activate':
      case 'session.create':
      case 'session.resume':
        // clawcodex runs a single agent-server session; hand back its id once
        // system/init has set it. The app then enables the composer.
        return this.readyPromise.then(() => ({ info: this.sessionInfo ?? undefined, session_id: this.sessionId }) as T)
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
          this.send({
            response: {
              request_id: ap.request_id,
              response: deny
                ? { behavior: 'deny', message: 'Denied by user' }
                : { behavior: 'allow', updatedInput: ap.input }
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

  // ── event plumbing ───────────────────────────────────────────────────────
  private controlQuery(subtype: string, params: Record<string, unknown>): Promise<unknown> {
    const requestId = `q${++this.reqId}`
    return new Promise(resolve => {
      this.pending.set(requestId, { reject: () => resolve(null), resolve })
      this.send({ request: { subtype, ...params }, request_id: requestId, type: 'control_request' })
      setTimeout(() => {
        if (this.pending.has(requestId)) {
          this.pending.delete(requestId)
          resolve(null)
        }
      }, RPC_TIMEOUT_MS)
    })
  }

  // Map a slash command (name + optional arg) to a clawcodex control_request
  // and return a CommandDispatchResponse (`type:'exec'` + human-readable output
  // the app prints). Unknown commands report that they aren't wired yet.
  private async dispatchSlash(name: string, arg?: string): Promise<{ output: string; type: string }> {
    const out = (output: string) => ({ output, type: 'exec' })
    switch (name) {
      case 'clear':
        await this.controlQuery('clear', {})
        return out('Conversation cleared.')
      case 'compact': {
        const r = (await this.controlQuery('compact', { instructions: arg ?? null })) as any
        return out(`Compacted${r?.tokens_saved ? ` (saved ~${r.tokens_saved} tokens)` : ''}.`)
      }
      case 'context': {
        const r = (await this.controlQuery('get_context_usage', {})) as any
        const pct = r?.percentage == null ? '?' : Math.round(r.percentage)
        return out(`Context: ${r?.total_tokens ?? '?'}/${r?.max_tokens ?? '?'} tokens (${pct}%).`)
      }
      case 'effort': {
        const r = (await this.controlQuery('set_effort', { effort: arg ?? null })) as any
        return out(`Effort: ${r?.effort ?? arg ?? '(unchanged)'}.`)
      }
      case 'mode':
        await this.controlQuery('set_permission_mode', { mode: arg })
        return out(`Permission mode: ${arg ?? '(unchanged)'}.`)
      case 'model':
        await this.controlQuery('set_model', { model: arg })
        return out(`Model set to ${arg ?? '(unchanged)'}.`)
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
      case 'rename': {
        const r = (await this.controlQuery('rename', { name: arg })) as any
        return out(`Renamed to ${r?.name ?? arg ?? '(unchanged)'}.`)
      }
      case 'resume': {
        if (arg) {
          const r = (await this.controlQuery('resume', { session_id: arg })) as any
          return out(`Resumed ${arg} (${r?.count ?? 0} messages).`)
        }
        const r = (await this.controlQuery('list_sessions', {})) as any
        const ss = Array.isArray(r?.sessions) ? r.sessions : []
        return out(
          ss.length
            ? `Sessions:\n${ss.slice(0, 10).map((s: any) => `${s.session_id} — ${s.preview ?? ''}`).join('\n')}\nUse /resume <id>`
            : 'No saved sessions.'
        )
      }
      default:
        return out(`/${name} isn't wired into the clawcodex backend yet.`)
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

  // Build a unified diff for Edit/Write tool results so the app renders a colored
  // ```diff block (turnController.pushInlineDiffSegment). Other tools → undefined
  // (just the result text shows).
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
                payload: { args_text: safeJson(b.input), context: toolContext(b.input), name: b.name, tool_id: b.id },
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
        }
        break
      case 'user': {
        const content = msg.message?.content
        if (Array.isArray(content)) {
          for (const b of content) {
            if (b?.type === 'tool_result') {
              const stored = this.toolInputs.get(String(b.tool_use_id))
              this.publish({
                payload: {
                  inline_diff: stored ? this.editDiff(stored.name, stored.input) : undefined,
                  name: stored?.name,
                  result_text: formatToolResult(
                    stored?.name,
                    typeof b.content === 'string' ? b.content : safeJson(b.content),
                    Boolean(b.is_error)
                  ),
                  tool_id: b.tool_use_id
                },
                type: 'tool.complete'
              })
              this.toolInputs.delete(String(b.tool_use_id))
            }
          }
        }
        break
      }
      // keep_alive / agent_progress / streamlined_* → ignored for the basic port
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
      this.pendingApproval = { input: req.input, request_id: String(msg.request_id ?? '') }
      this.publish({
        payload: { allow_permanent: true, command: String(req.tool_name ?? 'tool'), description: safeJson(req.input) },
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
    for (const p of this.pending.values()) p.reject(err)
    this.pending.clear()
    if (this.subscribed) this.emit('exit', code)
    else this.pendingExit = code
  }

  private publish(ev: GatewayEvent): void {
    if (ev.type === 'gateway.ready' && this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }
    if (this.subscribed) this.emit('event', ev)
    else this.buffered.push(ev)
  }

  private pushLog(line: string): void {
    this.logs.push(line)
    if (this.logs.length > MAX_LOG_LINES) this.logs.shift()
  }

  private resolvePending(msg: any): void {
    const r = msg.response
    const id = r?.request_id
    const p = id ? this.pending.get(id) : undefined
    if (!p) return
    this.pending.delete(id)
    if (r.subtype === 'error') p.reject(new Error(String(r.error ?? 'error')))
    else p.resolve(r.response)
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
