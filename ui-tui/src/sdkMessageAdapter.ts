/**
 * Convert server SDK messages into flat transcript entries the Ink UI renders.
 *
 * Distilled from typescript/src/remote/sdkMessageAdapter.ts. Streaming
 * `stream_event` deltas are handled in App.tsx (they mutate a live buffer);
 * everything else maps to zero-or-more finished entries here — including
 * tool_use blocks (rendered as Claude-Code-style tool calls) and tool_result
 * blocks (the indented ⎿ output).
 */
import { readFileSync } from 'node:fs'
import {
  blocksToText,
  type ContentBlock,
  type ServerMessage,
} from './protocol.js'
import { buildToolDiff, type ToolDiff } from './diff.js'

/** Best-effort read of the edited file (local in spawn mode) for true line numbers. */
function readFileSafe(p: unknown): string | undefined {
  if (typeof p !== 'string' || !p) return undefined
  try {
    return readFileSync(p, 'utf8')
  } catch {
    return undefined
  }
}

export interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
  activeForm?: string
}

export interface ContextData {
  percentage: number
  totalTokens: number
  maxTokens: number
  categories: { name: string; tokens: number }[]
}

export type EntryKind =
  | 'banner'
  | 'user'
  | 'assistant'
  | 'tool'
  | 'toolResult'
  | 'system'
  | 'result'
  | 'error'
  | 'context'

export interface TranscriptEntry {
  id: string
  kind: EntryKind
  text: string
  /** tool calls only: the tool name + a compact one-line args preview. */
  toolName?: string
  argsText?: string
  /** tool calls only: the raw input (used to render Edit/Write diffs). */
  input?: Record<string, unknown>
  /** Edit/Write tool calls: precomputed diff hunks (with true file line numbers). */
  diff?: ToolDiff
  /** tool calls: the tool_use id (used to correlate + collapse results). */
  toolUseId?: string
  /** tool results: the tool_use ids they answer (to drop collapsed-read results). */
  forToolUseIds?: string[]
  /** tool results: the tool reported an error (rendered in red). */
  isError?: boolean
  /** collapsed tool summary: how many same-kind calls this entry represents. */
  count?: number
  /** TodoWrite tool calls: the todo list to render as a checklist. */
  todos?: TodoItem[]
  /** /context entries: the context-window usage breakdown to visualize. */
  contextData?: ContextData
  /** banner only: the session info snapshot, captured once at init. */
  bannerData?: { model: string; mode: string; tools: number; cwd?: string }
}

let _seq = 0
function nextId(): string {
  _seq += 1
  return `e${_seq}`
}

/** Pull the human-readable text out of a `stream_event` text delta, if any. */
export function streamDeltaText(msg: ServerMessage): string | null {
  const m = msg as { type?: string; event?: { delta?: { type?: string; text?: string } } }
  if (m.type !== 'stream_event') return null
  const delta = m.event?.delta
  if (delta && delta.type === 'text_delta' && typeof delta.text === 'string') {
    return delta.text
  }
  return null
}

