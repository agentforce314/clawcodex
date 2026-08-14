import type { TodoItem } from '../types.js'

export type TodoTone = 'active' | 'body' | 'dim'

// Original TaskListV2 icons: ✔ done (green), ◼ in-progress (claude orange),
// ◻ pending (default); cancelled reuses the pending square, dimmed by tone.
export const todoGlyph = (status: TodoItem['status']) =>
  status === 'completed' ? '✔' : status === 'in_progress' ? '◼' : '◻'

export const todoTone = (status: TodoItem['status']): TodoTone =>
  status === 'in_progress' ? 'active' : status === 'pending' ? 'body' : 'dim'

// Tools whose entire UI is the pinned checklist. TodoWrite rewrites the list
// wholesale; TaskV2 mutates one row per call, so a plan of 7 tasks costs 7
// `⏺ TaskCreate(…) ⎿ {"task":{"id":"1383531298b4",…}}` rows up front and
// another `⏺ TaskUpdate ⎿ {"success":true,…,"statusChange":{…}}` row per
// status flip — a screen of JSON restating what the HUD already draws.
const CHECKLIST_HUD_TOOLS = new Set(['TaskCreate', 'TaskUpdate', 'TodoWrite'])

export const isChecklistHudTool = (name?: string) => Boolean(name && CHECKLIST_HUD_TOOLS.has(name))

/** TaskV2 reports a REFUSED mutation as a *successful* tool call carrying
 *  `{"success": false, …, "error": "Task not found"}` (tasks_v2.py
 *  `_task_update_call`), and the HUD then redraws the unchanged checklist —
 *  so hiding that row would swallow the failure with nothing left to show it.
 *  Only a mutation that actually landed is HUD-only. */
export const checklistMutationRefused = (resultText?: string): boolean => {
  const text = resultText?.trim()

  if (!text) {
    return false
  }

  try {
    const parsed: unknown = JSON.parse(text)

    return Boolean(parsed && typeof parsed === 'object' && (parsed as { success?: unknown }).success === false)
  } catch {
    // Backends predating the JSON results answered in prose ("Task #abc123
    // not found" — tasks_v2.py `_format_task_updated`).
    return /\bnot found\b/i.test(text)
  }
}

/** True when a finished tool call adds nothing the checklist HUD isn't
 *  already showing, so the transcript renders no row for it at all. */
export const isChecklistHudOnly = (name?: string, error?: string, resultText?: string) =>
  isChecklistHudTool(name) && !error && !checklistMutationRefused(resultText)
