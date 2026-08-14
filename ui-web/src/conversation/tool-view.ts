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

export type ToolBodyKind = 'diff' | 'none' | 'output' | 'read' | 'terminal' | 'todo'

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

function countLabel(count: number | undefined, singular: string): string {
  if (count === undefined) return ''

  return `${count} ${count === 1 ? singular : `${singular}s`}`
}

/** Summary for a finished call, from whichever result field its family uses. */
function resultSummary(name: string, result: ToolResult | undefined): string {
  if (result === undefined) return ''

  switch (name) {
    case 'list_files':
      return countLabel(num(result.file_count), 'file')

    case 'search_files':
      return countLabel(num(result.match_count), 'match')

    case 'web_search': {
      const count = countLabel(num(result.result_count), 'result')
      const seconds = num(result.duration_s)

      if (count === '' ) return ''

      return seconds === undefined ? count : `${count} in ${seconds.toFixed(1)}s`
    }

    default:
      return str(result.context) || firstLine(str(result.output) || str(result.message))
  }
}

export function describeTool(node: ToolNode, workspace?: string): ToolView {
  const name = node.name
  const args = node.args
  const title = TITLES[name] ?? name
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

      return {
        body: node.result?.inline_diff === undefined ? 'output' : 'diff',
        icon,
        path: shortPath(path, workspace),
        summary: node.result?.inline_diff === undefined ? resultSummary(name, node.result) : '',
        title,
      }
    }

    case 'todo':
      return { body: 'todo', icon, summary: resultSummary(name, node.result), title }

    case 'web_search':
      return { body: 'output', icon, summary: str(args.query) || str(node.context), title }

    case 'web_extract':
      return { body: 'output', icon, summary: str(args.url) || str(node.context), title }

    case 'clarify':
      return { body: 'output', icon, summary: str(node.context), title }

    default: {
      const summary =
        node.state === 'running'
          ? str(node.context) || str(args.description) || str(args.pattern)
          : resultSummary(name, node.result) || str(node.context)

      return { body: 'output', icon, summary, title }
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
