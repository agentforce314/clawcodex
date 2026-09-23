/**
 * The transcript model: gateway events in, renderable nodes out.
 *
 * This is the whole adaptation layer between the ClawCodex agent protocol and
 * the conversation UI, and it is deliberately pure — every rule about what the
 * reader sees (when a bubble seals, when a tool row opens, which text is a
 * duplicate) is a function of (state, event) and nothing else, so it can be
 * unit-tested without a socket or a DOM.
 *
 * The one rule worth stating up front, because every branch below serves it:
 * **streamed text wins**. `message.interim` and `message.complete` both carry
 * a fully-rendered copy of text that already arrived as `message.delta`s. If a
 * turn streamed anything, those copies are duplicates and are dropped; they
 * are only used to materialise a bubble for a turn that streamed nothing (a
 * non-streaming provider, or a reply short enough to arrive whole).
 */

import type {
  ApprovalRequestPayload,
  GatewayEvent,
  MessageCompletePayload,
  MessageDeltaPayload,
  QuestionRequestPayload,
  SessionInfoPayload,
  SubagentProgressPayload,
  ToolCompletePayload,
  ToolResult,
  ToolStartPayload,
  UsagePayload,
} from '../gateway/protocol.ts'
import { agentResultMeta, renderToolName, renderToolResult } from '../gateway/tool-vocabulary.ts'

export type ToolState = 'running' | 'done' | 'error'

export interface UserImage {
  name: string
  /** The prompt marker, when known; bytes are independent of composer Blob URLs. */
  placeholder?: string
  url: string
}

/** A file attached to a prompt: what its card in the user row shows. */
export interface UserFile {
  name: string
  /** Where the backend keeps it, once a stored message says so. */
  path?: string
  /** The prompt marker, `[File #N]`, when known. */
  placeholder?: string
  size?: number
}

export interface UserNode {
  at: number
  files?: UserFile[]
  id: string
  images?: UserImage[]
  kind: 'user'
  text: string
}

export interface AssistantNode {
  id: string
  kind: 'assistant'
  sealed: boolean
  text: string
}

export interface ReasoningNode {
  id: string
  kind: 'reasoning'
  sealed: boolean
  text: string
}

export interface ToolNode {
  args: Record<string, unknown>
  context?: string
  endedAt?: number
  error?: string
  id: string
  kind: 'tool'
  name: string
  result?: ToolResult
  startedAt: number
  state: ToolState
  toolId: string
}

export interface NoticeNode {
  body?: string
  id: string
  kind: 'notice'
  title: string
  tone: 'error' | 'info' | 'warn'
}

export type TranscriptNode = AssistantNode | NoticeNode | ReasoningNode | ToolNode | UserNode

/**
 * How a subagent run stands. `running` until its terminal frame; the rest are
 * the backend's own words for how it stopped. `background` is a run launched
 * to finish on its own, whose outcome this transcript never hears.
 */
export type SubagentStatus =
  | 'background'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | 'killed'
  | 'running'

/**
 * One subagent as its progress frames describe it — the live half of the
 * catalog the header counts. The Agent row that spawned it holds the other
 * half (the prompt, the report); `toolUseId` is how the two are joined
 * before the row's result names the run.
 */
export interface SubagentLive {
  /** What the agent was last seen doing — a tool name or its description. */
  activity?: string
  agentId: string
  depth?: number
  description?: string
  endedAt?: number
  model?: string
  name?: string
  startedAt: number
  status: SubagentStatus
  subagentType?: string
  tokens?: number
  toolCount?: number
  toolUseId?: string
}

export interface TranscriptState {
  /** Live subagents by agent id, folded from `subagent.progress`. */
  agents: Record<string, SubagentLive>
  approval?: ApprovalRequestPayload
  info: SessionInfoPayload
  nodes: TranscriptNode[]
  question?: QuestionRequestPayload
  running: boolean
  /** Wall-clock start of the running turn, for the elapsed clock. */
  turnStartedAt?: number
  /** True once the running turn has produced any assistant prose. */
  turnStreamedText: boolean
  usage?: UsagePayload
}

