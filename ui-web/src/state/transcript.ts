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
  ToolCompletePayload,
  ToolResult,
  ToolStartPayload,
  UsagePayload,
} from '../gateway/protocol.ts'
import { renderToolName, renderToolResult } from '../gateway/tool-vocabulary.ts'

export type ToolState = 'running' | 'done' | 'error'

export interface UserNode {
  at: number
  id: string
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

export interface TranscriptState {
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
  return { info: {}, nodes: [], running: false, turnStreamedText: false }
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
export function appendUserMessage(state: TranscriptState, text: string): TranscriptState {
  return {
    ...state,
    nodes: [
      ...sealOpen(state.nodes),
      { at: Date.now(), id: nextId('user'), kind: 'user', text },
    ],
  }
}

/** Mark the turn as started locally, so the composer locks before the ack. */
export function markTurnStarted(state: TranscriptState): TranscriptState {
  return { ...state, running: true, turnStartedAt: Date.now(), turnStreamedText: false }
}

function completeTool(
  nodes: TranscriptNode[],
  payload: ToolCompletePayload,
): TranscriptNode[] {
  let matched = false

  const next = nodes.map(node => {
    if (matched || node.kind !== 'tool' || node.toolId !== payload.tool_id) return node

    matched = true

    return {
      ...node,
      endedAt: Date.now(),
      error: payload.error,
      name: payload.name && payload.name !== '' ? payload.name : node.name,
      result: payload.result,
      state: payload.error === undefined ? ('done' as const) : ('error' as const),
    }
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
  text?: string
  thinking?: string
  tool_use_id?: string
  type?: string
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
  messages: readonly { content?: unknown; display_kind?: string; role?: string }[],
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
        for (const block of results) {
          const toolId = String(block.tool_use_id ?? '')
          const text = blockText(block.content)
          const rawName = toolNames.get(toolId) ?? ''

          nodes = completeTool(nodes, {
            error: block.is_error === true ? text : undefined,
            name: rawName === '' ? undefined : renderToolName(rawName),
            result: block.is_error === true ? {} : renderToolResult(rawName, text),
            tool_id: toolId,
          })
        }

        continue
      }

      const text = blockText(content)

      if (text.trim() === '') continue

      nodes = [...sealOpen(nodes), { at: 0, id: nextId('user'), kind: 'user', text }]
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
