/**
 * The session's CURRENT checklist, folded from the transcript.
 *
 * Two tool families write it. TodoWrite carries the whole list each call, so
 * its latest call simply replaces the state. The task registry
 * (TaskCreate/TaskUpdate/TaskList) edits incrementally: a create adds a
 * pending item under the id its result minted, an update moves one item's
 * status, a list result is an authoritative snapshot. Folding both into one
 * ordered list is exactly what the TUI's task HUD does — the transcript rows
 * say what each call did; this says where the work stands NOW.
 *
 * Pure over the nodes so the live stream and a rehydrated session agree by
 * construction.
 */

import type { TranscriptNode } from './transcript.ts'

export type TodoProgressStatus = 'completed' | 'in_progress' | 'pending'

export interface TodoProgressItem {
  content: string
  /** Present for task-registry items; TodoWrite items have no ids. */
  id?: string
  status: TodoProgressStatus
}

function asStatus(value: unknown): TodoProgressStatus | undefined {
  return value === 'completed' || value === 'in_progress' || value === 'pending'
    ? value
    : undefined
}

function parseJson(text: unknown): Record<string, unknown> | undefined {
  if (typeof text !== 'string') return undefined

  const trimmed = text.trim()

  if (!trimmed.startsWith('{')) return undefined

  try {
    const parsed = JSON.parse(trimmed) as unknown

    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

function todosFromArgs(args: Record<string, unknown>): TodoProgressItem[] {
  const raw = args.todos

  if (!Array.isArray(raw)) return []

  return raw.flatMap(entry => {
    if (entry === null || typeof entry !== 'object') return []

    const record = entry as Record<string, unknown>
    const content = typeof record.content === 'string' ? record.content : ''

    if (content === '') return []

    return [{ content, status: asStatus(record.status) ?? 'pending' }]
  })
}

export function currentTodos(nodes: readonly TranscriptNode[]): TodoProgressItem[] {
  let list: TodoProgressItem[] = []

  for (const node of nodes) {
    // Only settled calls move the checklist: a running call has not happened
    // yet and a failed one changed nothing.
    if (node.kind !== 'tool' || node.state !== 'done') continue

    const result = parseJson(node.result?.output) ?? parseJson(node.result?.context)

    switch (node.name) {
      case 'todo': {
        const todos = todosFromArgs(node.args)

        if (todos.length > 0) list = todos

        break
      }

      case 'TaskCreate': {
        const task = result?.task

        if (task === null || typeof task !== 'object') break

        const id = String((task as { id?: unknown }).id ?? '')
        const content =
          typeof node.args.subject === 'string'
            ? node.args.subject
            : String((task as { subject?: unknown }).subject ?? '')

        if (id === '' || content === '') break

        list = [...list.filter(item => item.id !== id), { content, id, status: 'pending' }]
        break
      }

      case 'TaskUpdate': {
        if (result?.success === false) break

        const id = typeof node.args.taskId === 'string' ? node.args.taskId : ''

        if (id === '') break

        if (node.args.status === 'deleted') {
          list = list.filter(item => item.id !== id)
          break
        }

        const change = result?.statusChange as { to?: unknown } | undefined
        const status = asStatus(node.args.status) ?? asStatus(change?.to)
        const subject = typeof node.args.subject === 'string' ? node.args.subject : undefined

        list = list.map(item =>
          item.id === id
            ? {
                ...item,
                ...(subject !== undefined && subject !== '' && { content: subject }),
                ...(status !== undefined && { status }),
              }
            : item,
        )
        break
      }

      case 'TaskList': {
        const tasks = result?.tasks

        if (!Array.isArray(tasks)) break

        const snapshot = tasks.flatMap(entry => {
          if (entry === null || typeof entry !== 'object') return []

          const record = entry as Record<string, unknown>
          const id = String(record.id ?? '')
          const content = typeof record.subject === 'string' ? record.subject : ''
          const status = asStatus(record.status)

          if (content === '' || status === undefined) return []

          return [{ content, ...(id !== '' && { id }), status }]
        })

        if (snapshot.length > 0) list = snapshot
        break
      }

      default:
        break
    }
  }

  return list
}

export interface TodoProgressCounts {
  completed: number
  inProgress: number
  pending: number
}

export function countTodos(todos: readonly TodoProgressItem[]): TodoProgressCounts {
  let completed = 0
  let inProgress = 0
  let pending = 0

  for (const todo of todos) {
    if (todo.status === 'completed') completed += 1
    else if (todo.status === 'in_progress') inProgress += 1
    else pending += 1
  }

  return { completed, inProgress, pending }
}