export function emptyTranscript(): TranscriptState {
  return { agents: {}, info: {}, nodes: [], running: false, turnStreamedText: false }
}

const TERMINAL_STATUSES = new Set<SubagentStatus>(['completed', 'failed', 'interrupted', 'killed'])

/** The backend's status word as one of ours; anything unexpected is still running. */
function asSubagentStatus(value: unknown): SubagentStatus {
  return typeof value === 'string' && TERMINAL_STATUSES.has(value as SubagentStatus)
    ? (value as SubagentStatus)
    : 'running'
}

/** Fold one progress frame into the live-agent map. */
function applySubagentProgress(
  agents: Record<string, SubagentLive>,
  payload: SubagentProgressPayload,
  at: number,
): Record<string, SubagentLive> {
  const id = payload.agent_id
  const previous = agents[id]
  const status = asSubagentStatus(payload.status)
  const next: SubagentLive = {
    ...previous,
    agentId: id,
    startedAt: previous?.startedAt ?? at,
    status,
  }

  // A frame names only what changed; the terminal one, for instance, carries
  // no activity or counts, and must not blank the ones the last frame set.
  if (typeof payload.tool_use_id === 'string' && payload.tool_use_id !== '') {
    next.toolUseId = payload.tool_use_id
  }
  if (typeof payload.description === 'string' && payload.description !== '') {
    next.description = payload.description
  }
  if (typeof payload.name === 'string' && payload.name !== '') next.name = payload.name
  if (typeof payload.subagent_type === 'string' && payload.subagent_type !== '') {
    next.subagentType = payload.subagent_type
  }
  if (typeof payload.model === 'string' && payload.model !== '') next.model = payload.model
  if (typeof payload.activity === 'string' && payload.activity !== '') {
    next.activity = payload.activity
  }
  if (typeof payload.depth === 'number') next.depth = payload.depth
  if (typeof payload.tool_count === 'number') next.toolCount = payload.tool_count
  if (typeof payload.tokens === 'number') next.tokens = payload.tokens
  if (status !== 'running') next.endedAt = previous?.endedAt ?? at

  return { ...agents, [id]: next }
}

let idCounter = 0

/**
 * Node ids are render keys, not identity: they only have to be unique and
 * stable for the life of a mounted list. A monotonic counter is both, and is
 * reproducible in tests (unlike a random or clock-derived id).
 */
function nextId(prefix: string): string {
  idCounter += 1

  return `${prefix}-${idCounter}`
}

/** Test seam: reset the id counter so snapshots stay stable across cases. */
export function resetNodeIds(): void {
  idCounter = 0
}

function lastNode(nodes: TranscriptNode[]): TranscriptNode | undefined {
  return nodes[nodes.length - 1]
}

/** The open (unsealed) trailing node of `kind`, if the flow currently has one. */
function openNode(
  nodes: TranscriptNode[],
  kind: 'assistant' | 'reasoning',
): AssistantNode | ReasoningNode | undefined {
  const last = lastNode(nodes)

  if (last === undefined || last.kind !== kind) return undefined

  return last.sealed ? undefined : last
}

/** Seal every open prose node so whatever comes next starts its own block. */
function sealOpen(nodes: TranscriptNode[]): TranscriptNode[] {
  const last = lastNode(nodes)

  if (last === undefined) return nodes
  if (last.kind !== 'assistant' && last.kind !== 'reasoning') return nodes
  if (last.sealed) return nodes

  // An empty trailing bubble is an artifact of ordering (a turn that opened a
  // block and then went straight to a tool call), not content: drop it rather
  // than paint an empty bubble.
  if (last.text === '') return nodes.slice(0, -1)

  return [...nodes.slice(0, -1), { ...last, sealed: true }]
}

