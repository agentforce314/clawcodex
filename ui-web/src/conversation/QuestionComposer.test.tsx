import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PendingQuestion, QuestionRequestPayload } from '../gateway/protocol.ts'
import { QuestionComposer, answerOf, parseRecommended } from './QuestionComposer.tsx'

afterEach(cleanup)

function request(...questions: PendingQuestion[]): QuestionRequestPayload {
  return { questions, request_id: 'req-1' }
}

const COLOUR: PendingQuestion = {
  header: 'Palette',
  options: [
    { description: 'Warm', label: 'Red (Recommended)' },
    { description: 'Cool', label: 'Blue' },
  ],
  question: 'Which colour?',
}

function pick(name: string): void {
  fireEvent.click(screen.getByRole('radio', { name }))
}

function send(): void {
  fireEvent.click(screen.getByRole('button', { name: /Send answers/ }))
}

describe('parseRecommended', () => {
  it('splits the suffix for display', () => {
    expect(parseRecommended('Red (Recommended)')).toEqual({ recommended: true, text: 'Red' })
    expect(parseRecommended('red (recommended)')).toEqual({ recommended: true, text: 'red' })
  })

  it('leaves a label that merely mentions the word alone', () => {
    expect(parseRecommended('Recommended reading')).toEqual({
      recommended: false,
      text: 'Recommended reading',
    })
  })
})

describe('answerOf', () => {
  it('omits a skipped question rather than answering it emptily', () => {
    // The tool renders the answer map verbatim for the model, so `""` would
    // read as the user having said something empty rather than nothing.
    expect(answerOf({ custom: '', selected: [], skipped: true })).toBeUndefined()
  })

  it('omits an untouched question', () => {
    expect(answerOf({ custom: '  ', selected: [], skipped: false })).toBeUndefined()
  })

  it('joins a multi-select pick with any typed addition', () => {
    expect(answerOf({ custom: ' teal ', selected: ['Red', 'Blue'], skipped: false })).toBe(
      'Red, Blue, teal',
    )
  })
})

describe('QuestionComposer', () => {
  it('drops a description that only repeats its own label', () => {
    // Models routinely fill the description with the label; rendering both
    // puts the same word on two lines.
    render(
      <QuestionComposer
        onRespond={vi.fn()}
        request={request({
          options: [{ description: 'Red', label: 'Red' }, { description: 'Cool', label: 'Blue' }],
          question: 'Which colour?',
        })}
      />,
    )

    expect(screen.queryAllByText('Red')).toHaveLength(1)
    expect(screen.getByText('Cool')).toBeTruthy()
  })

  it('shows the question, its header, and the option descriptions', () => {
    render(<QuestionComposer onRespond={vi.fn()} request={request(COLOUR)} />)

    expect(screen.getByRole('heading', { name: 'Which colour?' })).toBeTruthy()
    expect(screen.getByText('Palette')).toBeTruthy()
    expect(screen.getByText('Warm')).toBeTruthy()
  })

  it('strips the recommendation suffix for display but answers with the real label', () => {
    // The label is the value the agent gets back; only the display is trimmed.
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)

    expect(screen.getByText('Recommended')).toBeTruthy()
    pick('Red (Recommended)')
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', { 'Which colour?': 'Red (Recommended)' })
  })

  it('keys the answer by the question text', () => {
    // That string is what the tool filters answers on — an index or a header
    // would be dropped server-side without a word.
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    pick('Blue')
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', { 'Which colour?': 'Blue' })
  })

  it('replaces the pick in single-select', () => {
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    pick('Red (Recommended)')
    pick('Blue')
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', { 'Which colour?': 'Blue' })
  })

  it('accumulates picks in multi-select and toggles them off', () => {
    const onRespond = vi.fn()
    const multi: PendingQuestion = { ...COLOUR, multi_select: true }
    render(<QuestionComposer onRespond={onRespond} request={request(multi)} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Red (Recommended)' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blue' }))
    send()
    expect(onRespond).toHaveBeenCalledWith('submit', {
      'Which colour?': 'Red (Recommended), Blue',
    })

    fireEvent.click(screen.getByRole('checkbox', { name: 'Red (Recommended)' }))
    send()
    expect(onRespond).toHaveBeenLastCalledWith('submit', { 'Which colour?': 'Blue' })
  })

  it('lets a typed answer replace a single-select pick', () => {
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    pick('Red (Recommended)')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'green' } })
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', { 'Which colour?': 'green' })
  })

  it('answers a question that offers no options at all', () => {
    const onRespond = vi.fn()
    const open: PendingQuestion = { options: [], question: 'What should I name it?' }
    render(<QuestionComposer onRespond={onRespond} request={request(open)} />)

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'atlas' } })
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', { 'What should I name it?': 'atlas' })
  })

  it('refuses to advance on an untouched question', () => {
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    send()

    expect(onRespond).not.toHaveBeenCalled()
    expect(screen.getByText(/Choose an option/)).toBeTruthy()
  })

  it('walks a set one question at a time and submits every answer at the end', () => {
    const onRespond = vi.fn()
    const second: PendingQuestion = {
      options: [{ label: 'Yes' }, { label: 'No' }],
      question: 'Ship it?',
    }
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR, second)} />)

    expect(screen.getByText('Question 1 of 2')).toBeTruthy()
    pick('Blue')
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))

    expect(screen.getByText('Question 2 of 2')).toBeTruthy()
    pick('Yes')
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', {
      'Ship it?': 'Yes',
      'Which colour?': 'Blue',
    })
  })

  it('lets Back revisit an earlier answer', () => {
    const onRespond = vi.fn()
    const second: PendingQuestion = { options: [{ label: 'Yes' }], question: 'Ship it?' }
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR, second)} />)

    pick('Red (Recommended)')
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    pick('Blue')
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    pick('Yes')
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', {
      'Ship it?': 'Yes',
      'Which colour?': 'Blue',
    })
  })

  it('skipping drops that question from the answers and still submits the rest', () => {
    const onRespond = vi.fn()
    const second: PendingQuestion = { options: [{ label: 'Yes' }], question: 'Ship it?' }
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR, second)} />)

    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    pick('Yes')
    send()

    expect(onRespond).toHaveBeenCalledWith('submit', { 'Ship it?': 'Yes' })
  })

  it('skipping the only question submits nothing, which the tool reads as "no answers"', () => {
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))

    expect(onRespond).toHaveBeenCalledWith('submit', {})
  })

  it('declines on Escape', () => {
    // The agent's worker thread is parked on this; the dismiss gesture has to
    // resolve it, and a decline is a real answer rather than a silent drop.
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onRespond).toHaveBeenCalledWith('decline', {})
  })

  it('declines from the close button', () => {
    const onRespond = vi.fn()
    render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Decline to answer' }))

    expect(onRespond).toHaveBeenCalledWith('decline', {})
  })

  it('starts clean when a new request arrives', () => {
    const onRespond = vi.fn()
    const view = render(<QuestionComposer onRespond={onRespond} request={request(COLOUR)} />)
    pick('Red (Recommended)')

    const next: PendingQuestion = { options: [{ label: 'Yes' }], question: 'Ship it?' }
    view.rerender(<QuestionComposer onRespond={onRespond} request={request(next)} />)
    send()

    expect(onRespond).not.toHaveBeenCalled()
  })
})
