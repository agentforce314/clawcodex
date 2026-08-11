import { beforeEach, describe, expect, it } from 'vitest'

import {
  archiveDoneTodos,
  archiveTodosAtTurnEnd,
  getTurnState,
  patchTurnState,
  resetTurnState,
  toggleTodoCollapsed
} from '../app/turnStore.js'

describe('turnStore live progress helpers', () => {
  beforeEach(() => resetTurnState())

  it('archives completed todos into a transcript trail and clears the live anchor', () => {
    patchTurnState({
      todos: [
        { content: 'prep', id: 'prep', status: 'completed' },
        { content: 'serve', id: 'serve', status: 'completed' }
      ]
    })

    expect(archiveTodosAtTurnEnd()).toEqual([
      {
        kind: 'trail',
        role: 'system',
        text: '',
        todoCollapsedByDefault: true,
        todos: [
          { content: 'prep', id: 'prep', status: 'completed' },
          { content: 'serve', id: 'serve', status: 'completed' }
        ]
      }
    ])
    expect(getTurnState().todos).toEqual([])
  })

  it('keeps an incomplete list live in the HUD instead of archiving it', () => {
    // CC parity: the checklist persists across turns until the work is done
    // (REPL.tsx:4934 renders TaskListV2 from AppState while idle). Archiving
    // at every turn end was why the pinned panel vanished the moment a turn
    // completed.
    const todos = [
      { content: 'cook', id: 'cook', status: 'completed' as const },
      { content: 'serve', id: 'serve', status: 'in_progress' as const },
      { content: 'eat', id: 'eat', status: 'pending' as const }
    ]

    patchTurnState({ todos })

    expect(archiveTodosAtTurnEnd()).toEqual([])
    expect(getTurnState().todos).toEqual(todos)
  })

  it('returns nothing when there are no todos at turn end', () => {
    expect(archiveTodosAtTurnEnd()).toEqual([])
    expect(archiveDoneTodos()).toEqual([])
  })

  it('tracks collapsed state independently of todo content', () => {
    toggleTodoCollapsed()
    expect(getTurnState().todoCollapsed).toBe(true)

    toggleTodoCollapsed()
    expect(getTurnState().todoCollapsed).toBe(false)
  })
})
