import { describe, expect, it } from 'vitest'

import type { ToolNode, TranscriptNode } from './transcript.ts'
import { countTodos, currentTodos } from './todo-progress.ts'

let counter = 0

function done(name: string, args: Record<string, unknown>, output?: string): ToolNode {
  counter += 1

  return {
    args,
    id: `t${counter}`,
    kind: 'tool',
    name,
    result: output === undefined ? {} : { output },
    startedAt: 0,
    state: 'done',
    toolId: `tool${counter}`,
  }
}

describe('currentTodos', () => {
  it('is empty for a transcript with no checklist tools', () => {
    const nodes: TranscriptNode[] = [
      { id: 'u1', at: 0, kind: 'user', text: 'hi' },
      done('terminal', { command: 'ls' }, 'a b'),
    ]

    expect(currentTodos(nodes)).toEqual([])
  })

  it('takes the latest TodoWrite as the whole list', () => {
    const nodes = [
      done('todo', { todos: [{ content: 'one', status: 'pending' }] }),
      done('todo', {
        todos: [
          { content: 'one', status: 'completed' },
          { content: 'two', status: 'in_progress' },
        ],
      }),
    ]

    expect(currentTodos(nodes)).toEqual([
      { content: 'one', status: 'completed' },
      { content: 'two', status: 'in_progress' },
    ])
  })

  it('folds the task registry into an ordered checklist', () => {
    const nodes = [
      done('TaskCreate', { subject: 'Map the lineup' }, '{"task": {"id": "aaa", "subject": "Map the lineup"}}'),
      done('TaskCreate', { subject: 'Capture pricing' }, '{"task": {"id": "bbb", "subject": "Capture pricing"}}'),
      done('TaskUpdate', { status: 'completed', taskId: 'aaa' }, '{"success": true}'),
      done('TaskUpdate', { taskId: 'bbb' }, '{"success": true, "statusChange": {"from": "pending", "to": "in_progress"}}'),
    ]

    expect(currentTodos(nodes)).toEqual([
      { content: 'Map the lineup', id: 'aaa', status: 'completed' },
      { content: 'Capture pricing', id: 'bbb', status: 'in_progress' },
    ])
  })

  it('removes a task deleted by an update', () => {
    const nodes = [
      done('TaskCreate', { subject: 'Doomed' }, '{"task": {"id": "x"}}'),
      done('TaskUpdate', { status: 'deleted', taskId: 'x' }, '{"success": true}'),
    ]

    expect(currentTodos(nodes)).toEqual([])
  })

  it('ignores a failed update', () => {
    const nodes = [
      done('TaskCreate', { subject: 'Stays pending' }, '{"task": {"id": "x"}}'),
      done('TaskUpdate', { status: 'completed', taskId: 'x' }, '{"success": false}'),
    ]

    expect(currentTodos(nodes)).toEqual([{ content: 'Stays pending', id: 'x', status: 'pending' }])
  })

  it('treats a TaskList result as an authoritative snapshot', () => {
    const nodes = [
      done('TaskCreate', { subject: 'Old' }, '{"task": {"id": "gone"}}'),
      done(
        'TaskList',
        {},
        '{"tasks": [{"id": "a", "subject": "First", "status": "completed"}, {"id": "b", "subject": "Second", "status": "pending"}]}',
      ),
    ]

    expect(currentTodos(nodes)).toEqual([
      { content: 'First', id: 'a', status: 'completed' },
      { content: 'Second', id: 'b', status: 'pending' },
    ])
  })

  it('skips running and failed calls', () => {
    const running: ToolNode = {
      args: { todos: [{ content: 'not yet', status: 'pending' }] },
      id: 'r',
      kind: 'tool',
      name: 'todo',
      startedAt: 0,
      state: 'running',
      toolId: 'r',
    }
    const failed: ToolNode = { ...running, error: 'boom', id: 'f', state: 'error', toolId: 'f' }

    expect(currentTodos([running, failed])).toEqual([])
  })
})

describe('countTodos', () => {
  it('splits the three states', () => {
    expect(
      countTodos([
        { content: 'a', status: 'completed' },
        { content: 'b', status: 'completed' },
        { content: 'c', status: 'in_progress' },
        { content: 'd', status: 'pending' },
      ]),
    ).toEqual({ completed: 2, inProgress: 1, pending: 1 })
  })
})
