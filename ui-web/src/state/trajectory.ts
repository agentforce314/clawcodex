/**
 * The trajectory ledger: the same gateway events the chat reducer folds, kept
 * at full resolution and stamped with timings.
 *
 * Why a second reducer rather than a richer first one: the chat model is built
 * to *forget* — it seals bubbles, drops replayed copies, and merges a turn's
 * usage into one running total, because that is what makes a conversation
 * readable. The trajectory needs the opposite: every model request as its own
 * step, in order, with what it cost and how long each phase took. Folding both
 * jobs into one reducer would make each harder to reason about, and the chat
 * one is the load-bearing surface.
 *
 * **Timings are observed here, on the client.** The gateway reports *what*
 * happened, not when — so the clock readings below are this process's, taken
 * as each event lands. Over a loopback socket the transport cost is well under
 * a millisecond, which is far below the resolution anyone reads these at. The
 * one thing that is NOT approximated is token counts: those come from the
 * backend's own per-step accounting (`step.complete`).
 *
 * Every metric is honest about absence. A step whose first token was never
 * observed reports no TTFT rather than a plausible number — see
 * `stepMetrics()`, whose consumers render the reason instead of a figure.
 */

import { promptTokens } from '../gateway/protocol.ts'
import type {
  GatewayEvent,
  StepCompletePayload,
  ToolResult,
  UsagePayload,
} from '../gateway/protocol.ts'
import type {
  MessageCompletePayload,
  MessageDeltaPayload,
  ToolCompletePayload,
  ToolStartPayload,
} from '../gateway/protocol.ts'

export type TrajectoryKind = 'assistant' | 'notice' | 'tool' | 'user'

/** Everything known about one model request's timing and cost. */
export interface StepMetrics {
  /** When the request began: the turn's start, or the previous step's end. */
  startedAt: number | null
  /** When the first token of any kind arrived — prose or reasoning. */
  firstTokenAt: number | null
  /** When the request finished emitting. */
  completedAt: number | null
  model?: string
  stopReason?: string
  usage?: UsagePayload
}

export interface TrajectoryRecord {
  args?: Record<string, unknown>
  callId?: string
  /** Full content for the details panel; `text` is its one-line summary. */
  detail?: string
  durationMs: number | null
  endedAt: number | null
  error?: string
  id: string
  /** 1-based position in the ledger, shown as `#N`. */
  index: number
  isError?: boolean
  kind: TrajectoryKind
  metrics?: StepMetrics
  result?: ToolResult
  startedAt: number | null
  /** 1-based model request within the turn; 0 for records that are not one. */
  step: number
  /** Model reasoning for this step, when it emitted any. */
  thinking?: string
  /** One-line summary. */
  text: string
  toolName?: string
  /** 1-based conversation turn. */
  turn: number
}

export interface TrajectoryState {
  records: TrajectoryRecord[]
  /** Turn currently being recorded, or 0 before the first prompt. */
  turn: number
  /** Model requests completed in the current turn. */
  stepsThisTurn: number
  /**
   * When the next model request began — the turn's start, then each time the
   * tools of the previous step all finish. Null between turns.
   */
  pendingStepStartedAt: number | null
  /** Ledger index of the assistant record currently streaming. */
  openAssistant: number | null
  /** Tool records still running, by tool id. */
  openTools: Record<string, number>
}

export function emptyTrajectory(): TrajectoryState {
  return {
    openAssistant: null,
    openTools: {},
    pendingStepStartedAt: null,
    records: [],
    stepsThisTurn: 0,
    turn: 0,
  }
}

/**
 * Clock reading for one event.
 *
 * Injectable because every duration in this file is a difference of two of
 * these: a test that cannot control the clock cannot assert a duration.
 */
export type Clock = () => number

const now: Clock = () => Date.now()

