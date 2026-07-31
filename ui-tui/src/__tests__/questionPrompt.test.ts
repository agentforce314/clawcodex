import { describe, expect, it } from 'vitest'

import {
  hideSubmitTab,
  navChipLabels,
  OTHER,
  otherIndex,
  questionAction,
  rowCount,
  serializeMultiSelect,
  submitIndex,
  withAnswer
} from '../components/questionPrompt.js'
import type { QuestionSpec } from '../gatewayTypes.js'

// Pure-dispatch tests, mirroring __tests__/approvalAction.test.ts: the key
// matrix is verified without mounting React + Ink + a fake stdin.

const single: QuestionSpec = {
  header: 'Scope',
  question: 'Which posts?',
  options: [{ label: 'Two' }, { label: 'All seven' }, { label: 'New post' }]
}

const multi: QuestionSpec = { ...single, multiSelect: true }

const ctxFor = (q: QuestionSpec, typing = false) => ({
  multiSelect: !!q.multiSelect,
  optionCount: q.options?.length ?? 0,
  rows: rowCount(q),
  typing
})

const K = {}

describe('row geometry', () => {
  it('single-select rows are the options plus the free-text row', () => {
    expect(rowCount(single)).toBe(4)
    expect(otherIndex(single)).toBe(3)
    expect(submitIndex(single)).toBe(-1)
  })

  it('multiSelect adds a submit row, since Enter toggles instead of confirming', () => {
    expect(rowCount(multi)).toBe(5)
    expect(otherIndex(multi)).toBe(3)
    expect(submitIndex(multi)).toBe(4)
  })
})

describe('hideSubmitTab', () => {
  it('hides the review step for a lone single-select question', () => {
    expect(hideSubmitTab([single])).toBe(true)
  })

  it('keeps it for multiple questions or any multiSelect', () => {
    expect(hideSubmitTab([single, single])).toBe(false)
    expect(hideSubmitTab([multi])).toBe(false)
  })
})

describe('questionAction', () => {
  it('Esc cancels, and beats every other binding', () => {
    expect(questionAction('1', { escape: true, return: true }, 0, ctxFor(single))).toEqual({ kind: 'cancel' })
  })

  it('Esc still cancels while typing (the component re-scopes it to "go back")', () => {
    expect(questionAction('', { escape: true }, 3, ctxFor(single, true))).toEqual({ kind: 'cancel' })
  })

  it('swallows everything but Esc while the free-text row is focused', () => {
    // Otherwise "3" would jump to option 3 instead of being typed.
    expect(questionAction('3', K, 3, ctxFor(single, true))).toEqual({ kind: 'noop' })
    expect(questionAction('', { return: true }, 3, ctxFor(single, true))).toEqual({ kind: 'noop' })
  })

  it('digits quick-pick a real option', () => {
    expect(questionAction('2', K, 0, ctxFor(single))).toEqual({ kind: 'pick', index: 1 })
  })

  it('ignores digits past the last real option', () => {
    // 4 is the free-text row and 5 does not exist; neither is a shortcut, so a
    // stray digit can never submit the dialog.
    expect(questionAction('4', K, 0, ctxFor(single))).toEqual({ kind: 'noop' })
    expect(questionAction('5', K, 0, ctxFor(single))).toEqual({ kind: 'noop' })
    expect(questionAction('0', K, 0, ctxFor(single))).toEqual({ kind: 'noop' })
  })

  it('Enter picks the focused option', () => {
    expect(questionAction('', { return: true }, 1, ctxFor(single))).toEqual({ kind: 'pick', index: 1 })
  })

  it('Enter on the free-text row focuses the input instead of picking', () => {
    expect(questionAction('', { return: true }, 3, ctxFor(single))).toEqual({ kind: 'typing' })
  })

  it('Enter on the multiSelect submit row submits', () => {
    expect(questionAction('', { return: true }, 4, ctxFor(multi))).toEqual({ kind: 'submit' })
  })

  it('Space toggles in multiSelect only', () => {
    expect(questionAction(' ', K, 1, ctxFor(multi))).toEqual({ kind: 'pick', index: 1 })
    expect(questionAction(' ', K, 1, ctxFor(single))).toEqual({ kind: 'noop' })
  })

  it('arrows move within bounds and stop at the edges', () => {
    expect(questionAction('', { upArrow: true }, 1, ctxFor(single))).toEqual({ kind: 'move', delta: -1 })
    expect(questionAction('', { upArrow: true }, 0, ctxFor(single))).toEqual({ kind: 'noop' })
    expect(questionAction('', { downArrow: true }, 2, ctxFor(single))).toEqual({ kind: 'move', delta: 1 })
    expect(questionAction('', { downArrow: true }, 3, ctxFor(single))).toEqual({ kind: 'noop' })
  })

  it('Tab and left/right move between questions', () => {
    expect(questionAction('', { tab: true }, 0, ctxFor(single))).toEqual({ kind: 'question', delta: 1 })
    expect(questionAction('', { shift: true, tab: true }, 0, ctxFor(single))).toEqual({
      kind: 'question',
      delta: -1
    })
    expect(questionAction('', { rightArrow: true }, 0, ctxFor(single))).toEqual({ kind: 'question', delta: 1 })
    expect(questionAction('', { leftArrow: true }, 0, ctxFor(single))).toEqual({ kind: 'question', delta: -1 })
  })

  it('is a no-op for unbound keys', () => {
    expect(questionAction('z', K, 0, ctxFor(single))).toEqual({ kind: 'noop' })
  })
})

