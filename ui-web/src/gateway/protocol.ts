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
  /**
   * Nano mode (backend `--nano`, docs/nano.md): six-tool pi-style minimal
   * harness. Rendered as a chip beside the model name, like the TUI's.
   * Compared strict `=== true` everywhere — absent on an older backend must
   * stay falsy, never render a stale badge.
   */
  nano?: boolean
  provider?: string
  reasoning_effort?: string
  /** Whether the session's model accepts image input; false hides the attach control. */
  vision?: boolean
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

/**
 * Token counters for one request or turn.
 *
 * The first four are always present; the rest are reported only when the
 * provider supplies them.
 *
 * **`input` is the cache MISS, not the whole prompt.** The backend splits a
 * prompt into the part it paid full price for (`input`) and the part served
 * from cache (`cache_read`), because they are billed differently — see
 * `_build_usage_dict` in `src/providers/deepseek_provider.py`. The full prompt
 * is therefore `input + cache_read`, and so is the correct denominator for a
 * cache-hit rate. `total` follows the backend's own convention of
 * `input + output`, so it excludes the cached part too; use `promptTokens()`
 * rather than reading these fields directly.
 */
export interface UsagePayload {
  cache_read?: number
  cache_write?: number
  calls: number
  input: number
  output: number
  reasoning?: number
  total: number
}

/** The whole prompt: what was paid for plus what came from cache. */
export function promptTokens(usage: UsagePayload): number {
  return usage.input + (usage.cache_read ?? 0)
}

/**
 * `step.complete` — one finished model request inside a turn.
 *
 * A turn's `message.complete` reports totals only; an agentic turn is many
 * requests, and this is what makes the individual ones visible.
 */
export interface StepCompletePayload {
  model?: string
  stop_reason?: string
  usage?: UsagePayload
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
  /**
   * An Agent row's link to the subagent that answered it. Present on a live
   * completion (the server's display envelope) and on a resumed one (the
   * envelope the agent server persisted), so both render alike.
   */
  agent?: AgentResultMeta
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

/**
 * What an Agent call reported about its run, beyond the report text.
 *
 * `agent_id` names the run — its live progress and its transcript file — and
 * the totals are the ones the run counted itself, which the report does not
 * carry. Mirrors `agent_result_meta` in `desktop_gateway_translate.py`.
 */
export interface AgentResultMeta {
  agent_id: string
  agent_type?: string
  duration_ms?: number
  model?: string
  /** `completed`, `interrupted`, or `async_launched` for a background spawn. */
  status: string
  tokens?: number
  tool_count?: number
}

/**
 * `subagent.progress` — one subagent's latest state, while it runs and once
 * more when it stops.
 *
 * `agent_id` is the run's identity; `tool_use_id`, when present, is the Agent
 * call the run answers, which is how a row and its progress find each other
 * before the row's result names the id. A terminal frame carries a `status`
 * other than `running` and no activity.
 */
export interface SubagentProgressPayload {
  activity?: string
  agent_id: string
  depth?: number
  description?: string
  model?: string
  name?: string
  status?: string
  subagent_type?: string
  tokens?: number
  tool_count?: number
  tool_use_id?: string
}

export interface ApprovalRequestPayload {
  command?: string
  description?: string
  input?: Record<string, unknown>
  suggestions?: unknown[]
  tool_name?: string
  warning?: string
}

export interface QuestionOption {
  description?: string
  label: string
}

export interface PendingQuestion {
  header?: string
  multi_select?: boolean
  options: QuestionOption[]
  /**
   * The agent's answer KEY, not just display text — the tool drops any answer
   * whose key it did not ask about, so this string is echoed back verbatim.
   */
  question: string
}

export interface QuestionRequestPayload {
  questions: PendingQuestion[]
  request_id: string
}

/** Every push type this client acts on; anything else is ignored by design. */
export type GatewayEventType =
  | 'approval.request'
  | 'question.request'
  | 'error'
  | 'gateway.ready'
  | 'message.complete'
  | 'message.delta'
  | 'message.interim'
  | 'message.start'
  | 'reasoning.delta'
  | 'session.info'
  | 'session.title'
  | 'sessions.changed'
  | 'subagent.progress'
  | 'thinking.delta'
  | 'tool.complete'
  | 'step.complete'
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
  /**
   * The Agent tool's persisted display envelope, beside its `tool_result`
   * block — the same one a live `tool.complete` carries.
   */
  tool_use_result?: unknown
  [key: string]: unknown
}