function firstLine(text: string, limit = 240): string {
  const line = text.replace(/\s+/g, ' ').trim()

  return line.length > limit ? `${line.slice(0, limit)}…` : line
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** The one argument worth showing beside a tool name on its ledger row. */
export function toolSummary(name: string, args: Record<string, unknown>): string {
  const command = str(args.command)

  if (command !== '') return command

  const path = str(args.path) || str(args.file_path) || str(args.notebook_path)

  if (path !== '') return path

  return str(args.pattern) || str(args.query) || str(args.url) || str(args.description) || name
}

function pushRecord(
  state: TrajectoryState,
  record: Omit<TrajectoryRecord, 'index'>,
): TrajectoryState {
  const index = state.records.length + 1

  return { ...state, records: [...state.records, { ...record, index }] }
}

function patchRecord(
  state: TrajectoryState,
  index: number,
  patch: Partial<TrajectoryRecord>,
): TrajectoryState {
  const records = state.records.map(record =>
    record.index === index ? { ...record, ...patch } : record,
  )

  return { ...state, records }
}

function recordAt(state: TrajectoryState, index: number | null): TrajectoryRecord | undefined {
  if (index === null) return undefined

  return state.records.find(record => record.index === index)
}

/** Open the assistant record for the step now streaming, if one is not open. */
function openStep(state: TrajectoryState, at: number): TrajectoryState {
  if (state.openAssistant !== null) return state

  const startedAt = state.pendingStepStartedAt ?? at
  const step = state.stepsThisTurn + 1
  const next = pushRecord(state, {
    detail: '',
    durationMs: null,
    endedAt: null,
    id: `step-${state.turn}-${step}`,
    kind: 'assistant',
    metrics: { completedAt: null, firstTokenAt: null, startedAt },
    startedAt,
    step,
    text: '',
    turn: state.turn,
  })

  return { ...next, openAssistant: next.records.length, pendingStepStartedAt: null }
}

function noteFirstToken(state: TrajectoryState, index: number, at: number): TrajectoryState {
  const record = recordAt(state, index)

  if (record?.metrics === undefined || record.metrics.firstTokenAt !== null) return state

  return patchRecord(state, index, { metrics: { ...record.metrics, firstTokenAt: at } })
}

/** Seal the open step. Called when its envelope lands, or when the turn ends. */
function closeStep(state: TrajectoryState, at: number, patch: Partial<StepMetrics> = {}) {
  const index = state.openAssistant

  if (index === null) return state

  const record = recordAt(state, index)
  const metrics: StepMetrics = {
    ...(record?.metrics ?? { completedAt: null, firstTokenAt: null, startedAt: null }),
    ...patch,
    completedAt: at,
  }
  const startedAt = metrics.startedAt

  const sealed = patchRecord(state, index, {
    durationMs: startedAt === null ? null : Math.max(0, at - startedAt),
    endedAt: at,
    metrics,
    // A step that only called tools has no prose; name it for the ledger so
    // the row is not blank.
    text: record?.text === '' ? '(tool calls only)' : (record?.text ?? ''),
  })

  return {
    ...sealed,
    openAssistant: null,
    stepsThisTurn: sealed.stepsThisTurn + 1,
    // The next step begins when the tools this one asked for are done. If it
    // asked for none, it begins now.
    pendingStepStartedAt: at,
  }
}

/** Append the user's prompt and start a turn. A local action, not an event. */
export function recordPrompt(
  state: TrajectoryState,
  text: string,
  clock: Clock = now,
): TrajectoryState {
  const at = clock()
  const turn = state.turn + 1
  const started = { ...state, pendingStepStartedAt: at, stepsThisTurn: 0, turn }

  return pushRecord(started, {
    detail: text,
    durationMs: null,
    endedAt: at,
    id: `user-${turn}`,
    kind: 'user',
    startedAt: at,
    step: 0,
    text: firstLine(text),
    turn,
  })
}

/** Fold one gateway push into the ledger. Unknown types are returned as-is. */
export function applyTrajectoryEvent(
  state: TrajectoryState,
  event: GatewayEvent,
  clock: Clock = now,
): TrajectoryState {
  const at = clock()

  switch (event.type) {
    case 'message.start':
      // The turn may already have been opened locally by recordPrompt; only
      // supply a start time when it was not.
      return state.pendingStepStartedAt === null
        ? { ...state, pendingStepStartedAt: at }
        : state

    case 'message.delta': {
      const text = (event.payload as MessageDeltaPayload | undefined)?.text ?? ''

      if (text === '') return state

      const opened = openStep(state, at)
      const index = opened.openAssistant

      if (index === null) return opened

      const marked = noteFirstToken(opened, index, at)
      const record = recordAt(marked, index)
      const detail = (record?.detail ?? '') + text

      return patchRecord(marked, index, { detail, text: firstLine(detail) })
    }

    case 'reasoning.delta':
    case 'thinking.delta': {
      const text = (event.payload as MessageDeltaPayload | undefined)?.text ?? ''

      if (text === '') return state

      const opened = openStep(state, at)
      const index = opened.openAssistant

      if (index === null) return opened

      const marked = noteFirstToken(opened, index, at)
      const record = recordAt(marked, index)
      const thinking = (record?.thinking ?? '') + text
      const summary = record?.text === '' || record?.text === undefined
        ? firstLine(thinking)
        : record.text

      return patchRecord(marked, index, { text: summary, thinking })
    }

    case 'step.complete': {
      const payload = (event.payload ?? {}) as StepCompletePayload
      // A step that emitted nothing but tool calls never opened a record.
      const opened = openStep(state, at)

      return closeStep(opened, at, {
        ...(payload.model === undefined ? {} : { model: payload.model }),
        ...(payload.stop_reason === undefined ? {} : { stopReason: payload.stop_reason }),
        ...(payload.usage === undefined ? {} : { usage: payload.usage }),
      })
    }

    case 'tool.start': {
      const payload = event.payload as ToolStartPayload | undefined

      if (payload === undefined) return state

      // A backend that reports no per-step usage never sends step.complete,
      // so the tool call is what tells us the step finished emitting.
      const sealed = state.openAssistant === null ? state : closeStep(state, at)
      const args = payload.args ?? {}
      const next = pushRecord(sealed, {
        args,
        callId: payload.tool_id,
        durationMs: null,
        endedAt: null,
        id: `tool-${payload.tool_id}`,
        kind: 'tool',
        startedAt: at,
        step: sealed.stepsThisTurn,
        text: toolSummary(payload.name, args),
        toolName: payload.name,
        turn: sealed.turn,
      })

      return {
        ...next,
        openTools: { ...next.openTools, [payload.tool_id]: next.records.length },
        // A running tool suspends the next step's clock; it restarts when the
        // last one finishes.
        pendingStepStartedAt: null,
      }
    }

    case 'tool.complete': {
      const payload = event.payload as ToolCompletePayload | undefined

      if (payload === undefined) return state

      const index = state.openTools[payload.tool_id]

      if (index === undefined) return state

      const record = recordAt(state, index)
      const startedAt = record?.startedAt ?? null
      const resolved = patchRecord(state, index, {
        durationMs: startedAt === null ? null : Math.max(0, at - startedAt),
        endedAt: at,
        ...(payload.error === undefined ? {} : { error: payload.error, isError: true }),
        ...(payload.result === undefined ? {} : { result: payload.result }),
      })

      const openTools = { ...resolved.openTools }
      delete openTools[payload.tool_id]

      return {
        ...resolved,
        openTools,
        // The next model request starts once nothing is still running.
        pendingStepStartedAt:
          Object.keys(openTools).length === 0 ? at : resolved.pendingStepStartedAt,
      }
    }

    case 'message.complete': {
      const payload = (event.payload ?? {}) as MessageCompletePayload
      let next = closeStep(state, at)

      if (payload.status === 'error') {
        next = pushRecord(next, {
          detail: payload.error ?? '',
          durationMs: null,
          endedAt: at,
          error: payload.error,
          id: `error-${next.turn}-${next.records.length}`,
          isError: true,
          kind: 'notice',
          startedAt: at,
          step: next.stepsThisTurn,
          text: payload.error ?? 'Turn failed',
          turn: next.turn,
        })
      }

      return { ...next, pendingStepStartedAt: null }
    }

    default:
      return state
  }
}

/* ── aggregates ──────────────────────────────────────────────────────────── */

export interface TrajectoryStats {
  cacheHitRatio: number | null
  inputTokens: number
  llmMs: number
  outputTokens: number
  steps: number
  throughput: number | null
  toolMs: number
  /** Mean TTFT over the steps that recorded one. */
  ttftMs: number | null
  turns: number
}

/**
 * Roll the ledger up into the figures the status bar shows.
 *
 * Averages are taken over the records that actually carry the input, never
 * over all of them — a turn where two of five steps recorded a first token
 * must report those two, not a mean diluted by three zeros.
 */
export function trajectoryStats(state: TrajectoryState): TrajectoryStats {
  let llmMs = 0
  let toolMs = 0
  let steps = 0
  let inputTokens = 0
  let outputTokens = 0
  let cacheRead = 0
  let generationMs = 0
  let generationTokens = 0
  const ttfts: number[] = []

  for (const record of state.records) {
    if (record.kind === 'tool') {
      toolMs += record.durationMs ?? 0
      continue
    }

    if (record.kind !== 'assistant') continue

    steps += 1
    llmMs += record.durationMs ?? 0

    const metrics = record.metrics

    if (metrics === undefined) continue

    if (metrics.startedAt !== null && metrics.firstTokenAt !== null) {
      ttfts.push(Math.max(0, metrics.firstTokenAt - metrics.startedAt))
    }

    const usage = metrics.usage

    if (usage === undefined) continue

    // The FULL prompt, not the billed-as-input part: a well-cached run would
    // otherwise look like it barely sent anything.
    inputTokens += promptTokens(usage)
    outputTokens += usage.output
    cacheRead += usage.cache_read ?? 0

    if (metrics.firstTokenAt !== null && metrics.completedAt !== null) {
      generationMs += Math.max(0, metrics.completedAt - metrics.firstTokenAt)
      generationTokens += usage.output
    }
  }

  const generationSeconds = generationMs / 1000

  return {
    cacheHitRatio: inputTokens > 0 ? cacheRead / inputTokens : null,
    inputTokens,
    llmMs,
    outputTokens,
    steps,
    throughput: generationSeconds > 0 ? generationTokens / generationSeconds : null,
    toolMs,
    ttftMs: ttfts.length === 0 ? null : ttfts.reduce((a, b) => a + b, 0) / ttfts.length,
    turns: state.turn,
  }
}

/* ── formatting ──────────────────────────────────────────────────────────── */

export function formatMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`

  const minutes = Math.floor(ms / 60_000)

  return `${minutes}m ${((ms % 60_000) / 1000).toFixed(0)}s`
}

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`

  return String(value)
}