function appendText(
  nodes: TranscriptNode[],
  kind: 'assistant' | 'reasoning',
  text: string,
): TranscriptNode[] {
  const open = openNode(nodes, kind)

  if (open !== undefined) {
    return [...nodes.slice(0, -1), { ...open, text: open.text + text }]
  }

  // Reasoning and prose alternate within a turn (a model can think, answer,
  // then think again), so opening a block seals whichever other one was open.
  const base = sealOpen(nodes)

  return [
    ...base,
    kind === 'assistant'
      ? { id: nextId('assistant'), kind: 'assistant', sealed: false, text }
      : { id: nextId('reasoning'), kind: 'reasoning', sealed: false, text },
  ]
}

/** Text this turn already showed the reader — used to drop replayed copies. */
function turnHasProse(state: TranscriptState): boolean {
  return state.turnStreamedText
}

function mergeUsage(current: UsagePayload | undefined, incoming: UsagePayload): UsagePayload {
  if (current === undefined) return incoming

  return {
    calls: current.calls + incoming.calls,
    input: current.input + incoming.input,
    output: current.output + incoming.output,
    total: current.total + incoming.total,
  }
}

/** Append a user bubble — a local action, not a gateway event. */
export function appendUserMessage(
  state: TranscriptState,
  text: string,
  images: UserImage[] = [],
  files: UserFile[] = [],
): TranscriptState {
  return {
    ...state,
    nodes: [
      ...sealOpen(state.nodes),
      {
        at: Date.now(),
        id: nextId('user'),
        kind: 'user',
        text,
        ...(images.length > 0 && { images }),
        ...(files.length > 0 && { files }),
      },
    ],
  }
}

/** Mark the turn as started locally, so the composer locks before the ack. */
export function markTurnStarted(state: TranscriptState): TranscriptState {
  return { ...state, running: true, turnStartedAt: Date.now(), turnStreamedText: false }
}

/**
 * The subject a task id was created under, recovered from the transcript.
 *
 * A TaskUpdate names only `taskId`; the human-readable subject lives on the
 * TaskCreate that minted the id (its arguments) and the id itself in that
 * call's result JSON. Scanning the existing rows keeps this a pure function
 * of the nodes — no side registry to keep in step across live and rehydrated
 * paths.
 */
function taskSubjectFor(nodes: TranscriptNode[], taskId: string): string | undefined {
  if (taskId === '') return undefined

  for (const node of nodes) {
    if (node.kind !== 'tool' || node.name !== 'TaskCreate') continue

    const subject = typeof node.args.subject === 'string' ? node.args.subject : ''

    if (subject === '') continue

    const output = node.result?.output ?? node.result?.context ?? ''

    // The created id appears verbatim in the result JSON; a substring check
    // is enough for the token-ish ids the task registry mints.
    if (typeof output === 'string' && output.includes(taskId)) return subject
  }

  return undefined
}

/** Stamp a task row's context with its subject, so the row can say what the
    update touched instead of which hex id it touched. */
function enrichTaskContext(nodes: TranscriptNode[], node: ToolNode): ToolNode {
  if (node.name !== 'TaskUpdate' && node.name !== 'TaskView' && node.name !== 'TaskGet') {
    return node
  }
  if (node.context !== undefined && node.context !== '') return node

  const taskId = typeof node.args.taskId === 'string' ? node.args.taskId : ''
  const subject = taskSubjectFor(nodes, taskId)

  return subject === undefined ? node : { ...node, context: subject }
}

