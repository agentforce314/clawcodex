import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { TodoProgressItem } from '../state/todo-progress.ts'
import { TodoPanel } from './TodoPanel.tsx'

afterEach(cleanup)

const TODOS: TodoProgressItem[] = [
  { content: 'Define research scope', status: 'completed' },
  { content: 'Run overview searches', status: 'completed' },
  { content: 'Delegate deep-dive tracks', id: 'a', status: 'in_progress' },
  { content: 'Collect findings', status: 'pending' },
  { content: 'Synthesize report', status: 'pending' },
]

describe('TodoPanel', () => {
  it('renders nothing without a checklist', () => {
    const { container } = render(<TodoPanel todos={[]} />)

    expect(container.firstChild).toBeNull()
  })

  it('summarises the three states in its header', () => {
    const { getByText } = render(<TodoPanel todos={TODOS} />)

    expect(getByText('To-dos')).toBeTruthy()
    expect(getByText('2 completed · 1 in progress · 2 pending')).toBeTruthy()
  })

  it('drops empty states from the header line', () => {
    const { getByText } = render(
      <TodoPanel todos={[{ content: 'only one', status: 'completed' }]} />,
    )

    expect(getByText('1 completed')).toBeTruthy()
  })

  it('lists every item with a status glyph', () => {
    const { container, getByText } = render(<TodoPanel todos={TODOS} />)

    expect(getByText('Delegate deep-dive tracks')).toBeTruthy()
    expect(container.querySelectorAll('li')).toHaveLength(5)
    expect(container.querySelectorAll('li[data-status="completed"]')).toHaveLength(2)
    expect(container.querySelectorAll('li[data-status="in_progress"]')).toHaveLength(1)
    expect(container.querySelectorAll('li[data-status="pending"]')).toHaveLength(2)
  })

  it('collapses to the header line and back', () => {
    const { getByRole, queryByText } = render(<TodoPanel todos={TODOS} />)
    const header = getByRole('button', { expanded: true })

    fireEvent.click(header)

    expect(queryByText('Collect findings')).toBeNull()

    fireEvent.click(getByRole('button', { expanded: false }))

    expect(queryByText('Collect findings')).toBeTruthy()
  })

  it('folds itself once every item is completed', () => {
    const allDone = TODOS.map(todo => ({ ...todo, status: 'completed' as const }))
    const { getByRole, getByText, queryByText } = render(<TodoPanel todos={allDone} />)

    // A finished checklist is a receipt: header only, until asked.
    expect(getByText('5 completed')).toBeTruthy()
    expect(queryByText('Collect findings')).toBeNull()

    fireEvent.click(getByRole('button', { expanded: false }))

    expect(queryByText('Collect findings')).toBeTruthy()
  })

  it('stays open mid-run even after items complete', () => {
    const { queryByText, rerender } = render(<TodoPanel todos={TODOS} />)

    rerender(
      <TodoPanel
        todos={TODOS.map((todo, index) =>
          index < 4 ? { ...todo, status: 'completed' as const } : todo,
        )}
      />,
    )

    // One item still pending: the list keeps showing.
    expect(queryByText('Synthesize report')).toBeTruthy()
  })
})
