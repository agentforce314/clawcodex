/**
 * The pinned task checklist (CC's TaskListV2), in its three live shapes:
 *
 *  busy, expanded — rows hang off the busy line through the `  └  ` connector
 *    (the original's Spinner.tsx:275 mount inside MessageResponse):
 *
 *      ✳ Building the parser… (…)
 *        └  ✔ Map call sites
 *           ◼ Extract loader
 *           ◻ Add tests
 *
 *  busy, hidden (ctrl+t) — the panel yields to the busy line's `Next:` row
 *    (the original's expandedView='none').
 *
 *  idle — the standalone header render, which persists between turns while
 *    the list is incomplete (REPL.tsx:4934).
 */
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  process.env.FORCE_COLOR = '0'
  process.env.NO_COLOR = '1'
})

import { patchTurnState, resetTurnState } from '../app/turnStore.js'
import { patchUiState } from '../app/uiStore.js'
import { BusyLine } from '../components/busyLine.js'
import { ComposerFooter } from '../components/composerFooter.js'
import { LiveTodoPanel } from '../components/streamingAssistant.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { TodoItem } from '../types.js'

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 44 })
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

  return stripAnsi(output)
}

const TODOS: TodoItem[] = [
  { activeForm: 'Mapping call sites', content: 'Map call sites', id: '1', status: 'completed' },
  { activeForm: 'Extracting the loader', content: 'Extract loader', id: '2', status: 'in_progress' },
  { content: 'Add tests', id: '3', status: 'pending' }
]

beforeEach(() => {
  resetTurnState()
  patchTurnState({ todos: TODOS })
  patchUiState({ busy: true, statusBar: 'off', theme: DEFAULT_THEME })
})

describe('busy: list attached under the busy line', () => {
  it('hangs the rows off the └ connector, later rows aligned under the first', () => {
    // Non-TTY renders append one block per frame, so match anchored rows
    // rather than counting lines. The connector lead is 5 columns
    // ("  └  ") and every later row indents exactly those 5 — the
    // alignment from the reference's MessageResponse flex-row split.
    const out = renderToString(<LiveTodoPanel />)

    expect(out).toMatch(/^ {2}└ {2}✔ Map call sites$/m)
    expect(out).toMatch(/^ {5}◼ Extract loader$/m)
    expect(out).toMatch(/^ {5}◻ Add tests$/m)
    // No row renders unindented — everything hangs under the connector.
    expect(out).not.toMatch(/^[✔◼◻]/m)
  })

  it('renders no count header while attached', () => {
    expect(renderToString(<LiveTodoPanel />)).not.toContain('tasks (')
  })

  it('suppresses the busy line\'s Next: row — the list itself is right below', () => {
    const out = renderToString(<BusyLine t={DEFAULT_THEME} turnStartedAt={Date.now()} />)

    expect(out).not.toContain('Next:')
  })

  it('ctrl+t hidden: panel yields and the busy line\'s Next: row takes over', () => {
    patchTurnState({ todoCollapsed: true })

    expect(renderToString(<LiveTodoPanel />).trim()).toBe('')

    const busy = renderToString(<BusyLine t={DEFAULT_THEME} turnStartedAt={Date.now()} />)

    expect(busy).toContain('Next: Add tests')
  })

  it('busy-line verb is the in-progress todo\'s activeForm', () => {
    const busy = renderToString(<BusyLine t={DEFAULT_THEME} turnStartedAt={Date.now()} />)

    expect(busy).toContain('Extracting the loader…')
  })
})

describe('idle: standalone header render persists between turns', () => {
  beforeEach(() => patchUiState({ busy: false }))

  it('renders the count header plus rows', () => {
    const out = renderToString(<LiveTodoPanel />)

    expect(out).toContain('3 tasks (1 done, 1 in progress, 1 open)')
    expect(out).toContain('✔ Map call sites')
    expect(out).toContain('◼ Extract loader')
    expect(out).not.toContain('└')
  })
})

describe('composer footer hint', () => {
  const footer = () =>
    renderToString(
      <ComposerFooter busy inputEmpty mode="bypassPermissions" sh={false} t={DEFAULT_THEME} />
    )

  it('offers ctrl+t while busy with tasks', () => {
    expect(footer()).toContain('esc to interrupt · ctrl+t to hide tasks')
  })

  it('flips the wording when the panel is hidden', () => {
    patchTurnState({ todoCollapsed: true })
    expect(footer()).toContain('ctrl+t to show tasks')
  })

  it('drops the segment with no tasks', () => {
    patchTurnState({ todos: [] })

    const out = footer()

    expect(out).toContain('esc to interrupt')
    expect(out).not.toContain('ctrl+t')
  })

  it('keeps offering the toggle while idle — the pinned panel is most visible then', () => {
    // The reference gates the toggle segment on tasks EXISTING, not on busy
    // (getSpinnerHintParts:521); only the esc segment is loading-gated. An
    // incomplete list stays pinned while idle, so idle is exactly when the
    // user wants to know how to hide it.
    const out = renderToString(
      <ComposerFooter busy={false} inputEmpty mode="bypassPermissions" sh={false} t={DEFAULT_THEME} />
    )

    expect(out).toContain('? for shortcuts · ctrl+t to hide tasks')
  })

  it('stays quiet while idle with no tasks', () => {
    patchTurnState({ todos: [] })

    const out = renderToString(
      <ComposerFooter busy={false} inputEmpty mode="bypassPermissions" sh={false} t={DEFAULT_THEME} />
    )

    expect(out).toContain('? for shortcuts')
    expect(out).not.toContain('ctrl+t')
  })
})