function completeTool(
  nodes: TranscriptNode[],
  payload: ToolCompletePayload,
): TranscriptNode[] {
  let matched = false

  const next = nodes.map(node => {
    if (matched || node.kind !== 'tool' || node.toolId !== payload.tool_id) return node

    matched = true

    return enrichTaskContext(nodes, {
      ...node,
      endedAt: Date.now(),
      error: payload.error,
      name: payload.name && payload.name !== '' ? payload.name : node.name,
      result: payload.result,
      state: payload.error === undefined ? ('done' as const) : ('error' as const),
    })
  })

  if (matched) return next

  // A completion with no running row: the socket connected mid-tool (a reload
  // during a turn). Showing the resolved row is strictly better than dropping
  // the tool call entirely.
  return [
    ...sealOpen(nodes),
    {
      args: {},
      endedAt: Date.now(),
      error: payload.error,
      id: nextId('tool'),
      kind: 'tool',
      name: payload.name ?? 'tool',
      result: payload.result,
      startedAt: Date.now(),
      state: payload.error === undefined ? 'done' : 'error',
      toolId: payload.tool_id,
    },
  ]
}

/**
 * Fold one gateway push into the transcript. Unknown event types return the
 * same state object, so a `sessions.changed` (handled elsewhere) costs nothing.
 */
export function applyEvent(state: TranscriptState, event: GatewayEvent): TranscriptState {
  switch (event.type) {
    case 'session.info': {
      const payload = (event.payload ?? {}) as SessionInfoPayload

      // `running` is stamped `false` on EVERY session.info the backend
      // publishes (turn end, settings republish, model switch), so reading it
      // would clear a turn the composer had just started. Turn state comes
      // from message.start / message.complete alone.
      return { ...state, info: { ...state.info, ...payload } }
    }

    case 'message.start':
      return {
        ...state,
        nodes: sealOpen(state.nodes),
        running: true,
        turnStartedAt: state.turnStartedAt ?? Date.now(),
        turnStreamedText: false,
      }

    case 'message.delta': {
      const text = (event.payload as MessageDeltaPayload | undefined)?.text ?? ''

      if (text === '') return state

      return {
        ...state,
        nodes: appendText(state.nodes, 'assistant', text),
        running: true,
        turnStreamedText: true,
      }
    }

    case 'reasoning.delta':
    case 'thinking.delta': {
      const text = (event.payload as MessageDeltaPayload | undefined)?.text ?? ''

      if (text === '') return state

      return { ...state, nodes: appendText(state.nodes, 'reasoning', text), running: true }
    }

    case 'message.interim': {
      const text = (event.payload as MessageDeltaPayload | undefined)?.text ?? ''

      // Streamed text wins: this frame is the same prose, rendered. Seal what
      // streamed and move on.
      if (turnHasProse(state)) return { ...state, nodes: sealOpen(state.nodes) }
      if (text === '') return state

      return {
        ...state,
        nodes: [
          ...sealOpen(state.nodes),
          { id: nextId('assistant'), kind: 'assistant', sealed: true, text },
        ],
        turnStreamedText: true,
      }
    }

    case 'tool.start': {
      const payload = event.payload as ToolStartPayload | undefined

      if (payload === undefined) return state

      return {
        ...state,
        nodes: [
          ...sealOpen(state.nodes),
          {
            args: payload.args ?? {},
            context: payload.context,
            id: nextId('tool'),
            kind: 'tool',
            name: payload.name,
            startedAt: Date.now(),
            state: 'running',
            toolId: payload.tool_id,
          },
        ],
        running: true,
      }
    }

    case 'tool.complete': {
      const payload = event.payload as ToolCompletePayload | undefined

      if (payload === undefined) return state

      return { ...state, nodes: completeTool(state.nodes, payload) }
    }

    case 'message.complete': {
      const payload = (event.payload ?? {}) as MessageCompletePayload
      let nodes = state.nodes

      if (!turnHasProse(state) && payload.text !== undefined && payload.text !== '') {
        nodes = [
          ...sealOpen(nodes),
          { id: nextId('assistant'), kind: 'assistant', sealed: true, text: payload.text },
        ]
      } else {
        nodes = sealOpen(nodes)
      }

      if (payload.status === 'error') {
        // A turn-fatal error is often replayed as interim prose before the
        // result frame arrives, leaving the same words in a bubble AND in
        // this notice. One row carrying the failure is enough.
        const errorText = (payload.error ?? '').trim()
        const last = nodes[nodes.length - 1]

        if (
          errorText !== '' &&
          last !== undefined &&
          last.kind === 'assistant' &&
          last.text.trim() === errorText
        ) {
          nodes = nodes.slice(0, -1)
        }

        nodes = [
          ...nodes,
          {
            body: payload.error ?? 'The turn ended with an error.',
            id: nextId('notice'),
            kind: 'notice',
            title: 'Turn failed',
            tone: 'error',
          },
        ]
      }

      // Any still-running tool row belongs to the turn that just ended; leaving
      // it spinning forever is the one state the reader can never resolve.
      nodes = nodes.map(node =>
        node.kind === 'tool' && node.state === 'running'
          ? { ...node, endedAt: Date.now(), error: 'Interrupted', state: 'error' as const }
          : node,
      )

      return {
        ...state,
        nodes,
        running: false,
        turnStartedAt: undefined,
        turnStreamedText: false,
        usage: payload.usage === undefined ? state.usage : mergeUsage(state.usage, payload.usage),
      }
    }

    case 'subagent.progress': {
      const payload = event.payload as SubagentProgressPayload | undefined

      if (payload === undefined || typeof payload.agent_id !== 'string' || payload.agent_id === '') {
        return state
      }

      return { ...state, agents: applySubagentProgress(state.agents, payload, Date.now()) }
    }

    case 'approval.request':
      return { ...state, approval: (event.payload ?? {}) as ApprovalRequestPayload }

    case 'question.request': {
      const payload = event.payload as QuestionRequestPayload | undefined

      // A question set with nothing in it has no composer to show and no
      // answer to give; ignoring it leaves the normal input in place rather
      // than seating an empty takeover the user cannot get out of.
      if (payload === undefined || payload.questions.length === 0) return state

      return { ...state, question: payload }
    }

    case 'error': {
      const payload = event.payload as { message?: string } | undefined

      return {
        ...state,
        nodes: [
          ...sealOpen(state.nodes),
          {
            body: payload?.message ?? 'The backend reported an error.',
            id: nextId('notice'),
            kind: 'notice',
            title: 'Error',
            tone: 'error',
          },
        ],
        running: false,
      }
    }

    default:
      return state
  }
}

