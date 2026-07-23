import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

const harness = vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR

  return { proc: null as null | EventEmitter, spawnCalls: [] as unknown[][] }
})

vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => {
    harness.spawnCalls.push(args)

    return harness.proc
  }
}))

import { turnController } from '../app/turnController.js'
import { getTurnState, resetTurnState } from '../app/turnStore.js'
import { TodoPanel } from '../components/todoPanel.js'
import { GatewayClient } from '../gatewayClient.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { TodoItem } from '../types.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const TODOS = [
  { activeForm: 'Mapping call sites', content: 'Map call sites', id: '1', status: 'completed' },
  { activeForm: 'Extracting the loader', content: 'Extract loader', id: '2', status: 'in_progress' },
  { content: 'Add tests', id: '3', status: 'pending' }
]

// ── GatewayClient: TodoWrite input → payload.todos ───────────────────────────

describe('GatewayClient todo mapping', () => {
  it('carries TodoWrite input.todos on tool.start and tool.complete', async () => {
    const proc = new FakeProc()
    harness.proc = proc
    const events: any[] = []
    const gw = new GatewayClient()

    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain()

    const last = (t: string) => [...events].reverse().find(e => e.type === t)

    proc.line({
      message: { content: [{ id: 't1', input: { todos: TODOS }, name: 'TodoWrite', type: 'tool_use' }] },
      type: 'assistant'
    })
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.todos).toHaveLength(3)

    proc.line({
      message: {
        content: [{ content: 'Todos have been modified successfully.', is_error: false, tool_use_id: 't1', type: 'tool_result' }]
      },
      type: 'user'
    })
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())
    expect(last('tool.complete').payload.todos?.[1]).toMatchObject({ activeForm: 'Extracting the loader' })

    gw.kill()
  })

  it('projects TaskV2 lifecycle calls into the checklist', async () => {
    const proc = new FakeProc()
    harness.proc = proc
    const events: any[] = []
    const gw = new GatewayClient()

    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain()

    const complete = async (id: string, name: string, input: unknown, result: string) => {
      const expectedCount = events.filter(e => e.type === 'tool.complete').length + 1
      proc.line({ message: { content: [{ id, input, name, type: 'tool_use' }] }, type: 'assistant' })
      proc.line({
        message: { content: [{ content: result, is_error: false, tool_use_id: id, type: 'tool_result' }] },
        type: 'user'
      })
      await vi.waitFor(() => expect(events.filter(e => e.type === 'tool.complete')).toHaveLength(expectedCount))

      return events.filter(e => e.type === 'tool.complete').at(-1).payload.todos
    }

    expect(
      await complete(
        't1',
        'TaskCreate',
        { activeForm: 'Fixing auth', description: 'Details', subject: 'Fix auth' },
        '{"task":{"id":"abc123","subject":"Fix auth"}}'
      )
    ).toEqual([{ activeForm: 'Fixing auth', content: 'Fix auth', id: 'abc123', status: 'pending' }])

    expect(
      await complete('t2', 'TaskUpdate', { status: 'in_progress', taskId: 'abc123' }, 'Updated task #abc123 status')
    ).toEqual([{ activeForm: 'Fixing auth', content: 'Fix auth', id: 'abc123', status: 'in_progress' }])

    expect(
      await complete(
        't3',
        'TaskList',
        {},
        '{"tasks":[{"id":"abc123","subject":"Fix auth","status":"completed"},{"id":"def456","subject":"Add tests","status":"pending"}]}'
      )
    ).toEqual([
      { activeForm: 'Fixing auth', content: 'Fix auth', id: 'abc123', status: 'completed' },
      { content: 'Add tests', id: 'def456', status: 'pending' }
    ])

    expect(
      await complete('t4', 'TaskUpdate', { status: 'deleted', taskId: 'abc123' }, 'Updated task #abc123 deleted')
    ).toEqual([{ content: 'Add tests', id: 'def456', status: 'pending' }])

    gw.kill()
  })
})

// ── turnController: silent completion, no stranded spinner ──────────────────

describe('turnController TodoWrite lifecycle', () => {
  it('records todos (with activeForm) and completes without a trail line or stranded tool', () => {
    turnController.recordError()
    resetTurnState()
    turnController.startMessage()

    turnController.recordToolStart('t1', 'TodoWrite', '')
    expect(getTurnState().tools).toHaveLength(1)

    turnController.recordToolComplete('t1', 'TodoWrite', undefined, undefined, 0.1, TODOS)

    const state = getTurnState()

    expect(state.todos).toHaveLength(3)
    expect(state.todos[1]).toMatchObject({ activeForm: 'Extracting the loader', status: 'in_progress' })
    expect(state.tools).toHaveLength(0) // activeTools cleared — no stranded spinner
    expect(state.streamPendingTools).toHaveLength(0) // no trail line
    expect(state.streamSegments).toHaveLength(0)

    turnController.recordError()
    resetTurnState()
  })

  it('keeps the trail line for a FAILED TodoWrite (errors must stay visible)', () => {
    turnController.recordError()
    resetTurnState()
    turnController.startMessage()

    turnController.recordToolStart('t1', 'TodoWrite', '')
    turnController.recordToolComplete('t1', 'TodoWrite', 'Error: invalid todos', undefined, 0.1)

    expect(getTurnState().streamPendingTools[0]).toContain('TodoWrite')

    turnController.recordError()
    resetTurnState()
  })
})

// ── TodoPanel: TaskListV2 anatomy ────────────────────────────────────────────

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 90, isTTY: false, rows: 40 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  return output
}

describe('TodoPanel rendering', () => {
  const items = TODOS as TodoItem[]

  it('renders the TaskListV2 icons with the original states', () => {
    const output = renderToString(React.createElement(TodoPanel, { t: DEFAULT_THEME, todos: items }))
    const plain = stripAnsi(output)

    expect(plain).toContain('3 tasks (1 done, 1 in progress, 1 open)')
    expect(plain).toMatch(/✔ Map call sites/)
    expect(plain).toMatch(/◼ Extract loader/)
    expect(plain).toMatch(/◻ Add tests/)
    // done rows: strikethrough SGR (9); in-progress: bold around subject
    expect(output).toContain('\x1b[9m')
    // icon colors: ✔ success green (78;186;101), ◼ claude orange (215,119,87 → hex D77757)
    expect(output).toContain('78;186;101')
    expect(output).toContain('215;119;87')
  })

  it('caps the visible list and summarizes the overflow', () => {
    const many = Array.from({ length: 14 }, (_, i) => ({
      content: `todo ${i}`,
      id: String(i),
      status: i < 2 ? 'completed' : i === 2 ? 'in_progress' : 'pending'
    })) as TodoItem[]

    const plain = stripAnsi(renderToString(React.createElement(TodoPanel, { t: DEFAULT_THEME, todos: many })))

    expect(plain).toContain('todo 9')
    expect(plain).not.toContain('todo 10')
    expect(plain).toContain('+0 in progress, 4 pending, 0 completed')
  })
})
