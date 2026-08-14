/**
 * The ClawCodex gateway wire contract, as the browser sees it.
 *
 * Server side is `src/server/desktop_gateway.py` (+ `_methods` / `_translate`);
 * this file is the client-side mirror of that vocabulary and the single place
 * the web client is coupled to it. Two frame shapes cross the socket:
 *
 *   client → server  {"jsonrpc":"2.0","id":"r7","method":M,"params":P}
 *   server → client  {"id":"r7","result":R} | {"id":"r7","error":{message}}
 *                    {"method":"event","params":{type,session_id?,payload?}}
 *
 * A pushed event NEVER carries an id — the client's pending-call map would
 * swallow it.
 */

/* ── events (server → client pushes) ─────────────────────────────────────── */

export interface GatewayReadyPayload {
  app?: string
  change_events?: boolean
}

/** Live session facts, republished after every turn and settings change. */
export interface SessionInfoPayload {
  approval_mode?: 'manual' | 'smart' | 'off'
  cwd?: string
  desktop_contract?: number
  model?: string
  provider?: string
  reasoning_effort?: string
  running?: boolean
  stored_session_id?: string
}

export interface MessageDeltaPayload {
  text: string
}

export interface MessageCompletePayload {
  error?: string
  partial?: boolean
  status?: 'ok' | 'error'
  text?: string
  usage?: UsagePayload
}

export interface UsagePayload {
  calls: number
  input: number
  output: number
  total: number
}

/**
 * `tool.start` — one running tool row. `name` has already passed through the
 * server's vocabulary adapter (Read → read_file, Bash → terminal, …), so the
 * renderer keys its per-tool card off these names, not the raw tool names.
 */
export interface ToolStartPayload {
  args?: Record<string, unknown>
  context?: string
  name: string
  tool_id: string
}

/**
 * `tool.complete` — the same row, resolved. Each tool family reads its output
 * from a different field, which is what the server's `render_tool_result`
 * produces: `content` for a read, `output` for a shell run, `inline_diff` for
 * an edit.
 */
export interface ToolCompletePayload {
  error?: string
  name?: string
  result?: ToolResult
  tool_id: string
}

export interface ToolResult {
  content?: string
  context?: string
  duration_s?: number
  file_count?: number
  inline_diff?: string
  match_count?: number
  message?: string
  output?: string
  path?: string
  result_count?: number
}

export interface ApprovalRequestPayload {
  command?: string
  description?: string
  input?: Record<string, unknown>
  suggestions?: unknown[]
  tool_name?: string
  warning?: string
}

/** Every push type this client acts on; anything else is ignored by design. */
export type GatewayEventType =
  | 'approval.request'
  | 'error'
  | 'gateway.ready'
  | 'message.complete'
  | 'message.delta'
  | 'message.interim'
  | 'message.start'
  | 'reasoning.delta'
  | 'session.info'
  | 'sessions.changed'
  | 'thinking.delta'
  | 'tool.complete'
  | 'tool.start'
  | (string & {})

export interface GatewayEvent<P = unknown> {
  payload?: P
  session_id?: string
  type: GatewayEventType
}

/* ── methods (client → server calls) ─────────────────────────────────────── */

export interface SessionCreateResult {
  info?: SessionInfoPayload
  session_id: string
  stored_session_id?: string
}

export interface StoredMessage {
  content?: unknown
  role?: string
  [key: string]: unknown
}

export interface SessionResumeResult extends SessionCreateResult {
  message_count?: number
  messages?: StoredMessage[]
  messages_omitted?: boolean
  resumed?: string
}

export interface ModelOption {
  authenticated?: boolean
  auth_type?: string
  is_current?: boolean
  models?: string[]
  name: string
  label?: string
}

export interface ModelOptionsResult {
  model?: string | null
  provider?: string | null
  providers?: ModelOption[]
}

export interface SessionRow {
  cwd?: string | null
  id: string
  is_active?: boolean
  last_active?: string | null
  message_count?: number
  model?: string | null
  preview?: string
  source?: string
  started_at?: string | null
  title?: string
}

/**
 * `projects.tree` shape (`src/server/desktop_projects.py`): one project per
 * git repo root, each holding repos whose *groups* are the repo's checkouts —
 * the main lane plus one per linked worktree that has sessions in it. Sessions
 * with no cwd, or a cwd outside any repo, land in the synthetic "Home" project.
 */
export interface ProjectLane {
  id: string
  isMain?: boolean
  label: string
  path: string | null
  sessions: SessionRow[]
}

export interface ProjectRepo {
  groups: ProjectLane[]
  id: string
  label: string
  path: string | null
  sessionCount?: number
}

export interface ProjectNode {
  id: string
  isNoProject?: boolean
  label: string
  lastActive?: number
  path: string | null
  previewSessions?: SessionRow[]
  repos: ProjectRepo[]
  sessionCount?: number
}

export interface ProjectsTreeResult {
  active_id?: string | null
  projects?: ProjectNode[]
  scoped_session_ids?: string[]
}

/**
 * `commands.catalog` (`src/server/desktop_commands.py`): a flat `[name, desc]`
 * pair list plus per-command argument hints and a skill-origin map.
 */
export interface CommandsCatalogResult {
  hints?: Record<string, string>
  pairs?: [string, string][]
  skill_count?: number
  skills?: Record<string, { origin?: string; usage?: number }>
}

/** One row in the composer's slash popover. */
export interface CommandEntry {
  description: string
  hint?: string
  name: string
  origin?: string
}

/** `slash.exec` / `command.dispatch` result. */
export type SlashResult =
  | { output: string; type: 'exec' }
  | { message: string; name: string; type: 'skill' }

/**
 * `session.usage` → the agent's `get_context_usage` control
 * (`src/server/agent_server.py::_context_usage`). Best-effort by contract: any
 * failure degrades to `{protocol_version, error}` rather than raising, so
 * every field here is optional and the meter hides itself without a reading.
 *
 * `percentage` is 0–100, already rounded to one decimal.
 */
export interface ContextUsageResult {
  categories?: { name: string; tokens: number }[]
  error?: string
  max_tokens?: number
  percentage?: number
  protocol_version?: string
  total_tokens?: number
}

/** Approval answers the gateway understands (`approval.respond`). */
export type ApprovalChoice = 'allow' | 'deny' | 'session' | 'always'
