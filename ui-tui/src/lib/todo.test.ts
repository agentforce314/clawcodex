import { describe, expect, it } from 'vitest'

import { isChecklistHudOnly, isChecklistHudTool, todoGlyph, todoTone } from './todo.js'

describe('todoGlyph', () => {
  it('uses the original TaskListV2 icons (✔ done, ◼ in-progress, ◻ pending)', () => {
    expect(todoGlyph('completed')).toBe('✔')
    expect(todoGlyph('in_progress')).toBe('◼')
    expect(todoGlyph('pending')).toBe('◻')
    expect(todoGlyph('cancelled')).toBe('◻')
  })
})

describe('todoTone', () => {
  it('keeps todo status rows neutral instead of red/green', () => {
    expect(todoTone('completed')).toBe('dim')
    expect(todoTone('cancelled')).toBe('dim')
    expect(todoTone('pending')).toBe('body')
    expect(todoTone('in_progress')).toBe('active')
  })
})

describe('isChecklistHudTool', () => {
  it('covers the checklist mutations and nothing else', () => {
    expect(isChecklistHudTool('TodoWrite')).toBe(true)
    expect(isChecklistHudTool('TaskCreate')).toBe(true)
    expect(isChecklistHudTool('TaskUpdate')).toBe(true)
    // Reads whose output the HUD never shows.
    expect(isChecklistHudTool('TaskList')).toBe(false)
    expect(isChecklistHudTool('TaskGet')).toBe(false)
    expect(isChecklistHudTool('TaskOutput')).toBe(false)
    expect(isChecklistHudTool('Bash')).toBe(false)
    expect(isChecklistHudTool(undefined)).toBe(false)
  })
})

describe('isChecklistHudOnly', () => {
  const ok = '{"success": true, "taskId": "abc123", "updatedFields": ["status"]}'
  const refused = '{"success": false, "taskId": "gone", "updatedFields": [], "error": "Task not found"}'

  it('hides a mutation that landed', () => {
    expect(isChecklistHudOnly('TaskUpdate', undefined, ok)).toBe(true)
    expect(isChecklistHudOnly('TaskCreate', undefined, '{"task": {"id": "abc123", "subject": "Fix auth"}}')).toBe(true)
    expect(isChecklistHudOnly('TodoWrite', undefined, 'Todos have been modified successfully.')).toBe(true)
    expect(isChecklistHudOnly('TaskUpdate', undefined, undefined)).toBe(true)
  })

  it('shows a mutation the backend refused or errored', () => {
    expect(isChecklistHudOnly('TaskUpdate', undefined, refused)).toBe(false)
    expect(isChecklistHudOnly('TaskUpdate', 'Error: invalid taskId', ok)).toBe(false)
    // Backends predating the JSON results answered in prose.
    expect(isChecklistHudOnly('TaskUpdate', undefined, 'Task #abc123 not found')).toBe(false)
  })

  it('never hides a non-checklist tool', () => {
    expect(isChecklistHudOnly('TaskList', undefined, '{"tasks": []}')).toBe(false)
    expect(isChecklistHudOnly('Bash', undefined, '{"success": true}')).toBe(false)
  })
})
