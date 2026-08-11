// Heuristic JSON → human summary for tool results. Default view; technical
// mode still gets the raw JSON section.

import { capitalize, normalize } from '@/lib/text'

const WRAPPER_KEYS = ['data', 'result', 'output', 'response', 'payload'] as const

const PRIORITY_KEYS = [
  'title',
  'name',
  'path',
  'file',
  'filepath',
  'url',
  'href',
  'link',
  'status',
  'id',
  'message',
  'summary',
  'description'
] as const

const ERROR_KEYS = ['error', 'errors', 'failure', 'exception'] as const
// 'stderr' deliberately excluded: many CLIs emit informational lines on
// stderr (npm progress, git's hint:, gcc's `In file included from`) that
// aren't errors. Treating those as error signal flipped tool cards into
// destructive styling for healthy commands.
const ERROR_MSG_KEYS = ['message', 'reason', 'detail'] as const
const NON_ERROR_TEXT = new Set(['', '0', 'false', 'none', 'null', 'nil', 'ok', 'success', 'n/a', 'na'])

type Json = Record<string, unknown>

const isRecord = (v: unknown): v is Json => Boolean(v && typeof v === 'object' && !Array.isArray(v))

function tryJson(value: string): unknown {
  const t = value.trim()

  if (!t) {
    return ''
  }

  if (!/^[{[]|^"/.test(t)) {
    return value
  }

  try {
    return JSON.parse(t)
  } catch {
    return value
  }
}

// TaskOutput is the one tool whose result is not JSON: it serializes to the
// original Claude Code's `<tag>value</tag>` part list (tasks_v2.py
// `_task_output_map_result_to_api`, ported from
// typescript/src/tools/TaskOutputTool/TaskOutputTool.tsx). Left alone it hits
// the plain-string path and the whole tag soup lands on the card, so decode it
// back into the record shape the summarizer below already knows how to render.
//
// Anchored at the head, and only these two tags can appear there — a tool
// result that merely *contains* markup (a fetched HTML page, an XML file read)
// never matches, and a non-match falls through untouched.
const TASK_OUTPUT_LEAD_TAG = /^<(?:retrieval_status|stuck_task_hint)>/
// `output` is deliberately absent: it is in WRAPPER_KEYS, so skipField drops it
// before any renderer sees it. Capturing it anyway would copy and trim up to
// 200KB of log per call, unmemoized, in a render path — for a value that is
// never displayed.
const TASK_FIELDS = ['task_id', 'task_type', 'status', 'description', 'exit_code', 'error'] as const

function tryTaggedTaskOutput(value: string): unknown {
  const text = value.trim()

  if (!TASK_OUTPUT_LEAD_TAG.test(text)) {
    return tryJson(value)
  }

  const read = (tag: string): string | undefined =>
    new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`).exec(text)?.[1]

  const task: Json = {}

  for (const field of TASK_FIELDS) {
    const raw = read(field)

    if (raw !== undefined) {
      task[field] = raw.trim()
    }
  }

  // Key order is the render order (formatRecordSummary walks Object.keys), and
  // the backend puts stuck_task_hint first for the same reason it leads the
  // tag list — it is the one line the poll guard exists to surface. Building
  // it after retrieval_status would quietly demote it.
  const out: Json = {}
  const hint = read('stuck_task_hint')

  if (hint !== undefined) {
    out.stuck_task_hint = hint.trim()
  }

  out.retrieval_status = read('retrieval_status') ?? ''

  // No task fields at all is the `task: null` result (unknown / evicted id).
  out.task = Object.keys(task).length > 0 ? task : null

  return out
}

const norm = (v: unknown): unknown => (typeof v === 'string' ? tryTaggedTaskOutput(v) : v)

const titleCase = (k: string) =>
  k
    .split(/[_\-.]+/)
    .filter(Boolean)
    .map(capitalize)
    .join(' ')

const pluralize = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

function clipInline(value: string, max = 180): string {
  const c = value.replace(/\s+/g, ' ').trim()

  return c.length > max ? `${c.slice(0, max - 1)}…` : c
}

function clipBlock(value: string, maxChars = 1800, maxLines = 18): string {
  const t = value.trim()

  if (!t) {
    return ''
  }

  const lines = t.split('\n')
  let text = lines.slice(0, maxLines).join('\n')
  const clipped = lines.length > maxLines || text.length > maxChars

  if (text.length > maxChars) {
    text = text.slice(0, maxChars - 1).trimEnd()
  }

  return clipped && !text.endsWith('…') ? `${text}…` : text
}

function firstString(record: Json, keys: readonly string[]): string {
  for (const k of keys) {
    const v = record[k]

    if (typeof v === 'string' && v.trim()) {
      return v.trim()
    }
  }

  return ''
}

function orderedKeys(keys: string[]): string[] {
  const priority = PRIORITY_KEYS.filter(k => keys.includes(k))
  const rest = keys.filter(k => !priority.includes(k as never))

  return [...priority, ...rest]
}

const isWrapperKey = (k: string) => (WRAPPER_KEYS as readonly string[]).includes(k)
const skipField = (k: string, v: unknown) => isWrapperKey(k) || ((k === 'success' || k === 'ok') && v === true)

function summarizeScalar(v: unknown): string {
  if (typeof v === 'string') {
    return clipInline(v)
  }

  if (typeof v === 'number' || typeof v === 'boolean') {
    return String(v)
  }

  return ''
}

function summarizeRecordInline(record: Json, depth: number): string {
  if (depth > 3) {
    return pluralize(Object.keys(record).length, 'field')
  }

  const title = firstString(record, ['title', 'name', 'path', 'file', 'filepath', 'url', 'href', 'link', 'id'])
  const status = firstString(record, ['status', 'category', 'type'])
  const message = firstString(record, ['snippet', 'summary', 'description', 'message'])

  if (title && status) {
    return `${clipInline(title, 110)} (${clipInline(status, 54)})`
  }

  if (title && message && title !== message) {
    return `${clipInline(title, 90)} - ${clipInline(message, 84)}`
  }

  if (title) {
    return clipInline(title, 150)
  }

  const pairs = orderedKeys(Object.keys(record))
    .filter(k => !skipField(k, record[k]))
    .map(k => {
      const s = summarizeScalar(record[k])

      return s ? `${titleCase(k)}: ${s}` : ''
    })
    .filter(Boolean)
    .slice(0, 2)

  return pairs.length ? pairs.join(' · ') : pluralize(Object.keys(record).length, 'field')
}

function summarizeListItem(item: unknown, depth: number): string {
  const v = norm(item)

  if (typeof v === 'string') {
    return clipInline(v)
  }

  if (typeof v === 'number' || typeof v === 'boolean') {
    return String(v)
  }

  if (v == null) {
    return ''
  }

  if (Array.isArray(v)) {
    return pluralize(v.length, 'item')
  }

  if (isRecord(v)) {
    return summarizeRecordInline(v, depth + 1)
  }

  return clipInline(String(v))
}

function formatFieldValue(value: unknown, depth: number): string {
  const v = norm(value)
  const scalar = summarizeScalar(v)

  if (scalar) {
    return scalar
  }

  if (v == null) {
    return ''
  }

  if (Array.isArray(v)) {
    if (!v.length) {
      return ''
    }

    const scalars = v.map(summarizeScalar).filter(Boolean)

    if (scalars.length === v.length && v.length <= 4) {
      return clipInline(scalars.join(', '))
    }

    const first = summarizeListItem(v[0], depth + 1)

    return first ? `${pluralize(v.length, 'item')} (${first})` : pluralize(v.length, 'item')
  }

  if (isRecord(v)) {
    return summarizeRecordInline(v, depth + 1)
  }

  return clipInline(String(v))
}

// "Returned N items" / "0 items" / "Returned an empty object" are all
// noise — better to render nothing and let the title carry the signal.
function formatArraySummary(value: unknown[], depth: number): string {
  if (!value.length) {
    return ''
  }

  const max = 6

  const lines = value
    .slice(0, max)
    .map(item => summarizeListItem(item, depth + 1))
    .filter(Boolean)
    .map(l => `- ${l}`)

  if (!lines.length) {
    return ''
  }

  if (value.length > max) {
    const remaining = value.length - max
    lines.push(`- … ${remaining} more ${remaining === 1 ? 'item' : 'items'}`)
  }

  return lines.join('\n')
}

function formatRecordSummary(record: Json, depth: number): string {
  const keys = Object.keys(record)

  if (!keys.length) {
    return ''
  }

  if (depth <= 2) {
    const direct = firstString(record, ['message', 'summary', 'description', 'preview', 'text', 'content'])
    const meaningful = keys.filter(k => !skipField(k, record[k]) && !isWrapperKey(k))

    if (direct && meaningful.length <= 1) {
      return clipBlock(direct)
    }
  }

  const candidates = orderedKeys(keys).filter(k => !skipField(k, record[k]))
  const max = 8
  const lines: string[] = []

  for (const k of candidates) {
    const v = formatFieldValue(record[k], depth + 1)

    if (!v) {
      continue
    }

    lines.push(`- ${titleCase(k)}: ${v}`)

    if (lines.length >= max) {
      break
    }
  }

  if (!lines.length) {
    return ''
  }

  if (candidates.length > lines.length) {
    const remaining = candidates.length - lines.length
    lines.push(`- … ${remaining} more ${remaining === 1 ? 'field' : 'fields'}`)
  }

  return lines.join('\n')
}

function formatSummaryValue(value: unknown, depth: number): string {
  if (depth > 4) {
    return ''
  }

  const v = norm(value)

  if (typeof v === 'string') {
    return clipBlock(v)
  }

  if (typeof v === 'number' || typeof v === 'boolean') {
    return String(v)
  }

  if (v == null) {
    return ''
  }

  if (Array.isArray(v)) {
    return formatArraySummary(v, depth + 1)
  }

  if (isRecord(v)) {
    return formatRecordSummary(v, depth + 1)
  }

  return clipInline(String(v))
}

function unwrapPayload(value: unknown): unknown {
  let cur: unknown = norm(value)

  for (let i = 0; i < 4; i += 1) {
    if (!isRecord(cur)) {
      return cur
    }

    const record = cur
    const key = WRAPPER_KEYS.find(k => record[k] != null)

    if (!key) {
      return record
    }

    cur = norm(record[key])
  }

  return cur
}

function hasMeaningfulErrorValue(value: unknown): boolean {
  const v = norm(value)

  if (v == null) {
    return false
  }

  if (typeof v === 'string') {
    return !NON_ERROR_TEXT.has(normalize(v))
  }

  if (typeof v === 'boolean') {
    return v
  }

  if (typeof v === 'number') {
    return v !== 0
  }

  if (Array.isArray(v)) {
    return v.some(hasMeaningfulErrorValue)
  }

  if (isRecord(v)) {
    return Object.keys(v).length > 0
  }

  return true
}

function hasErrorSignal(record: Json): boolean {
  const status = typeof record.status === 'string' ? record.status : ''

  return (
    record.success === false ||
    record.ok === false ||
    /\b(error|failed|failure|fatal|exception)\b/i.test(status) ||
    ERROR_KEYS.some(k => hasMeaningfulErrorValue(record[k]))
  )
}

function valueErrorText(value: unknown): string {
  const v = norm(value)

  if (typeof v === 'string') {
    return hasMeaningfulErrorValue(v) ? clipBlock(v, 700, 12) : ''
  }

  if (Array.isArray(v)) {
    return clipBlock(v.map(valueErrorText).filter(Boolean).slice(0, 3).join('; '), 700, 12)
  }

  if (isRecord(v)) {
    const direct = firstString(v, ERROR_MSG_KEYS)

    if (direct) {
      return clipBlock(direct, 700, 12)
    }
  }

  return ''
}

function findNestedError(value: unknown, depth: number, seen: Set<unknown>): string {
  if (depth > 5) {
    return ''
  }

  const v = norm(value)

  if (!v || typeof v !== 'object' || seen.has(v)) {
    return ''
  }

  seen.add(v)

  if (Array.isArray(v)) {
    for (const item of v) {
      const nested = findNestedError(item, depth + 1, seen)

      if (nested) {
        return nested
      }
    }

    return ''
  }

  const record = v as Json

  for (const k of ERROR_KEYS) {
    if (!hasMeaningfulErrorValue(record[k])) {
      continue
    }

    const text = valueErrorText(record[k])

    if (text) {
      return text
    }
  }

  if (hasErrorSignal(record)) {
    const direct = firstString(record, ERROR_MSG_KEYS)

    if (direct) {
      return clipBlock(direct, 700, 12)
    }
  }

  for (const k of [...ERROR_KEYS, ...WRAPPER_KEYS, 'details', 'meta']) {
    const nested = findNestedError(record[k], depth + 1, seen)

    if (nested) {
      return nested
    }
  }

  return ''
}

export function formatToolResultSummary(value: unknown): string {
  return formatSummaryValue(unwrapPayload(value), 0) || formatSummaryValue(value, 0)
}

export function extractToolErrorMessage(value: unknown): string {
  return findNestedError(value, 0, new Set())
}
