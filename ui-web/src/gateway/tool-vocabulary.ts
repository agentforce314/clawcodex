/**
 * ClawCodex tool names → the renderer vocabulary the conversation UI keys off.
 *
 * The gateway already applies this mapping to LIVE events
 * (`src/server/desktop_gateway_translate.py` — `render_tool_name` /
 * `render_tool_result`), because the renderer's per-tool cards are keyed by
 * these names and read each tool's output from a differently-named field.
 *
 * Rehydrated transcripts bypass that translation entirely: `session.resume`
 * returns the raw stored conversation, so a `Read` block would arrive as
 * `Read` and fall through to the generic card — no line-numbered window, and
 * no entry in the details column's file list. This module is the client-side
 * mirror that closes that gap, so a resumed turn renders exactly like the live
 * one that produced it.
 *
 * Keep in step with the Python table; the two are one contract.
 */

import type { AgentResultMeta, ToolResult } from './protocol.ts'

const RENDER_TOOL_NAMES: Record<string, string> = {
  askuserquestion: 'clarify',
  bash: 'terminal',
  bashoutput: 'terminal',
  edit: 'edit_file',
  glob: 'list_files',
  grep: 'search_files',
  killbash: 'terminal',
  killshell: 'terminal',
  ls: 'list_files',
  multiedit: 'edit_file',
  notebookedit: 'edit_file',
  read: 'read_file',
  todowrite: 'todo',
  webfetch: 'web_extract',
  websearch: 'web_search',
  write: 'write_file',
}

/**
 * Unknown tools (Task, MCP tools, …) pass through unchanged and get the
 * generic treatment, which is the right fallback.
 */
export function renderToolName(name: string): string {
  return RENDER_TOOL_NAMES[name.trim().toLowerCase()] ?? name
}

const NUMBERED_LINE = /^\s*\d+\t/

function nonEmptyLines(text: string): number {
  return text.split('\n').filter(line => line.trim() !== '').length
}

/** Tool output text → the result object the matching card knows how to read. */
export function renderToolResult(name: string, text: string): ToolResult {
  const render = renderToolName(name)

  if (text === '') return {}

  if (render === 'read_file') {
    // The live gateway summarises a numbered read as "Read N lines"; a resumed
    // row deserves the same summary, not a blank one.
    const lines = text.split('\n').filter(line => line !== '')
    const first = lines[0]

    if (first !== undefined && NUMBERED_LINE.test(first)) {
      return {
        content: text,
        context: `Read ${lines.length} ${lines.length === 1 ? 'line' : 'lines'}`,
      }
    }

    return { content: text }
  }

  if (render === 'web_extract') return { content: text }
  if (render === 'terminal') return { output: text }
  if (render === 'edit_file' || render === 'write_file') return { message: text }

  // Counts, mirroring the live translate layer, so a resumed Search/List row
  // keeps its "N matches" summary.
  if (render === 'list_files') return { context: text, file_count: nonEmptyLines(text), output: text }
  if (render === 'search_files') {
    return { context: text, match_count: nonEmptyLines(text), output: text }
  }

  // No dedicated card: the generic path prefers `context`, and `output` feeds
  // the copy affordance.
  return { context: text, output: text }
}

function positiveInt(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined
}

/**
 * A stored Agent display envelope → the `agent` facts a live row carries.
 *
 * The agent server persists `{type: "agent", agent_id, status, …}` beside an
 * Agent call's result; the live gateway turns the same envelope into
 * `result.agent` (`agent_result_meta`, the Python twin of this). Doing it
 * here for the stored copy is what makes a resumed Agent row indistinguishable
 * from the one that streamed. Anything that is not that envelope is ignored.
 */
export function agentResultMeta(display: unknown): AgentResultMeta | undefined {
  if (display === null || typeof display !== 'object') return undefined

  const record = display as Record<string, unknown>

  if (record.type !== 'agent' || typeof record.agent_id !== 'string' || record.agent_id === '') {
    return undefined
  }

  const meta: AgentResultMeta = {
    agent_id: record.agent_id,
    status: typeof record.status === 'string' && record.status !== '' ? record.status : 'completed',
  }

  if (typeof record.agent_type === 'string' && record.agent_type !== '') meta.agent_type = record.agent_type
  if (typeof record.model === 'string' && record.model !== '') meta.model = record.model

  const duration = positiveInt(record.total_duration_ms)
  const tokens = positiveInt(record.total_tokens)
  const tools = positiveInt(record.total_tool_use_count)

  if (duration !== undefined) meta.duration_ms = duration
  if (tokens !== undefined) meta.tokens = tokens
  if (tools !== undefined) meta.tool_count = tools

  return meta
}
