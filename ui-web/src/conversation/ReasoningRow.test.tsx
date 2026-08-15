import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ReasoningNode } from '../state/transcript.ts'
import { ReasoningRow } from './ReasoningRow.tsx'

afterEach(cleanup)

function node(overrides: Partial<ReasoningNode> = {}): ReasoningNode {
  return {
    id: 'r1',
    kind: 'reasoning',
    sealed: false,
    text: 'First line of the thought.\nSecond line, still going.',
    ...overrides,
  }
}

describe('ReasoningRow', () => {
  it('shows the live thought as a readable block, not a sliding line', () => {
    const { container, getByText } = render(<ReasoningRow node={node()} />)

    expect(getByText('Thinking')).toBeTruthy()
    // The whole tail is in the flow, wrapped — the reader watches words
    // arrive instead of a line shifting left on every delta.
    expect(container.textContent).toContain('First line of the thought.')
    expect(container.textContent).toContain('Second line, still going.')
  })

  it('collapses a sealed thought to its first line', () => {
    const { getByText, queryByText } = render(<ReasoningRow node={node({ sealed: true })} />)

    expect(getByText('Thought')).toBeTruthy()
    expect(getByText('First line of the thought.')).toBeTruthy()
    // The full text stays behind the disclosure until asked for.
    expect(queryByText(/Second line/)).toBeNull()
  })

  it('discloses the full sealed thought on click', () => {
    const { getByRole, getByText } = render(<ReasoningRow node={node({ sealed: true })} />)

    fireEvent.click(getByRole('button'))

    expect(getByText(/Second line, still going./)).toBeTruthy()
  })
})
