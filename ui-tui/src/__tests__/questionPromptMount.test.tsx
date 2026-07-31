import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { QuestionPrompt } from '../components/questionPrompt.js'
import type { QuestionSpec } from '../gatewayTypes.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

/** Poll until `check` stops throwing. A fixed settle is load-dependent — 20ms
 *  is plenty when this file runs alone and not nearly enough inside the full
 *  135-file parallel run, which is exactly how a green test becomes a flaky
 *  one. Assert on the condition, not on a guessed duration. */
async function waitFor(check: () => void, timeout = 3000) {
  const started = Date.now()

  for (;;) {
    try {
      check()

      return
    } catch (err) {
      if (Date.now() - started > timeout) {
        throw err
      }

      await delay(10)
    }
  }
}

/** Mount the dialog with a writable stdin so keys can actually be driven.
 *  questionPrompt.test.ts covers the extracted pure helpers; the behavior that
 *  loses answers when it regresses lives in the STATEFUL half — commit(),
 *  submitOther(), the submit view, and the Esc re-scope while typing — none of
 *  which a pure-function test can reach. Same harness as
 *  permissionsPicker.test.tsx. */
function mount(questions: QuestionSpec[]) {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let out = ''

  stdout.on('data', (c: Buffer) => {
    out += c.toString()
  })
  Object.assign(stdout, { columns: 100, rows: 40 })
  Object.assign(stdin, { isTTY: true, ref: () => {}, setRawMode: () => {}, unref: () => {} })

  const onAnswer = vi.fn()

  const app = renderSync(
    React.createElement(QuestionPrompt, { cols: 100, onAnswer, questions, t: DEFAULT_THEME }),
    {
      exitOnCtrlC: false,
      patchConsole: false,
      stderr: stderr as NodeJS.WriteStream,
      stdin: stdin as NodeJS.ReadStream,
      stdout: stdout as NodeJS.WriteStream
    }
  )

  return {
    /** Reset the accumulator so a later `toContain` means "rendered SINCE this
     *  point". `out` is cumulative and ink writes incremental cell diffs, so
     *  without this every assertion reads as "has ever been written". */
    clearOutput: () => {
      out = ''
    },
    onAnswer,
    output: () => stripAnsi(out),
    /** Write one key sequence and wait for it to be CONSUMED, not for a
     *  guessed duration. A fixed settle is two bugs: it is load-dependent, and
     *  worse, if the loop stalls between two writes the PassThrough coalesces
     *  them into a single chunk — ink parses one chunk's keys in a single
     *  batched update, so the second key's handler runs against the first
     *  key's pre-commit closure and silently computes the wrong state. Draining
     *  before returning keeps every key in its own chunk. */
    async press(seq: string, settle = 1) {
      stdin.write(seq)

      const deadline = Date.now() + 2000

      while ((stdin as unknown as { readableLength: number }).readableLength > 0 && Date.now() < deadline) {
        await delay(1)
      }

      await delay(settle)
    },
    unmount: () => app.unmount()
  }
}

const ARROW_DOWN = '[B'
const ENTER = '\r'
const ESC = ''
const TAB = '\t'
const SHIFT_TAB = '\x1b[Z'

const q1: QuestionSpec = {
  header: 'Scope',
  question: 'Which posts?',
  options: [{ description: 'Just those two', label: 'Two' }, { label: 'All seven' }]
}

const q2: QuestionSpec = {
  header: 'Depth',
  multiSelect: true,
  question: 'How deep?',
  options: [{ label: 'Facts only' }, { label: 'Expand' }, { label: 'Rewrite' }]
}

describe('QuestionPrompt — lone single-select question', () => {
  it('submits immediately on pick, with no review step', async () => {
    // hideSubmitTab: one single-select question has no Submit chip, so the
    // pick IS the submit. If commit() stopped short of onAnswer here, the
    // dialog would sit there with no visible way forward.
    const p = mount([q1])

    await p.press('1')
    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'Which posts?': 'Two' }))
    p.unmount()
  })

  it('submits the focused option on Enter', async () => {
    const p = mount([q1])

    await p.press(ARROW_DOWN)
    await p.press(ENTER)
    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'Which posts?': 'All seven' }))
    p.unmount()
  })

  it('declines with null on Esc, which is not the same as an empty submit', async () => {
    const p = mount([q1])

    await p.press(ESC, 200)
    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith(null))
    p.unmount()
  })

  it('renders the options, their descriptions, and the free-text row', async () => {
    const p = mount([q1])

    await waitFor(() => expect(p.output()).toContain('Which posts?'))
    const out = p.output().replace(/\s+/g, ' ')

    expect(out).toContain('1. Two')
    expect(out).toContain('Just those two')
    // Upstream never prints the word "Other" — the row is its placeholder.
    expect(out).toContain('Type something.')
    expect(out).not.toContain('Other')
    p.unmount()
  })
})

