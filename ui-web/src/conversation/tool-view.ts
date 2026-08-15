/**
 * How a tool call is presented: title, one-line summary, and which card shape
 * (if any) its body takes.
 *
 * Keyed off the gateway's *adapted* tool names — `src/server/desktop_gateway_
 * translate.py` maps ClawCodex's vocabulary (Read/Bash/Glob/…) onto the
 * renderer-facing set (read_file/terminal/list_files/…) and moves each tool's
 * output into the field its card reads. Unknown names (Task, MCP tools) fall
 * through to the generic shape, which is the right default: a title and the
 * raw output.
 *
 * Pure and data-only so the row component stays a renderer.
 */

import type { ToolResult } from '../gateway/protocol.ts'
import type { ToolNode } from '../state/transcript.ts'

export type ToolBodyKind = 'diff' | 'io' | 'none' | 'output' | 'read' | 'terminal' | 'todo' | 'web'

export type ToolIconName =
  | 'edit'
  | 'file'
  | 'globe'
  | 'help'
  | 'layers'
  | 'list'
  | 'search'
  | 'terminal'
  | 'tool'

export interface ToolView {
  body: ToolBodyKind
  icon: ToolIconName
  /** Monospace path shown in place of the summary, for file tools. */
  path?: string
  summary: string
  title: string
}

export interface TodoEntry {
  active: string
  content: string
  status: string
}

/** The `todos` argument, defensively: content-less or malformed entries drop. */
export function readTodos(args: Record<string, unknown>): TodoEntry[] {
  const raw = args.todos

  if (!Array.isArray(raw)) return []

  return raw.flatMap(entry => {
    if (entry === null || typeof entry !== 'object') return []

    const record = entry as Record<string, unknown>
    const content = typeof record.content === 'string' ? record.content : ''

    if (content === '') return []

    return [
      {
        active: typeof record.activeForm === 'string' ? record.activeForm : content,
        content,
        status: typeof record.status === 'string' ? record.status : 'pending',
      },
    ]
  })
}

/** `2/6 done · Porting the reference polish` — progress plus the live item. */
export function todoSummary(args: Record<string, unknown>): string {
  const todos = readTodos(args)

  if (todos.length === 0) return ''

  return todoProgress(todos)
}

function todoProgress(todos: TodoEntry[]): string {
  const done = todos.filter(todo => todo.status === 'completed').length
  const active = todos.find(todo => todo.status === 'in_progress')
  const progress = `${done}/${todos.length} done`

  return active === undefined ? progress : `${progress} · ${active.active}`
}

/** Parse a value that should be JSON, or undefined when it is not. */
function parseJson(text: string): unknown {
  const trimmed = text.trim()

  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return undefined

  try {
    return JSON.parse(trimmed) as unknown
  } catch {
    return undefined
  }
}

/** A row summary must be a sentence, never a JSON dump; '' hides it. */
function proseOnly(summary: string): string {
  return parseJson(summary) === undefined ? summary : ''
}

/**
 * Machine payloads read better indented. Applied to the generic card's OUT
 * side only — copy fidelity matters less than a reader being able to see
 * `"status": "completed"` without horizontal archaeology.
 */
export function prettyMaybeJson(text: string): string {
  const parsed = parseJson(text)

  if (parsed === undefined || typeof parsed !== 'object' || parsed === null) return text

  try {
    return JSON.stringify(parsed, null, 2)
  } catch {
    return text
  }
}

/**
 * The task registry's checklist, from a TaskList result
 * (`{"tasks": [{id, subject, status}, …]}`).
 */
export function readTaskEntries(node: ToolNode): TodoEntry[] {
  const parsed = parseJson(str(node.result?.output) || str(node.result?.context))

  if (parsed === null || typeof parsed !== 'object') return []

  const tasks = (parsed as { tasks?: unknown }).tasks

  if (!Array.isArray(tasks)) return []

  return tasks.flatMap(entry => {
    if (entry === null || typeof entry !== 'object') return []

    const record = entry as Record<string, unknown>
    const subject = typeof record.subject === 'string' ? record.subject : ''

    if (subject === '') return []

    return [
      {
        active: subject,
        content: subject,
        status: typeof record.status === 'string' ? record.status : 'pending',
      },
    ]
  })
}

/** The status a TaskUpdate moved its task to, from args or the result JSON. */
function taskStatusChange(node: ToolNode): string {
  const fromArgs = str(node.args.status)

  if (fromArgs !== '') return fromArgs

  const parsed = parseJson(str(node.result?.output) || str(node.result?.context))

  if (parsed === null || typeof parsed !== 'object') return ''

  const change = (parsed as { statusChange?: { to?: unknown } }).statusChange

  return typeof change?.to === 'string' ? change.to : ''
}