/** Clear the pending approval after the user answered it. */
export function clearApproval(state: TranscriptState): TranscriptState {
  if (state.approval === undefined) return state

  const next = { ...state }
  delete next.approval

  return next
}

/**
 * Trim the transcript back to just before the last user prompt, and hand back
 * the prompt that was removed.
 *
 * The mirror of the agent's `rewind` control, which drops whole prompt-turns:
 * the client has to cut at the same boundary or the two disagree about what
 * the conversation contains. A turn starts at a user message and runs to the
 * end, so everything from that node onward goes — the assistant's reply, its
 * reasoning, and every tool row it produced.
 *
 * Returns `null` when there is no prompt to rewind to, which is the honest
 * answer for a transcript holding only a replayed session or a notice.
 */
export function rewindLastTurn(
  state: TranscriptState,
): { prompt: string; state: TranscriptState } | null {
  for (let index = state.nodes.length - 1; index >= 0; index -= 1) {
    const node = state.nodes[index]

    if (node?.kind !== 'user') continue

    return {
      prompt: node.text,
      state: { ...state, nodes: state.nodes.slice(0, index) },
    }
  }

  return null
}

/** Clear the pending question after the user answered or declined it. */
export function clearQuestion(state: TranscriptState): TranscriptState {
  if (state.question === undefined) return state

  const next = { ...state }
  delete next.question

  return next
}

/* ── rehydration ─────────────────────────────────────────────────────────── */

interface StoredBlock {
  content?: unknown
  id?: string
  input?: Record<string, unknown>
  is_error?: boolean
  name?: string
  source?: { data?: unknown; media_type?: unknown; type?: string; url?: unknown }
  text?: string
  thinking?: string
  tool_use_id?: string
  type?: string
}