describe('QuestionPrompt — free-text row', () => {
  it('does NOT cancel the dialog on Enter with an empty field', async () => {
    // Deliberate divergence from upstream, which cancels the WHOLE dialog here
    // (select.tsx onEmptyInputSubmit) and throws away every answer already
    // given. A stray Enter must be inert.
    const p = mount([q1])

    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ENTER) // focus the free-text row
    await waitFor(() => expect(p.output()).toContain('Enter to accept')) // precondition
    await p.press(ENTER) // Enter on empty

    await delay(150)
    expect(p.onAnswer).not.toHaveBeenCalled()
    p.unmount()
  })

  it('submits the typed text as the answer, not the __other__ sentinel', async () => {
    const p = mount([q1])

    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ENTER)
    await p.press('only the DOGE one')
    await waitFor(() => expect(p.output()).toContain('only the DOGE one'))
    await p.press(ENTER)

    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'Which posts?': 'only the DOGE one' }))
    p.unmount()
  })

  it('re-scopes Esc to "back to the list" while typing, instead of cancelling', async () => {
    // TextInput deliberately passes Esc through, so without the second
    // isActive-gated useInput this Esc would reach the main handler and
    // destroy every answer given so far. Silent, total data loss.
    const p = mount([q1])

    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ENTER)
    await waitFor(() => expect(p.output()).toContain('Enter to accept')) // precondition
    await p.press(ESC, 200)

    await delay(150)
    expect(p.onAnswer).not.toHaveBeenCalled()

    // Still live: the list took the key, so picking still works.
    await p.press('1')
    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'Which posts?': 'Two' }))
    p.unmount()
  })

  it('swallows digits while typing so they land in the text, not on an option', async () => {
    const p = mount([q1])

    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ENTER)
    await p.press('2')

    expect(p.onAnswer).not.toHaveBeenCalled()
    expect(p.output()).toContain('2')
    p.unmount()
  })

  it('auto-checks the free-text row on type and drops it when cleared (multiSelect)', async () => {
    // setOtherText's multiSelect branch had no coverage at all: it adds the
    // OTHER sentinel to `picked` on type and filters it back out when the
    // field is emptied, writing `picked` and `answers` off the same closure.
    const p = mount([q2])

    await p.press('1') // toggle 'Facts only'
    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ARROW_DOWN)
    await p.press(ENTER) // focus the free-text row
    await waitFor(() => expect(p.output()).toContain('Enter to accept'))

    await p.press('and the charts')
    await waitFor(() => expect(p.output()).toContain('and the charts'))
    await p.press(ESC, 200) // back to the list; the sentinel should be checked

    await p.press(TAB)
    await p.press(ENTER)
    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'How deep?': 'Facts only, and the charts' }))
    p.unmount()
  })
})

describe('QuestionPrompt — multi-question flow', () => {
  it('advances to the next question on pick and marks the first answered', async () => {
    const p = mount([q1, q2])

    await p.press('1')
    await waitFor(() => expect(p.output()).toContain('How deep?'))
    const out = p.output().replace(/\s+/g, ' ')

    expect(p.onAnswer).not.toHaveBeenCalled() // review step comes first
    expect(out).toContain('☒ Scope') // answered chip
    p.unmount()
  })

  it('toggles multi-select options and joins them in toggle order', async () => {
    const p = mount([q1, q2])

    await p.press('1') // answer q1, advance
    await p.press('3') // toggle Rewrite first
    await p.press('1') // then Facts only
    await p.press(TAB) // to the review step
    await p.press(ENTER) // submit

    await waitFor(() =>
      expect(p.onAnswer).toHaveBeenCalledWith({
        'How deep?': 'Rewrite, Facts only',
        'Which posts?': 'Two'
      })
    )
    p.unmount()
  })

  it('drops a question toggled back to empty rather than submitting an empty answer', async () => {
    // Every surface reads '' as unanswered (☐ chip, the "not all answered"
    // warning, the review list). Submitting {"Q": ""} anyway would tell the
    // model the user answered a question the UI had just called unanswered.
    const p = mount([q1, q2])

    await p.press('1')
    await p.press('2') // toggle on
    await p.press('2') // toggle back off
    await p.press(TAB)
    await p.press(ENTER)

    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'Which posts?': 'Two' }))
    p.unmount()
  })
})