const TASK_STATUS_WORDS: Record<string, string> = {
  completed: 'completed',
  deleted: 'removed',
  in_progress: 'started',
  pending: 'queued',
}

const TITLES: Record<string, string> = {
  clarify: 'Ask',
  edit_file: 'Edit',
  list_files: 'List',
  read_file: 'Read',
  search_files: 'Search',
  terminal: 'Bash',
  todo: 'Todos',
  web_extract: 'Fetch',
  web_search: 'Web search',
  write_file: 'Write',
}

const ICONS: Record<string, ToolIconName> = {
  clarify: 'help',
  edit_file: 'edit',
  list_files: 'list',
  read_file: 'file',
  search_files: 'search',
  terminal: 'terminal',
  todo: 'list',
  web_extract: 'globe',
  web_search: 'globe',
  write_file: 'edit',
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

/** `/home/me/repo/src/app.ts` → `src/app.ts` when a workspace root is known. */
export function shortPath(path: string, workspace?: string): string {
  if (path === '') return ''

  if (workspace !== undefined && workspace !== '' && path.startsWith(workspace)) {
    const rest = path.slice(workspace.length).replace(/^[/\\]/, '')

    if (rest !== '') return rest
  }

  const segments = path.split(/[/\\]/).filter(Boolean)

  return segments.length <= 3 ? path : `…/${segments.slice(-3).join('/')}`
}

function firstLine(text: string): string {
  const line = text.split('\n').find(candidate => candidate.trim() !== '')

  return line === undefined ? '' : line.trim()
}

function argPath(args: Record<string, unknown>): string {
  return (
    str(args.path) || str(args.file_path) || str(args.notebook_path) || str(args.filePath) || ''
  )
}

function countLabel(count: number | undefined, singular: string, plural = `${singular}s`): string {
  if (count === undefined) return ''

  return `${count} ${count === 1 ? singular : plural}`
}

/** Summary for a finished call, from whichever result field its family uses. */
function resultSummary(name: string, result: ToolResult | undefined): string {
  if (result === undefined) return ''

  switch (name) {
    case 'list_files':
      return countLabel(num(result.file_count), 'file')

    case 'search_files':
      return countLabel(num(result.match_count), 'match', 'matches')

    case 'web_search': {
      const count = countLabel(num(result.result_count), 'result')
      const seconds = num(result.duration_s)

      if (count === '' ) return ''

      return seconds === undefined ? count : `${count} in ${seconds.toFixed(1)}s`
    }

    default:
      return proseOnly(str(result.context) || firstLine(str(result.output) || str(result.message)))
  }
}

/**
 * The diff a mutation row shows.
 *
 * Live events carry the server's `inline_diff` (built from the agent's
 * structuredPatch). A REHYDRATED session has no display envelope — but the
 * stored `tool_use` arguments still hold the whole mutation (`old_string` /
 * `new_string`, a MultiEdit's `edits`, a Write's `content`), so the diff is
 * reconstructed from those rather than degrading to "file updated" prose.
 */
export function synthesizeDiff(node: ToolNode): string | undefined {
  const stored = node.result?.inline_diff

  if (stored !== undefined && stored !== '') return stored
  if (node.error !== undefined) return undefined

  const args = node.args
  const path = argPath(args) || str(node.result?.path) || 'file'

  const hunk = (oldText: string, newText: string): string[] => [
    ...(oldText === '' ? [] : oldText.replace(/\n$/, '').split('\n').map(line => `-${line}`)),
    ...(newText === '' ? [] : newText.replace(/\n$/, '').split('\n').map(line => `+${line}`)),
  ]

  if (node.name === 'edit_file') {
    const edits = Array.isArray(args.edits)
      ? (args.edits as { new_string?: unknown; old_string?: unknown }[])
      : []

    if (edits.length > 0) {
      const body = edits.flatMap((edit, index) => {
        const lines = hunk(str(edit.old_string), str(edit.new_string))

        return index === 0 ? lines : ['@@', ...lines]
      })

      return body.length === 0 ? undefined : [`+++ ${path}`, ...body].join('\n')
    }

    const body = hunk(str(args.old_string), str(args.new_string))

    return body.length === 0 ? undefined : [`+++ ${path}`, ...body].join('\n')
  }

  if (node.name === 'write_file') {
    const content = str(args.content)

    if (content === '') return undefined

    return [`+++ ${path}`, ...hunk('', content)].join('\n')
  }

  return undefined
}

/** `mcp__linear__create_issue` → `linear · create_issue`: the server and the
    verb, without the wire prefix. */
function mcpTitle(name: string): string | undefined {
  if (!name.startsWith('mcp__')) return undefined

  const parts = name.slice('mcp__'.length).split('__').filter(part => part !== '')

  if (parts.length === 0) return undefined

  return parts.join(' · ')
}

export function describeTool(node: ToolNode, workspace?: string): ToolView {
  const name = node.name
  const args = node.args
  const title = TITLES[name] ?? mcpTitle(name) ?? name
  const icon = ICONS[name] ?? 'tool'

  if (node.error !== undefined) {
    return { body: 'output', icon, summary: firstLine(node.error), title }
  }

  switch (name) {
    case 'terminal': {
      const command = str(args.command)

      return {
        body: 'terminal',
        icon,
        summary: command === '' ? str(node.context) : command,
        title,
      }
    }

    case 'read_file': {
      const path = argPath(args)

      return {
        body: node.result?.content === undefined ? 'none' : 'read',
        icon,
        path: shortPath(path, workspace),
        summary: resultSummary(name, node.result),
        title,
      }
    }

    case 'edit_file':
    case 'write_file': {
      const path = argPath(args) || str(node.result?.path)
      const diff = synthesizeDiff(node)

      return {
        body: diff === undefined ? 'output' : 'diff',
        icon,
        path: shortPath(path, workspace),
        summary: diff === undefined ? resultSummary(name, node.result) : '',
        title,
      }
    }

    case 'todo': {
      const summary = todoSummary(args)

      return {
        body: 'todo',
        icon,
        summary: summary === '' ? resultSummary(name, node.result) : summary,
        title,
      }
    }

    case 'web_search':
      return { body: 'web', icon, summary: str(args.query) || str(node.context), title }

    case 'web_extract':
      return { body: 'web', icon, summary: str(args.url) || str(node.context), title }

    case 'clarify':
      return { body: 'output', icon, summary: str(node.context), title }

    // The task registry's own tools. Their results are JSON envelopes; the
    // row reads like the Todos row instead of quoting them.
    case 'TaskCreate':
      return { body: 'io', icon: 'list', summary: str(args.subject), title: 'Task' }

    case 'TaskUpdate': {
      const subject = str(node.context) || str(args.subject) || str(args.taskId)
      const status = taskStatusChange(node)
      const word = TASK_STATUS_WORDS[status] ?? status

      return {
        body: 'io',
        icon: 'list',
        summary: word === '' ? subject : subject === '' ? word : `${subject} → ${word}`,
        title: 'Task',
      }
    }

    case 'TaskView':
    case 'TaskGet':
      return {
        body: 'io',
        icon: 'list',
        summary: str(node.context) || str(args.taskId),
        title: 'Task',
      }

    case 'TaskList': {
      const tasks = readTaskEntries(node)

      return {
        body: tasks.length > 0 ? 'todo' : 'io',
        icon: 'list',
        summary: tasks.length > 0 ? todoProgress(tasks) : '',
        title: 'Tasks',
      }
    }

    default: {
      const summary =
        node.state === 'running'
          ? str(node.context) || str(args.description) || str(args.pattern)
          : resultSummary(name, node.result) || proseOnly(str(node.context))

      // Unknown tools (Task, MCP…) disclose both sides of the exchange: the
      // arguments are the only record of what was asked.
      return { body: 'io', icon, summary, title }
    }
  }
}

/** The text a generic card shows, or '' when the row has nothing to disclose. */
export function genericBodyText(node: ToolNode): string {
  if (node.error !== undefined) return node.error

  const result = node.result

  if (result === undefined) return ''

  return str(result.output) || str(result.content) || str(result.message) || ''
}

/**
 * Tool arguments as readable `key: value` lines for the IN side of a generic
 * card — friendlier than JSON-escaped strings for the prompt-sized values a
 * Task or MCP call carries. Multi-line and structured values indent under
 * their key.
 */
export function formatArgs(args: Record<string, unknown>): string {
  const lines: string[] = []

  for (const [key, value] of Object.entries(args)) {
    if (value === undefined) continue

    let text: string

    if (typeof value === 'string') {
      text = value
    } else {
      try {
        text = JSON.stringify(value, null, 1) ?? String(value)
      } catch {
        text = String(value)
      }
    }

    if (text.includes('\n')) {
      lines.push(`${key}:`)
      lines.push(...text.split('\n').map(line => `  ${line}`))
    } else {
      lines.push(`${key}: ${text}`)
    }
  }

  return lines.join('\n')
}