export interface SessionResumeResult extends SessionCreateResult {
  message_count?: number
  messages?: StoredMessage[]
  messages_omitted?: boolean
  resumed?: string
  /** The stored name, so the header keeps the title the sidebar row showed. */
  title?: string
}

/**
 * `subagent.transcript` — a subagent's own record, in the stored-message
 * shape `session.resume` uses, so a child rehydrates like its parent.
 *
 * `found` is false, with no messages, when there is no file: a run that
 * predates transcripts, or one whose record was cleaned up. Not an error —
 * the row still has the prompt and the report to show.
 */
export interface SubagentTranscriptResult {
  agent_id: string
  found: boolean
  message_count?: number
  messages?: StoredMessage[]
}

export interface ModelOption {
  authenticated?: boolean
  auth_type?: string
  is_current?: boolean
  /** The environment variable a key could come from, e.g. DEEPSEEK_API_KEY. */
  key_env?: string
  total_models?: number
  models?: string[]
  /** Display name — "DeepSeek", "Anthropic Claude", "Moonshot / Kimi". */
  name: string
  label?: string
  /**
   * The id the backend answers to — "deepseek", not "DeepSeek". Sending the
   * display name back gets "Unknown provider: DeepSeek", so the menu shows
   * `name` and sends `slug`.
   */
  slug?: string
}

/** `settings.general` — the session-side general settings. */
export interface GeneralSettingsResult {
  available_output_styles?: string[]
  language?: string
  output_style?: string
  /** End-of-turn recap on/off; absent when the session did not report it. */
  recap?: boolean
}

/** `provider.list` — every provider, configured or not. Carries no secrets. */
export interface ProviderListResult {
  /** The provider the session runs on now. */
  current?: string
  /** The config default the NEXT session starts on (`default_provider`). */
  default?: string
  providers?: ModelOption[]
}

export interface ProviderMutationResult {
  error?: string
  message?: string
  ok?: boolean
  provider?: ModelOption
}

export interface ModelOptionsResult {
  model?: string | null
  provider?: string | null
  providers?: ModelOption[]
}

/**
 * `effort.options` — the ladder the *model* accepts, asked per model rather
 * than carried on `model.options` (some providers list hundreds).
 *
 * `supported: false` means the model takes no effort parameter at all, and the
 * client is expected to show no control rather than a list it cannot apply.
 */
export interface EffortOptionsResult {
  current?: string
  levels?: string[]
  model?: string
  provider?: string
  supported?: boolean
}

/** `fs.search_files` — workspace paths ranked by the backend's fuzzy scorer. */
export interface FileSearchResult {
  /** Present when the listing itself failed; "could not look" ≠ "nothing here". */
  error?: string
  files?: string[]
  total?: number
  truncated?: boolean
}

export interface SessionRow {
  cwd?: string | null
  id: string
  is_active?: boolean
  /**
   * Epoch seconds OR an ISO string, depending on where the row came from —
   * `desktop_projects.py` says so outright ("Both may be epoch floats or ISO
   * strings (saved rows)") and coerces both. Typing this `string` is what made
   * `Date.parse` return NaN for every live row.
   */
  last_active?: number | string | null
  message_count?: number
  model?: string | null
  preview?: string
  source?: string
  started_at?: number | string | null
  title?: string
}

