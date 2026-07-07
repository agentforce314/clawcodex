export interface ActiveTool {
  context?: string
  id: string
  name: string
  verboseArgs?: string
  startedAt?: number
}

export interface TodoItem {
  // Present-continuous label ("Fixing the parser…") — the busy line shows the
  // in-progress todo's activeForm as its verb (original Spinner.tsx).
  activeForm?: string
  content: string
  id: string
  status: 'cancelled' | 'completed' | 'in_progress' | 'pending'
}

export interface ActivityItem {
  id: number
  text: string
  tone: 'error' | 'info' | 'warn'
}

export type SubagentStatus = 'completed' | 'error' | 'failed' | 'interrupted' | 'queued' | 'running' | 'timeout'

export interface SubagentProgress {
  apiCalls?: number
  costUsd?: number
  depth: number
  durationSeconds?: number
  filesRead?: string[]
  filesWritten?: string[]
  goal: string
  id: string
  index: number
  inputTokens?: number
  iteration?: number
  model?: string
  notes: string[]
  outputTail?: SubagentOutputEntry[]
  outputTokens?: number
  parentId: null | string
  reasoningTokens?: number
  startedAt?: number
  status: SubagentStatus
  summary?: string
  taskCount: number
  thinking: string[]
  toolCount: number
  tools: string[]
  toolsets?: string[]
}

export interface SubagentOutputEntry {
  isError: boolean
  preview: string
  tool: string
}

export interface SubagentNode {
  aggregate: SubagentAggregate
  children: SubagentNode[]
  item: SubagentProgress
}

export interface SubagentAggregate {
  activeCount: number
  costUsd: number
  descendantCount: number
  filesTouched: number
  hotness: number
  inputTokens: number
  maxDepthFromHere: number
  outputTokens: number
  totalDuration: number
  totalTools: number
}

export interface DelegationStatus {
  active: {
    depth?: number
    goal?: string
    model?: null | string
    parent_id?: null | string
    started_at?: number
    status?: string
    subagent_id?: string
    tool_count?: number
  }[]
  max_concurrent_children?: number
  max_spawn_depth?: number
  paused: boolean
}

export interface ApprovalReq {
  // false when the backend won't honor a permanent allow (tirith warning) → hide "don't ask again".
  allowPermanent?: boolean
  // The actual command / action under review (e.g. the Bash command line), NOT
  // the tool name or a JSON dump — this is what the box shows.
  command: string
  // Tool name for the box title ("Bash", "Write", …).
  toolName: string
  // The grant rule content the "don't ask again" option would persist, e.g.
  // "git status:*". Editable in the box so the user can widen it (git:*).
  // undefined when the backend sent no suggestion (→ no persist option).
  rule?: string
  // Display form of the rule for the option label, e.g. "Bash(git status:*)".
  ruleLabel?: string
  // Authoritative per-tool wording for the persist option, rendered as
  // "Yes, <sessionLabel>" (e.g. "Yes, allow all edits during this session").
  // Used for non-Bash tools; Bash uses the editable rule instead.
  sessionLabel?: string
  // Destructive-command caution from the backend (e.g. "Note: may overwrite
  // remote history"), rendered as a warning line above the options.
  warning?: string
}

export interface ConfirmReq {
  cancelLabel?: string
  confirmLabel?: string
  danger?: boolean
  detail?: string
  onConfirm: () => void
  title: string
}

export interface ClarifyReq {
  choices: string[] | null
  question: string
  requestId: string
}

/** Exit-time keep/remove dialog for a --worktree session (the TS reference's
 *  WorktreeExitDialog). `phase` flips to keeping/removing while the backend
 *  RPC runs — removal of a large tree can take a while. */
export interface WorktreeExitReq {
  branch: string
  /** Esc — abort the exit and return to the session. */
  onCancel: () => void
  onChoose: (action: 'keep' | 'remove') => void
  /** Ctrl+C/Ctrl+D during the busy phase — die now without waiting for the
   *  backend RPC (worktree left in place). Escape hatch for a hung git. */
  onForceQuit: () => void
  path: string
  phase: 'asking' | 'keeping' | 'removing'
  /** Remove is rendered as destructive when changes would be lost. */
  removeIsDanger: boolean
  subtitle: string
}

