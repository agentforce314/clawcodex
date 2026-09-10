/**
 * The session's subagents, as one list: every Agent call the transcript holds,
 * joined with whatever its run has reported so far.
 *
 * Two sources describe one delegation. The Agent tool row carries what was
 * asked (the prompt, the description) and, once settled, the report and the
 * totals the run counted; the `subagent.progress` frames carry what the run
 * is doing while it is still going. Neither is complete alone — a running row
 * has no result yet, and a resumed session has no frames — so the catalog is
 * folded from both, here, as a pure function of the transcript state. The
 * header's count, the dropdown and the child view all read this one list.
 *
 * Joining is by evidence, strongest first: the row's own result names the run
 * (`agent_id`); a frame names its row (`tool_use_id`); failing both, a running
 * frame that describes what the row described is taken to be it.
 */

import type { SubagentLive, SubagentStatus, ToolNode, TranscriptNode } from './transcript.ts'

/** Tool names that spawn a subagent — ClawCodex's `Agent`, Claude Code's `Task`. */
const AGENT_TOOLS = new Set(['Agent', 'Task'])

export function isAgentTool(name: string): boolean {
  return AGENT_TOOLS.has(name)
}

export interface SubagentEntry {
  /** What the run was last seen doing, while it runs. */
  activity?: string
  agentId?: string
  description?: string
  /** Milliseconds: the run's own total once settled, the elapsed time while live. */
  durationMs?: number
  /** Stable identity for rendering and for the child view: the row, else the run. */
  key: string
  /** The line the catalog shows: the description, the name, or the prompt's opening. */
  label: string
  model?: string
  name?: string
  prompt?: string
  /** The final report, once the row has one; the error text when it failed. */
  report?: string
  status: SubagentStatus
  subagentType?: string
  tokens?: number
  toolCount?: number
  toolId?: string
}