/**
 * `projects.tree` shape (`src/server/desktop_projects.py`): one project per
 * git repo root, each holding repos whose *groups* are the repo's checkouts —
 * the main lane plus one per linked worktree that has sessions in it. A valid
 * non-Git cwd is its own basename-labeled workspace project; only sessions
 * without a resolvable cwd land in the synthetic "Home" project.
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

/** One row in the workspace picker: a listing child or a breadcrumb ancestor. */
export interface DirectoryEntry {
  /** Hidden by the server platform's convention; the client decides to show it. */
  hidden: boolean
  /** Base name for the row; a root crumb carries its full path. */
  name: string
  /**
   * Absolute path, always from the server. Clients never join path segments
   * themselves — that is how a Windows path ends up built with the wrong
   * separator by a client that has never seen one.
   */
  path: string
}

/** `fs.list_directory` — one directory level plus its ancestry. */
export interface DirectoryListing {
  /** Root→target ancestor chain, inclusive; every crumb is a jump target. */
  crumbs: DirectoryEntry[]
  /** Direct child directories, name-sorted. */
  entries: DirectoryEntry[]
  /** The server account's home directory. */
  home: string
  path: string
  /** True when the listing was cut at the server's bound. */
  truncated: boolean
}

/* ── workspace files (the right sidebar's tree and text preview) ──────────── */

/**
 * Why a workspace read could not be answered.
 *
 * These arrive *in the result*, not as an RPC rejection: the gateway's error
 * envelope carries a message and nothing else, and the sidebar needs the code
 * to say why in terms of the file — "that file is gone" is a different sentence
 * from "that page is too large". Codes are the backend's `workspace-file/*`
 * vocabulary (`src/server/desktop_workspace_files.py`).
 */
export interface WorkspaceFileFailure {
  code: string
  details?: { limit?: number }
  message: string
}

/** Either the answer, or the reason there isn't one. */
export type WorkspaceFileResult<T> = ({ ok: true } & T) | { error: WorkspaceFileFailure; ok: false }

/** `fs.read_file` — one page of a text file's lines, from the 1-based `offset`. */
export interface FilePage {
  absolute_path: string
  /** The whole file's size, not the page's. */
  bytes: number
  /** Whether the file ends inside this page. */
  eof: boolean
  /**
   * Lines in this page. Zero means "past the end of the file"; one with an
   * empty `text` means one empty line — which is why the count is carried
   * rather than derived from the text.
   */
  lines: number
  offset: number
  /** The page's lines joined with `\n`, without a terminator. */
  text: string
  /** Changes whenever the file does; a page from another version is not merged. */
  version: string
}

/** One child of a listed directory. */
export interface WorkspaceEntry {
  name: string
  /** Files only. */
  size?: number
  type: 'directory' | 'file' | 'other'
}

/** `fs.list_dir` — one directory level under the session's workspace root. */
export interface WorkspaceLevel {
  absolute_path: string
  entries: WorkspaceEntry[]
  /** True when the level was cut at the backend's entry cap. */
  truncated: boolean
}

/** Approval answers the gateway understands (`approval.respond`). */
export type ApprovalChoice = 'allow' | 'deny' | 'session' | 'always'

// ── delegation control plane ────────────────────────────────────────────────

/** One live subagent in a `delegation.status` snapshot. */
export interface LiveAgent {
  /** Nesting level; 0 is a direct child of the root session. */
  depth?: number
  /** The task the agent was spawned for — its `description`, else its prompt. */
  goal?: string
  model?: null | string
  parent_id?: null | string
  /** Unix seconds, from the backend clock. */
  started_at?: number
  status?: string
  subagent_id?: string
  tool_count?: number
}

/**
 * `delegation.status` — the session's authoritative live-agent state.
 *
 * Every field is optional because a backend that predates the supervisor
 * answers with only `active`; the caps are absent rather than zero there, and a
 * client must not read a missing cap as "at capacity".
 */
export interface DelegationStatusResult {
  active?: LiveAgent[]
  max_concurrent_children?: number
  max_spawn_depth?: number
  paused?: boolean
}

/** `delegation.pause` — the resulting admission state, echoed back. */
export interface DelegationPauseResult {
  paused?: boolean
}

/** `subagent.interrupt` — `found` is false when the id was not live. */
export interface SubagentInterruptResult {
  found?: boolean
  subagent_id?: string
}