/** Compact one-line preview of tool input (Claude-Code style: Bash(cmd)). */
export function formatToolArgs(input: unknown): string {
  if (input == null) return ''
  if (typeof input === 'string') return truncate(input)
  if (typeof input !== 'object') return truncate(String(input))
  const obj = input as Record<string, unknown>
  // Prefer the single most meaningful field if present.
  for (const k of ['command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt']) {
    if (typeof obj[k] === 'string') return truncate(obj[k] as string)
  }
  const parts = Object.entries(obj).map(
    ([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`,
  )
  return truncate(parts.join(', '))
}

function truncate(s: string, max = 100): string {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max - 1)}…` : one
}

function fmtK(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

function toolResultText(content: string | ContentBlock[] | undefined): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  const parts: string[] = []
  for (const block of content) {
    if (block && block.type === 'tool_result') {
      const inner = block['content']
      parts.push(typeof inner === 'string' ? inner : JSON.stringify(inner))
    } else if (block && block.type === 'text' && typeof block.text === 'string') {
      parts.push(block.text)
    }
  }
  return parts.join('\n')
}

/** Map one server message to finished transcript entries (excludes stream_event). */
export function messageToEntries(msg: ServerMessage): TranscriptEntry[] {
  const type = (msg as { type: string }).type

  if (type === 'system') {
    const m = msg as {
      subtype?: string
      model?: string
      permission_mode?: string
      protocol_version?: string
      tools?: unknown[]
      message?: string
      level?: string
    }
    if (m.subtype === 'init') {
      // Connection info is shown in the welcome Banner, not the transcript.
      return []
    }
    if (m.message) {
      return [{ id: nextId(), kind: m.level === 'error' ? 'error' : 'system', text: m.message }]
    }
    return []
  }

  if (type === 'assistant') {
    const m = msg as { message: { content: string | ContentBlock[] } }
    const content = m.message?.content
    const out: TranscriptEntry[] = []
    const text = blocksToText(content)
    if (text.trim()) out.push({ id: nextId(), kind: 'assistant', text })
    if (Array.isArray(content)) {
      for (const block of content) {
        if (block && block.type === 'tool_use') {
          const toolName = String((block as { name?: string }).name ?? 'tool')
          const tinput = ((block as { input?: Record<string, unknown> }).input ?? {}) as Record<
            string,
            unknown
          >
          // TodoWrite renders as a checklist, not a generic tool call.
          if (toolName === 'TodoWrite' && Array.isArray(tinput['todos'])) {
            out.push({
              id: nextId(),
              kind: 'tool',
              text: '',
              toolName,
              todos: tinput['todos'] as TodoItem[],
              toolUseId: String((block as { id?: string }).id ?? ''),
            })
            continue
          }
          const diff =
            toolName === 'Edit' || toolName === 'Write' || toolName === 'MultiEdit'
              ? (buildToolDiff(toolName, tinput, readFileSafe(tinput['file_path'])) ?? undefined)
              : undefined
          out.push({
            id: nextId(),
            kind: 'tool',
            text: '',
            toolName,
            argsText: formatToolArgs(tinput),
            input: tinput,
            diff,
            toolUseId: String((block as { id?: string }).id ?? ''),
          })
        }
      }
    }
    return out
  }

  if (type === 'user') {
    const m = msg as { message: { content: string | ContentBlock[] } }
    const content = m.message?.content
    const text = toolResultText(content)
    const forToolUseIds = Array.isArray(content)
      ? content
          .filter((b) => b && b.type === 'tool_result')
          .map((b) => String((b as { tool_use_id?: string }).tool_use_id ?? ''))
      : []
    const isError =
      Array.isArray(content) &&
      content.some((b) => b && b.type === 'tool_result' && (b as { is_error?: boolean }).is_error === true)
    return text ? [{ id: nextId(), kind: 'toolResult', text, forToolUseIds, isError }] : []
  }

  if (type === 'result') {
    const m = msg as {
      subtype: string
      num_turns?: number
      usage?: Record<string, number> | null
      error?: string
    }
    if (m.subtype === 'error') {
      return [{ id: nextId(), kind: 'error', text: `error: ${m.error ?? 'unknown'}` }]
    }
    if (m.subtype === 'cancelled') {
      return [{ id: nextId(), kind: 'system', text: 'interrupted' }]
    }
    const u = (m.usage ?? {}) as Record<string, number>
    const inTok = u['input_tokens'] ?? u['input'] ?? 0
    const outTok = u['output_tokens'] ?? u['output'] ?? 0
    const total = inTok + outTok
    const tok = total > 0 ? ` · ${fmtK(total)} tokens` : ''
    const turns = m.num_turns ?? 0
    return [{ id: nextId(), kind: 'result', text: `done · ${turns} turn${turns === 1 ? '' : 's'}${tok}` }]
  }

  return []
}