export interface Msg {
  // Structured Edit/Write patch for kind:'diff' segments — rendered by
  // DiffView (line numbers, word diff, ColorDiff). Text-only diff segments
  // (legacy backends) fall back to the markdown ```diff path.
  diffData?: MsgDiffData
  info?: SessionInfo
  kind?: 'diff' | 'intro' | 'panel' | 'slash' | 'trail'
  panelData?: PanelData
  role: Role
  text: string
  thinking?: string
  thinkingTokens?: number
  toolTokens?: number
  tools?: string[]
  // Verbose siblings for `tools`, lockstep by index ('' = no verbose form).
  // Rendered instead of the compact line when tool details are expanded
  // (ctrl+o / /details expanded). Absent on resumed/legacy messages.
  toolsVerbose?: string[]
  todos?: TodoItem[]
  todoIncomplete?: boolean
  todoCollapsedByDefault?: boolean
}

/**
 * Display data for a structured diff segment. Same shape as the gateway's
 * StructuredDiffPayload plus the ingestion-time truncation bookkeeping
 * (hunks are capped once when the segment is built so the render cache can
 * key on stable hunk objects).
 */
export interface MsgDiffData {
  content?: string
  filePath: string
  firstLine?: null | string
  hunks: Array<{ lines: string[]; newLines: number; newStart: number; oldLines: number; oldStart: number }>
  kind: 'create' | 'update'
  /** Hunk lines dropped by the ingestion cap (renders as "… +N lines"). */
  truncatedLines?: number
}

export type Role = 'assistant' | 'system' | 'tool' | 'user'
export type DetailsMode = 'hidden' | 'collapsed' | 'expanded'
export type ThinkingMode = 'collapsed' | 'truncated' | 'full'

// Per-section overrides for the agent details accordion.  Resolution order
// at lookup time is: explicit `display.sections.<name>` → built-in
// SECTION_DEFAULTS → global `details_mode`.  Today the built-in defaults
// expand `thinking`/`tools` and hide `activity`; `subagents` falls through
// to the global mode.  Any explicit value still wins for that one section.
export type SectionName = 'thinking' | 'tools' | 'subagents' | 'activity'
export type SectionVisibility = Partial<Record<SectionName, DetailsMode>>

export interface McpServerStatus {
  connected: boolean
  disabled?: boolean
  status?: 'configured' | 'connecting' | 'connected' | 'disabled' | 'failed'
  name: string
  tools: number
  transport: string
}

export interface SessionInfo {
  cwd?: string
  fast?: boolean
  lazy?: boolean
  mcp_servers?: McpServerStatus[]
  model: string
  permission_mode?: string
  profile_name?: string
  reasoning_effort?: string
  release_date?: string
  service_tier?: string
  skills: Record<string, string[]>
  system_prompt?: string
  tools: Record<string, string[]>
  update_behind?: number | null
  update_command?: string
  usage?: Usage
  version?: string
}

export interface Usage {
  active_subagents?: number
  calls: number
  compressions?: number
  context_max?: number
  context_percent?: number
  context_used?: number
  cost_status?: string
  cost_usd?: number
  dev_credits_spent_micros?: number
  input: number
  output: number
  reasoning?: number
  total: number
}

export interface SudoReq {
  requestId: string
}

export interface SecretReq {
  envVar: string
  prompt: string
  requestId: string
}

export interface PanelData {
  sections: PanelSection[]
  title: string
}

export interface PanelSection {
  items?: string[]
  rows?: [string, string][]
  text?: string
  title?: string
}

export interface SlashCatalog {
  canon: Record<string, string>
  categories: SlashCategory[]
  /** Argument hints by canonical `/name` (gateway + workflow commands). */
  hints: Record<string, string>
  pairs: [string, string][]
  skillCount: number
  sub: Record<string, string[]>
}

export interface SlashCategory {
  name: string
  pairs: [string, string][]
}