/**
 * The four request-timing readings, each either a figure or the reason there
 * isn't one. Never a zero standing in for "unknown".
 */
export interface TimingReadout {
  generation: string
  started: string
  throughput: string
  total: string
}

export function stepMetrics(metrics: StepMetrics | undefined): TimingReadout {
  if (metrics === undefined) {
    return {
      generation: 'Not recorded',
      started: 'Not recorded',
      throughput: 'Not recorded',
      total: 'Not recorded',
    }
  }

  const { completedAt, firstTokenAt, startedAt, usage } = metrics
  const started =
    startedAt === null ? 'Not recorded' : new Date(startedAt).toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })

  const total =
    startedAt === null
      ? 'Step start unavailable'
      : completedAt === null
        ? 'Pending'
        : formatMs(completedAt - startedAt)

  const generation =
    firstTokenAt === null
      ? 'First token unavailable'
      : completedAt === null
        ? 'Pending'
        : formatMs(completedAt - firstTokenAt)

  let throughput: string

  if (usage === undefined) throughput = 'Usage unavailable'
  else if (firstTokenAt === null) throughput = 'First token unavailable'
  else if (completedAt === null) throughput = 'Pending'
  else {
    const seconds = (completedAt - firstTokenAt) / 1000

    throughput = seconds <= 0 ? 'Duration too short' : `${(usage.output / seconds).toFixed(1)} tok/s`
  }

  return { generation, started, throughput, total }
}

/** TTFT for one step, or the reason it has none. */
export function stepTtft(metrics: StepMetrics | undefined): string {
  if (metrics === undefined) return 'Not recorded'
  if (metrics.startedAt === null) return 'Step start unavailable'
  if (metrics.firstTokenAt === null) return 'First token unavailable'

  return formatMs(metrics.firstTokenAt - metrics.startedAt)
}