describe('QuestionPrompt — untrusted model input', () => {
  it('strips ANSI/OSC escapes out of model-supplied strings', async () => {
    // Every string here is model-controlled and the model may be relaying text
    // it read from a file or web page. Raw escapes reach the terminal and can
    // move the cursor or repaint rows the dialog does not own.
    const nasty: QuestionSpec = {
      header: '\u001b[31mScope\u001b[0m',
      question: 'Pick\u001b[2J one',
      options: [{ description: 'desc\u001b]0;pwned\u0007', label: 'Opt\u001b[1mA' }, { label: 'B' }]
    }

    const p = mount([nasty])

    await waitFor(() => expect(p.output()).toContain('Pick one'))
    const out = p.output()

    expect(out).toContain('OptA')
    expect(out).toContain('desc')
    expect(out).not.toContain('\u001b[2J')
    expect(out).not.toContain('\u001b]0;')
    p.unmount()
  })

  it('cannot forge extra option rows with newlines in a label', async () => {
    // stripAnsi keeps \n, and ink splits on it even under truncate-end, so a
    // label could paint a second convincing row. The digit shortcut picks by
    // ARRAY INDEX, so the forged "2." and the real option 2 are different
    // things — the user reads one and selects the other.
    const forged: QuestionSpec = {
      header: 'Deploy',
      question: 'Which deploy?',
      options: [
        { label: 'Cancel deploy\n  2. Deploy to staging (safe)' },
        { label: 'Deploy to PRODUCTION' }
      ]
    }

    const p = mount([forged])

    await waitFor(() => expect(p.output()).toContain('Which deploy?'))
    // One option is one row: the forged row must not exist as its own line.
    expect(p.output()).not.toMatch(/^\s*2\. Deploy to staging \(safe\)\s*$/m)

    // And the answer that reaches the model carries no embedded newline.
    await p.press('1')
    await waitFor(() =>
      expect(p.onAnswer).toHaveBeenCalledWith({
        'Which deploy?': 'Cancel deploy 2. Deploy to staging (safe)'
      })
    )
    p.unmount()
  })

  it('windows a long option list instead of overflowing the frame', async () => {
    // The schema advertises 2-4 options but enforces nothing, so a model can
    // send any number. Unwindowed, the frame outgrows the viewport and every
    // keystroke becomes a full-frame repaint.
    const many: QuestionSpec = {
      header: 'Many',
      question: 'Pick one of many',
      options: Array.from({ length: 30 }, (_, i) => ({ label: `Option ${i + 1}` }))
    }

    const p = mount([many])

    await waitFor(() => expect(p.output()).toContain('Option 1'))
    const out = p.output()

    expect(out).toContain('more') // the ↓ N more affordance
    expect(out).not.toContain('Option 30')
    p.unmount()
  })
})

describe('QuestionPrompt — question navigation clamps', () => {
  // questionAction only reports a direction; the clamp lives in the unexported
  // goto(), so these bounds are unreachable from the pure-dispatch tests.

  it('does not run off the front on Shift+Tab at the first question', async () => {
    const p = mount([q1, q2])

    await waitFor(() => expect(p.output()).toContain('Which posts?'))
    p.clearOutput()

    await p.press(SHIFT_TAB)

    // A clamped move is a no-op, so it repaints NOTHING — asserting on a
    // redraw here would be asserting on the bug. Prove the clamp behaviorally
    // instead: we must still be on q1, and the dialog must still be live.
    await delay(100)
    expect(p.output()).toBe('')

    // Picking still works and advances to q2, so the index did not go negative
    // and strand the dialog on an out-of-range render.
    await p.press('1')
    await waitFor(() => expect(p.output()).toContain('How deep?'))
    // Advanced to q2 rather than submitting — there is a review step to reach.
    expect(p.onAnswer).not.toHaveBeenCalled()
    p.unmount()
  })

  it('stops at the review step rather than past it', async () => {
    const p = mount([q1, q2])

    await p.press(TAB)
    await p.press(TAB)
    await p.press(TAB)
    await p.press(TAB)
    await waitFor(() => expect(p.output()).toContain('Review your answers'))

    // Enter here must still submit — if the index had run past maxIndex the
    // component would render null and swallow the key.
    await p.press(ENTER)
    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({}))
    p.unmount()
  })
})

describe('QuestionPrompt — review step', () => {
  it('lists the answers and warns when some are missing', async () => {
    const p = mount([q1, q2])

    await p.press('1')
    await p.press(TAB)
    await waitFor(() => expect(p.output()).toContain('Review your answers'))
    const out = p.output().replace(/\s+/g, ' ')

    expect(out).toContain('Which posts?')
    expect(out).toContain('Two')
    expect(out).toContain('You have not answered all questions')
    p.unmount()
  })

  it('submits on Enter with the Submit row focused', async () => {
    const p = mount([q1, q2])

    await p.press('1')
    await p.press(TAB)
    await p.press(ENTER)

    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith({ 'Which posts?': 'Two' }))
    p.unmount()
  })

  it('declines when Cancel is chosen', async () => {
    const p = mount([q1, q2])

    await p.press('1')
    await p.press(TAB)
    await p.press(ARROW_DOWN) // move to Cancel
    await p.press(ENTER)

    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith(null))
    p.unmount()
  })

  it('declines on Esc from the review step', async () => {
    const p = mount([q1, q2])

    await p.press('1')
    await p.press(TAB)
    await p.press(ESC, 200)

    await waitFor(() => expect(p.onAnswer).toHaveBeenCalledWith(null))
    p.unmount()
  })
})