export interface SubagentCounts {
  running: number
  total: number
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function firstLine(text: string): string {
  const line = text.split('\n').find(candidate => candidate.trim() !== '')

  return line === undefined ? '' : line.trim()
}

function labelOf(description: string, name: string, prompt: string): string {
  return description || name || firstLine(prompt) || 'subagent'
}

/** How a settled row's reported status reads in the catalog's vocabulary. */
function statusFromResult(status: string, live: SubagentLive | undefined): SubagentStatus {
  switch (status) {
    case 'completed':
    case 'interrupted':
      return status

    case 'async_launched':
      // The call returned at launch; only a progress frame can say how the
      // run went, and a resumed session has none.
      return live?.status ?? 'background'

    default:
      return live?.status ?? 'completed'
  }
}

/**
 * The frame for a running row that names no run yet: one that points at the
 * row, else the first unclaimed running one describing the same task.
 */
function liveFor(
  node: ToolNode,
  agents: readonly SubagentLive[],
  claimed: Set<string>,
): SubagentLive | undefined {
  const byToolId = agents.find(agent => agent.toolUseId === node.toolId && !claimed.has(agent.agentId))

  if (byToolId !== undefined) return byToolId

  const description = str(node.args.description)
  const name = str(node.args.name)

  return agents.find(
    agent =>
      !claimed.has(agent.agentId) &&
      agent.status === 'running' &&
      agent.toolUseId === undefined &&
      ((description !== '' && agent.description === description) ||
        (name !== '' && agent.name === name)),
  )
}

function entryFromNode(
  node: ToolNode,
  agents: Record<string, SubagentLive>,
  claimed: Set<string>,
  now: number,
): SubagentEntry {
  const meta = node.result?.agent
  const live =
    meta !== undefined
      ? agents[meta.agent_id]
      : node.state === 'running'
        ? liveFor(node, Object.values(agents), claimed)
        : undefined

  if (live !== undefined) claimed.add(live.agentId)

  const description = str(node.args.description)
  const name = str(node.args.name)
  const prompt = str(node.args.prompt)
  const agentId = meta?.agent_id ?? live?.agentId

  let status: SubagentStatus

  if (node.state === 'error') status = 'failed'
  else if (node.state === 'running') status = live?.status ?? 'running'
  else status = statusFromResult(meta?.status ?? '', live)

  let durationMs: number | undefined

  if (meta?.duration_ms !== undefined) durationMs = meta.duration_ms
  else if (live !== undefined) durationMs = Math.max(0, (live.endedAt ?? now) - live.startedAt)
  else if (node.startedAt > 0) {
    durationMs = Math.max(0, (node.endedAt ?? (node.state === 'running' ? now : node.startedAt)) - node.startedAt)
  }

  const report =
    node.state === 'error'
      ? node.error
      : node.state === 'done'
        ? str(node.result?.output) || str(node.result?.context) || undefined
        : undefined

  const entry: SubagentEntry = {
    key: `tool:${node.toolId}`,
    label: labelOf(description || (live?.description ?? ''), name || (live?.name ?? ''), prompt),
    status,
    toolId: node.toolId,
  }

  if (agentId !== undefined) entry.agentId = agentId
  if (description !== '') entry.description = description
  if (name !== '') entry.name = name
  if (prompt !== '') entry.prompt = prompt
  if (report !== undefined && report !== '') entry.report = report

  const subagentType = str(node.args.subagent_type) || live?.subagentType || meta?.agent_type
  const model = meta?.model ?? live?.model ?? str(node.args.model)
  const toolCount = meta?.tool_count ?? live?.toolCount
  const tokens = meta?.tokens ?? live?.tokens

  if (subagentType !== undefined && subagentType !== '') entry.subagentType = subagentType
  if (model !== undefined && model !== '') entry.model = model
  if (toolCount !== undefined) entry.toolCount = toolCount
  if (tokens !== undefined) entry.tokens = tokens
  if (durationMs !== undefined) entry.durationMs = durationMs
  if (live?.activity !== undefined && status === 'running') entry.activity = live.activity

  return entry
}

/** A run the transcript has frames for but no row — kept, so nothing live goes unlisted. */
function entryFromLive(live: SubagentLive, now: number): SubagentEntry {
  const entry: SubagentEntry = {
    agentId: live.agentId,
    durationMs: Math.max(0, (live.endedAt ?? now) - live.startedAt),
    key: `agent:${live.agentId}`,
    label: labelOf(live.description ?? '', live.name ?? '', ''),
    status: live.status,
  }

  if (live.description !== undefined) entry.description = live.description
  if (live.name !== undefined) entry.name = live.name
  if (live.subagentType !== undefined) entry.subagentType = live.subagentType
  if (live.model !== undefined) entry.model = live.model
  if (live.toolCount !== undefined) entry.toolCount = live.toolCount
  if (live.tokens !== undefined) entry.tokens = live.tokens
  if (live.activity !== undefined && live.status === 'running') entry.activity = live.activity

  return entry
}

/**
 * Every subagent this session spawned, in the order it spawned them.
 *
 * `now` is taken as a parameter so a running entry's elapsed time is a pure
 * function of its inputs — and so a test can hold the clock still.
 */
export function subagentCatalog(
  nodes: readonly TranscriptNode[],
  agents: Record<string, SubagentLive>,
  now: number = Date.now(),
): SubagentEntry[] {
  const claimed = new Set<string>()
  const entries: SubagentEntry[] = []

  for (const node of nodes) {
    if (node.kind !== 'tool' || !isAgentTool(node.name)) continue

    entries.push(entryFromNode(node, agents, claimed, now))
  }

  for (const live of Object.values(agents)) {
    if (claimed.has(live.agentId)) continue

    entries.push(entryFromLive(live, now))
  }

  return entries
}

export function subagentCounts(entries: readonly SubagentEntry[]): SubagentCounts {
  let running = 0

  for (const entry of entries) {
    if (entry.status === 'running') running += 1
  }

  return { running, total: entries.length }
}

/** `3m 18s`, `42s`, `1h 02m` — a run's length at the width a row affords. */
export function formatRunDuration(ms: number): string {
  const seconds = Math.floor(Math.max(0, ms) / 1000)

  if (seconds < 60) return `${seconds}s`

  const minutes = Math.floor(seconds / 60)

  if (minutes < 60) return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`

  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}

/** The status word a row shows beside its dot. */
export function describeStatus(status: SubagentStatus): string {
  switch (status) {
    case 'running':
      return 'running'
    case 'completed':
      return 'done'
    case 'failed':
      return 'failed'
    case 'interrupted':
      return 'interrupted'
    case 'killed':
      return 'stopped'
    case 'background':
      return 'in background'
  }
}
