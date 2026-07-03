import type { TodoItem } from '../types.js'

export type TodoTone = 'active' | 'body' | 'dim'

// Original TaskListV2 icons: ✔ done (green), ◼ in-progress (claude orange),
// ◻ pending (default); cancelled reuses the pending square, dimmed by tone.
export const todoGlyph = (status: TodoItem['status']) =>
  status === 'completed' ? '✔' : status === 'in_progress' ? '◼' : '◻'

export const todoTone = (status: TodoItem['status']): TodoTone =>
  status === 'in_progress' ? 'active' : status === 'pending' ? 'body' : 'dim'