/** Image blocks are already saved with the conversation, including their bytes. */
function storedUserImages(blocks: StoredBlock[], text: string): UserImage[] {
  const placeholders = [...new Set(text.match(/\[Image #\d+\]/g) ?? [])]
  let imageIndex = 0
  const images: UserImage[] = []

  for (const block of blocks) {
    if (block?.type !== 'image') continue

    const placeholder = placeholders[imageIndex]
    imageIndex += 1
    const source = block.source
    let url: string | undefined

    if (
      source?.type === 'base64' && typeof source.data === 'string' && source.data !== '' &&
      typeof source.media_type === 'string' && /^image\/[\w.+-]+$/.test(source.media_type)
    ) {
      url = `data:${source.media_type};base64,${source.data}`
    } else if (
      source?.type === 'url' && typeof source.url === 'string' && /^https?:\/\//i.test(source.url)
    ) {
      url = source.url
    }

    if (url !== undefined) {
      images.push({ name: placeholder?.slice(1, -1) ?? `Attached image ${imageIndex}`, placeholder, url })
    }
  }

  return images
}

/**
 * The header line the agent writes above an attached file's contents:
 * `[File #N: name] saved at <path> (<size>)`. The block it opens is the
 * backend's, not the user's words, so it is hidden from the caption and
 * turned back into the card the composer showed.
 */
const STORED_FILE_HEADER = /^\[File #(\d+): ([^\n]+?)\] (?:saved )?at ([^\n]+?) \(([\d.]+ [KM]?B)\)(?:\n|$)/

/**
 * Whether the block at `index` is an attached file's block rather than prose.
 *
 * The agent appends file blocks AFTER the user's text, so the first text
 * block is never one: a user who happens to type a header-shaped line keeps
 * their own words on screen.
 */
function isStoredFileBlock(block: StoredBlock, index: number, firstText: number): boolean {
  return (
    index > firstText &&
    block?.type === 'text' &&
    typeof block.text === 'string' &&
    STORED_FILE_HEADER.test(block.text)
  )
}

/** The index of the user's own text block: the first text block, if any. */
function firstTextBlock(blocks: StoredBlock[]): number {
  return blocks.findIndex(block => block?.type === 'text' && typeof block.text === 'string')
}

/** The files a stored user message carried, from the agent's header lines. */
function storedUserFiles(blocks: StoredBlock[]): UserFile[] {
  const files: UserFile[] = []
  const firstText = firstTextBlock(blocks)

  for (const [index, block] of blocks.entries()) {
    if (!isStoredFileBlock(block, index, firstText)) continue
    if (block?.type !== 'text' || typeof block.text !== 'string') continue

    const match = STORED_FILE_HEADER.exec(block.text)

    if (match === null) continue

    const [, id, name, path, size] = match
    const bytes = parseStoredSize(size ?? '')

    files.push({
      name: name ?? 'file',
      path,
      placeholder: `[File #${id ?? ''}]`,
      ...(bytes !== undefined && { size: bytes }),
    })
  }

  return files
}

/** `12.3 KB` back to bytes, well enough to render the same label. */
function parseStoredSize(label: string): number | undefined {
  const match = /^([\d.]+) ([KM]?B)$/.exec(label)

  if (match === null) return undefined

  const amount = Number(match[1])
  const unit = match[2]

  if (Number.isNaN(amount)) return undefined

  return Math.round(amount * (unit === 'MB' ? 1024 * 1024 : unit === 'KB' ? 1024 : 1))
}

function blockText(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''

  return content
    .map(block => {
      if (typeof block === 'string') return block
      if (block === null || typeof block !== 'object') return ''

      const typed = block as StoredBlock

      return typed.type === 'text' && typeof typed.text === 'string' ? typed.text : ''
    })
    .join('')
}

/**
 * Stored transcript (`session.resume` → `messages`) → nodes.
 *
 * The saved shape is the raw conversation, not the gateway's event vocabulary:
 * assistant `tool_use` blocks and the user `tool_result` blocks that answer
 * them are two separate messages, so tool rows are opened on the first and
 * resolved on the second, exactly as the live stream does it.
 */
export function hydrateStoredMessages(
  messages: readonly {
    content?: unknown
    display_kind?: string
    role?: string
    tool_use_result?: unknown
  }[],
): TranscriptNode[] {
  let nodes: TranscriptNode[] = []
  const toolNames = new Map<string, string>()

  for (const message of messages) {
    if (message.display_kind === 'hidden') continue

    const role = message.role ?? 'user'
    const content = message.content
    const blocks: StoredBlock[] = Array.isArray(content) ? (content as StoredBlock[]) : []

    if (role === 'user') {
      const results = blocks.filter(block => block?.type === 'tool_result')

      if (results.length > 0) {
        // The persisted envelope rides the message, not the block; a stored
        // tool-result message holds one block, so it names the same call.
        const agent = agentResultMeta(message.tool_use_result)

        for (const block of results) {
          const toolId = String(block.tool_use_id ?? '')
          const text = blockText(block.content)
          const rawName = toolNames.get(toolId) ?? ''

          nodes = completeTool(nodes, {
            error: block.is_error === true ? text : undefined,
            name: rawName === '' ? undefined : renderToolName(rawName),
            result:
              block.is_error === true
                ? {}
                : { ...renderToolResult(rawName, text), ...(agent !== undefined && { agent }) },
            tool_id: toolId,
          })
        }

        continue
      }

      const images = storedUserImages(blocks, blockText(content))
      const files = storedUserFiles(blocks)
      // The backend appends coordinate/source metadata, and each attached
      // file's block, as separate text blocks. They guide the model; they are
      // not part of the user's caption.
      const firstText = firstTextBlock(blocks)
      const text = blockText(
        images.length === 0 && files.length === 0
          ? content
          : blocks.filter(
              (block, index) =>
                !isStoredFileBlock(block, index, firstText) &&
                !(
                  block?.type === 'text' &&
                  typeof block.text === 'string' &&
                  /^\[Image(?:: (?:source:|original \d+x\d+)| source:)[\s\S]*\]$/.test(block.text)
                ),
            ),
      )

      if (text.trim() === '' && images.length === 0 && files.length === 0) continue

      nodes = [
        ...sealOpen(nodes),
        {
          at: 0,
          id: nextId('user'),
          kind: 'user',
          text,
          ...(images.length > 0 && { images }),
          ...(files.length > 0 && { files }),
        },
      ]
      continue
    }

    if (role !== 'assistant') continue

    const thinking = blocks
      .filter(block => block?.type === 'thinking' && typeof block.thinking === 'string')
      .map(block => block.thinking ?? '')
      .join('')

    if (thinking !== '') {
      nodes = [
        ...sealOpen(nodes),
        { id: nextId('reasoning'), kind: 'reasoning', sealed: true, text: thinking },
      ]
    }

    const text = blockText(content)

    if (text.trim() !== '') {
      nodes = [
        ...sealOpen(nodes),
        { id: nextId('assistant'), kind: 'assistant', sealed: true, text },
      ]
    }

    for (const block of blocks) {
      if (block?.type !== 'tool_use') continue

      const toolId = String(block.id ?? '')
      const name = String(block.name ?? 'tool')
      toolNames.set(toolId, name)

      nodes = [
        ...sealOpen(nodes),
        {
          args: block.input ?? {},
          id: nextId('tool'),
          kind: 'tool',
          name: renderToolName(name),
          startedAt: 0,
          state: 'running',
          toolId,
        },
      ]
    }
  }

  // A tool whose result never made it into the saved file (the session ended
  // mid-call) stays unresolved rather than spinning forever.
  return nodes.map(node =>
    node.kind === 'tool' && node.state === 'running'
      ? { ...node, error: 'No result recorded', state: 'error' as const }
      : node,
  )
}