describe('serializeMultiSelect', () => {
  it('joins with ", " in toggle order, not option order', () => {
    expect(serializeMultiSelect(['All seven', 'Two'], '')).toBe('All seven, Two')
  })

  it('replaces the sentinel with the typed text and moves it last', () => {
    expect(serializeMultiSelect([OTHER, 'Two'], 'Only the DOGE one')).toBe('Two, Only the DOGE one')
  })

  it('drops the sentinel when nothing was typed', () => {
    expect(serializeMultiSelect([OTHER, 'Two'], '   ')).toBe('Two')
  })

  it('is empty when nothing is picked', () => {
    expect(serializeMultiSelect([], '')).toBe('')
  })
})

describe('withAnswer', () => {
  it('stores a real answer', () => {
    expect(withAnswer({}, 'Q', 'A')).toEqual({ Q: 'A' })
  })

  it('DELETES the key when the value is empty rather than storing an empty string', () => {
    // Regression: toggling a multiSelect option on then off serializes to '',
    // which every UI surface reads as unanswered (☐ chip, "not all answered"
    // warning, review list). Storing it submitted {"Q": ""} anyway and told the
    // model the user had answered with nothing.
    expect(withAnswer({ Q: 'A' }, 'Q', '')).toEqual({})
    expect(Object.hasOwn(withAnswer({ Q: 'A' }, 'Q', ''), 'Q')).toBe(false)
  })

  it('leaves other answers alone and does not mutate its input', () => {
    const base = { Other: 'kept', Q: 'A' }
    expect(withAnswer(base, 'Q', '')).toEqual({ Other: 'kept' })
    expect(base).toEqual({ Other: 'kept', Q: 'A' })
  })
})

describe('navChipLabels', () => {
  const CHROME = 4
  const FIXED = '← '.length + ' →'.length + ' ✔ Submit '.length

  const rendered = (labels: string[]) =>
    labels.filter(Boolean).reduce((sum, l) => sum + CHROME + l.length, 0) + FIXED

  it('renders headers verbatim when they fit', () => {
    expect(navChipLabels(['Scope', 'Depth'], 0, 80)).toEqual(['Scope', 'Depth'])
  })

  it('truncates when they do not, giving the current chip up to half the room', () => {
    const labels = navChipLabels(['Scope', 'Editing depth', 'Third one'], 1, 34)
    expect(labels.join('').length).toBeLessThan('ScopeEditing depthThird one'.length)
    // The focused chip keeps the most room.
    expect(labels[1]!.length).toBeGreaterThanOrEqual(labels[0]!.length)
  })

  it('degrades to the current chip alone when there is no room at all', () => {
    expect(navChipLabels(['Scope', 'Depth'], 1, 4)).toEqual(['', 'Dep'])
  })

  it('never drops the FOCUSED chip while showing an unfocused one', () => {
    // Regression: clamping the focused chip to available/2 could take it under
    // the 4 cells of chrome, so slice() returned '' and the caller rendered
    // nothing — hiding the one chip that says where you are.
    for (let cols = 1; cols <= 80; cols++) {
      const labels = navChipLabels(['Scope', 'Depth'], 0, cols)

      if (labels.some(Boolean)) {
        expect(labels[0], `focused chip empty at cols=${cols}`).not.toBe('')
      }
    }
  })

  it('fits inside the width it is given', () => {
    // Regression: an unconditional floor on the non-focused chips could total
    // wider than `columns` and wrap the nav bar onto a second row.
    const headers = ['Scope', 'Editing depth', 'Sources', 'Tone']

    for (let cols = 21; cols <= 120; cols++) {
      for (let current = 0; current < headers.length; current++) {
        expect(rendered(navChipLabels(headers, current, cols)), `cols=${cols} current=${current}`)
          .toBeLessThanOrEqual(cols)
      }
    }
  })
})
