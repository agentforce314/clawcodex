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

import type { ToolResult } from './protocol.ts'

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

/** Tool output text → the result object the matching card knows how to read. */
export function renderToolResult(name: string, text: string): ToolResult {
  const render = renderToolName(name)

  if (text === '') return {}

  if (render === 'read_file' || render === 'web_extract') return { content: text }
  if (render === 'terminal') return { output: text }
  if (render === 'edit_file' || render === 'write_file') return { message: text }

  // No dedicated card: the generic path prefers `context`, and `output` feeds
  // the copy affordance.
  return { context: text, output: text }
}
