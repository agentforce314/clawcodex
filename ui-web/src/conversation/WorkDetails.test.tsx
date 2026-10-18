import { fireEvent, render, screen, cleanup } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { $reading, DEFAULT_READING } from '../state/preferences.ts'
import type { TranscriptNode } from '../state/transcript.ts'
import { ChatView } from './ChatView.tsx'
import { chatParts, chatTurns } from './work-groups.ts'

const nodes: TranscriptNode[] = [
  { id: 'u1', kind: 'user', text: 'Inspect the project', at: 0 },
  { id: 'r1', kind: 'reasoning', text: 'Check the files carefully', sealed: true },
  { id: 't1', toolId: 'call1', kind: 'tool', name: 'terminal', args: { command: 'pwd' }, startedAt: 1, state: 'error', error: 'Permission denied' },
  { id: 'a1', kind: 'assistant', text: 'The command was refused.', sealed: true },
]
afterEach(() => { cleanup(); $reading.set({ ...DEFAULT_READING }) })

it('groups only adjacent work and keeps the user and answer in order', () => {
  expect(chatTurns(nodes)).toHaveLength(1)
  expect(chatParts(nodes).map(part => part.kind)).toEqual(['message', 'work', 'message'])
  render(<ChatView nodes={nodes} running={false} />)
  expect(screen.getByText('The command was refused.')).toBeTruthy()
  expect(screen.queryByText('Thought')).toBeNull()
  expect(screen.getByText('1 failed')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Work details/ }))
  expect(screen.getByText('Thought')).toBeTruthy()
})

it('keeps detailed work visible until the next input when requested', () => {
  $reading.set({ ...DEFAULT_READING, collapseTiming: 'next-input' })
  const view = render(<ChatView nodes={nodes} running={false} />)
  expect(screen.getByText('Thought')).toBeTruthy()
  view.rerender(<ChatView nodes={[...nodes, { kind: 'user', id: 'u2', text: 'Try again', at: 2 }]} running />)
  expect(screen.queryByText('Thought')).toBeNull()
  expect(screen.getByRole('button', { name: /Work details/ })).toBeTruthy()
})

it('verbose retains individual work after completion; compact groups live work', () => {
  $reading.set({ ...DEFAULT_READING, workDetails: 'verbose' })
  const view = render(<ChatView nodes={nodes} running={false} />)
  expect(screen.getByText('Thought')).toBeTruthy()
  $reading.set({ ...DEFAULT_READING, workDetails: 'compact' })
  view.rerender(<ChatView nodes={nodes} running />)
  expect(screen.getByRole('button', { name: /Work details/ })).toBeTruthy()
})

it('pages by whole turns and restores older messages on demand', () => {
  const history: TranscriptNode[] = Array.from({ length: 24 }, (_, n) => ({ id: `u${n}`, kind: 'user', text: `Message ${n}`, at: n }))
  render(<ChatView nodes={history} running={false} />)
  expect(screen.queryByText('Message 0')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Show earlier messages (4 turns)' }))
  expect(screen.getByText('Message 0')).toBeTruthy()
  expect(screen.getByText('Message 23')).toBeTruthy()
})
