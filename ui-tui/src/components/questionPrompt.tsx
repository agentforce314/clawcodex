import { Box, Text, useInput } from '@clawcodex/ink'
import { useMemo, useState } from 'react'

import type { QuestionSpec } from '../gatewayTypes.js'
import { stripAnsi } from '../lib/text.js'
import type { Theme } from '../theme.js'

import { windowItems } from './overlayControls.js'
import { TextInput } from './textInput.js'

/** Sentinel value for the auto-injected free-text row. Never shown to the user
 *  and never sent to the backend — it is replaced by the typed text on submit.
 *  Mirrors upstream's `'__other__'`. */
export const OTHER = '__other__'

/** Placeholder on the free-text row. Upstream never renders the word "Other";
 *  the row shows this instead, dim, until the user types. */
const OTHER_PLACEHOLDER = 'Type something.'

/** Cap on the free-text field so a long answer can't push the frame around. */
const OTHER_COLUMNS = 80

/** Option rows shown at once. The schema advertises 2-4, but
 *  schema_validation.py implements no length keywords, so the bound is a nudge
 *  to the model and not a guarantee. Past roughly this many rows the frame
 *  outgrows the viewport, the renderer drops from a cell diff to a full-frame
 *  repaint on every keystroke, and the focused row can scroll out of sight
 *  entirely. Same windowing every other overlay here uses. */
const VISIBLE_OPTIONS = 8

/** Review-step rows. Order is load-bearing: the digit shortcuts and the
 *  cancel check below are derived from it rather than hardcoded, so adding or
 *  reordering a row cannot silently desync them from what is rendered. */
const SUBMIT_ROWS = ['Submit answers', 'Cancel'] as const
const CANCEL_ROW = SUBMIT_ROWS.indexOf('Cancel')

export type QuestionAnswers = Record<string, string>

/** Every string in a question is MODEL-controlled, and the model may be
 *  relaying text it read from a file or a web page.
 *
 *  `stripAnsi` alone is not enough. Its CONTROL_RE deliberately keeps `\n` and
 *  `\t`, and ink's wrapText splits on `\n` even under `truncate-end` — so one
 *  label renders as SEVERAL rows. Since the digit shortcut picks by array
 *  index, not by rendered row, a label like `"Cancel\n  2. Deploy (safe)"`
 *  paints a convincing fake option 2; pressing 2 selects the real option 2,
 *  which is something else entirely. The user reads one thing and picks
 *  another. Collapse the row-breaking whitespace so one option is one row.
 *
 *  Escape sequences are stripped for the usual reasons too: they can reposition
 *  the cursor, repaint rows the dialog does not own, or emit OSC (clipboard)
 *  sequences, and ink measures width after stripping them so they wreck the
 *  layout. Same treatment the transcript gives model-derived text. */
const safe = (v: unknown) =>
  stripAnsi(String(v ?? ''))
    .replace(/\s+/g, ' ')
    .trim()

// ── Pure helpers (exported for tests; no React, no Ink) ─────────────────

/** Rows in a question's option list: the real options, the auto-injected
 *  free-text row, and — for multiSelect only — the submit row that Enter
 *  needs to land on (in multiSelect, Enter on an option toggles it). */
export function rowCount(question: QuestionSpec): number {
  return (question.options?.length ?? 0) + 1 + (question.multiSelect ? 1 : 0)
}

export function otherIndex(question: QuestionSpec): number {
  return question.options?.length ?? 0
}

export function submitIndex(question: QuestionSpec): number {
  return question.multiSelect ? otherIndex(question) + 1 : -1
}

/** A lone single-select question has no review step: picking an option submits
 *  straight away, and the navigation bar hides its arrows and Submit chip.
 *  Upstream's `hideSubmitTab`. */
export function hideSubmitTab(questions: QuestionSpec[]): boolean {
  return questions.length === 1 && !questions[0]?.multiSelect
}

/** Join multi-select values the way upstream does: `", "`-separated in TOGGLE
 *  order, with the free-text sentinel replaced by its text and moved last. */
export function serializeMultiSelect(values: string[], text: string): string {
  const typed = text.trim()

  return values
    .filter(v => v !== OTHER)
    .concat(typed ? [typed] : [])
    .join(', ')
}

/**
 * Sole writer for the answers map. An empty value DELETES the key rather than
 * storing `''`: every surface treats `''` as unanswered (the ☐ chip, the "not
 * all answered" warning, the review list's filter), so keeping it would submit
 * `{"Q": ""}` for a question the UI had just called unanswered — and the model
 * would be told the user answered it with nothing, which is strictly worse than
 * the empty-submit path that says "proceed autonomously".
 *
 * Reachable by toggling a multiSelect option on then off, or by typing into the
 * free-text row and clearing it again.
 */
export function withAnswer(base: QuestionAnswers, question: string, value: string): QuestionAnswers {
  const next = { ...base }

  if (value) {
    next[question] = value
  } else {
    delete next[question]
  }

  return next
}

export type QuestionKey = {
  downArrow?: boolean
  escape?: boolean
  leftArrow?: boolean
  return?: boolean
  rightArrow?: boolean
  shift?: boolean
  tab?: boolean
  upArrow?: boolean
}

export type QuestionAction =
  | { kind: 'cancel' }
  | { kind: 'move'; delta: -1 | 1 }
  | { kind: 'noop' }
  | { kind: 'pick'; index: number }
  | { kind: 'question'; delta: -1 | 1 }
  | { kind: 'submit' }
  | { kind: 'typing' }

/**
 * Pure key-dispatch for the question dialog — exported so the regression matrix
 * (Esc, digits, Enter, Tab, arrows, the multiSelect submit row) is testable
 * without mounting React + Ink + a fake stdin. The component just maps the
 * action onto its own state setters. Same shape as `approvalAction` in
 * prompts.tsx, for the same reason.
 *
 * `typing` short-circuits everything but Esc: while the free-text row is
 * focused the TextInput owns the keyboard, including digits, so that "3" can
 * be typed into an answer instead of jumping to option 3.
 */
export function questionAction(
  ch: string,
  key: QuestionKey,
  sel: number,
  ctx: { multiSelect: boolean; optionCount: number; rows: number; typing: boolean }
): QuestionAction {
  if (key.escape) {
    return { kind: 'cancel' }
  }

  if (ctx.typing) {
    return { kind: 'noop' }
  }

  if (key.tab) {
    return { kind: 'question', delta: key.shift ? -1 : 1 }
  }

  if (key.leftArrow) {
    return { kind: 'question', delta: -1 }
  }

  if (key.rightArrow) {
    return { kind: 'question', delta: 1 }
  }

  if (key.upArrow) {
    return sel > 0 ? { kind: 'move', delta: -1 } : { kind: 'noop' }
  }

  if (key.downArrow) {
    return sel < ctx.rows - 1 ? { kind: 'move', delta: 1 } : { kind: 'noop' }
  }

  // Digits quick-pick a real option only. The free-text and submit rows are
  // deliberately unreachable this way (upstream numbers them in the frame but
  // does not bind them), so a stray digit can't submit the dialog.
  const n = parseInt(ch, 10)

  if (n >= 1 && n <= ctx.optionCount) {
    return { kind: 'pick', index: n - 1 }
  }

  // Space toggles in multiSelect, matching upstream. It is NOT bound in
  // single-select, where there is nothing to toggle.
  if (ch === ' ' && ctx.multiSelect && sel < ctx.optionCount) {
    return { kind: 'pick', index: sel }
  }

  if (key.return) {
    if (sel === ctx.optionCount) {
      return { kind: 'typing' }
    }

    if (ctx.multiSelect && sel === ctx.optionCount + 1) {
      return { kind: 'submit' }
    }

    return { kind: 'pick', index: sel }
  }

  return { kind: 'noop' }
}

/** Chrome around a chip's text: a leading space, the checkbox, a space, and a
 *  trailing space — so a chip's rendered width is `CHIP_CHROME + text.length`. */
const CHIP_CHROME = 4

/** Below this a chip cannot show enough text to identify a question, so it is
 *  dropped entirely rather than rendered as a stub. */
const CHIP_MIN = CHIP_CHROME + 2

/**
 * Width-fit the navigation bar chips (upstream QuestionNavigationBar). Each
 * chip renders as `" ☐ Header "`, so its ideal width is `CHIP_CHROME + header.length`.
 * When they all fit, headers render verbatim; otherwise the FOCUSED chip keeps
 * up to half the available room and the rest split what is left.
 *
 * Two invariants the naive version broke:
 *  - The focused chip is never dropped. `4 + len` clamped to `available / 2`
 *    could fall under CHIP_CHROME, making `slice` return '' — and the caller
 *    renders nothing for an empty label, so the one chip that must be visible
 *    was the one that disappeared.
 *  - The result actually fits. An unconditional floor on the other chips could
 *    total wider than `columns` and wrap the bar; chips that cannot make
 *    CHIP_MIN are elided instead.
 *
 * Returns the per-chip header text (already truncated), or `''` for a chip that
 * cannot be shown at all.
 */
export function navChipLabels(headers: string[], current: number, columns: number): string[] {
  const fixed = '← '.length + ' →'.length + ' ✔ Submit '.length
  const available = columns - fixed

  // Not even one readable chip fits: show a stub of the focused one so the bar
  // still says where you are, and drop the rest.
  if (available < CHIP_MIN + 1) {
    return headers.map((h, i) => (i === current ? h.slice(0, 3) : ''))
  }

  const ideal = headers.reduce((sum, h) => sum + CHIP_CHROME + h.length, 0)

  if (ideal <= available) {
    return [...headers]
  }

  const desired = Math.min(CHIP_CHROME + (headers[current]?.length ?? 0), Math.floor(available / 2))
  const currentWidth = Math.max(CHIP_MIN + 1, desired)
  const others = Math.max(headers.length - 1, 1)
  const otherWidth = Math.floor((available - currentWidth) / others)

  return headers.map((h, i) => {
    if (i === current) {
      return h.slice(0, currentWidth - CHIP_CHROME)
    }

    return otherWidth >= CHIP_MIN ? h.slice(0, otherWidth - CHIP_CHROME) : ''
  })
}

// ── Component ──────────────────────────────────────────────────────────

/** Full-width horizontal rule. A top-only border stretches to the container,
 *  so the rule spans the padded PromptZone exactly, without this component
 *  having to know the container's padding to subtract it from `cols`. */
function Rule({ t }: { t: Theme }) {
  return (
    <Box
      borderBottom={false}
      borderColor={t.color.muted}
      borderLeft={false}
      borderRight={false}
      borderStyle="single"
      borderTop
    />
  )
}

interface QuestionPromptProps {
  cols?: number
  /** `null` answers = the user dismissed the dialog (backend maps it to a
   *  decline, which is not the same as submitting nothing). */
  onAnswer: (answers: null | QuestionAnswers) => void
  questions: QuestionSpec[]
  t: Theme
}

export function QuestionPrompt({ cols = 80, onAnswer, questions: raw, t }: QuestionPromptProps) {
  // Normalize model-controlled text ONCE, up front. Every downstream use --
  // identity comparisons (`chosen.includes(opt.label)`), the answers map that
  // travels back to the model, and the rendered rows -- then agrees on the same
  // string. Sanitizing at render only would leave the raw label in `answers`.
  const questions = useMemo(
    () =>
      raw.map(q => ({
        ...q,
        header: safe(q.header),
        question: safe(q.question),
        options: (q.options ?? []).map(o => ({
          ...o,
          description: safe(o.description),
          label: safe(o.label)
        }))
      })),
    [raw]
  )

  const [index, setIndex] = useState(0)
  const [sel, setSel] = useState(0)
  const [answers, setAnswers] = useState<QuestionAnswers>({})
  // Multi-select toggles, kept as an ordered array per question: upstream
  // serializes in toggle order, not option order.
  const [picked, setPicked] = useState<Record<string, string[]>>({})
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [typing, setTyping] = useState(false)
  const [submitSel, setSubmitSel] = useState(0)

  const hideSubmit = hideSubmitTab(questions)
  const maxIndex = hideSubmit ? questions.length - 1 : questions.length
  const inSubmitView = index === questions.length
  const question = questions[index]

  const answered = (q: QuestionSpec) => !!answers[q.question]

  const allAnswered = questions.every(answered)

  const goto = (next: number) => {
    setIndex(Math.max(0, Math.min(maxIndex, next)))
    setSel(0)
    setTyping(false)
  }

  const commit = (next: QuestionAnswers) => {
    setAnswers(next)

    // A lone single-select question has no review step — picking IS submitting.
    if (hideSubmit) {
      onAnswer(next)

      return
    }

    goto(index + 1)
  }

  const pickSingle = (label: string) => {
    if (!question) {
      return
    }

    commit(withAnswer(answers, question.question, label))
  }

  const toggleMulti = (label: string) => {
    if (!question) {
      return
    }

    const key = question.question
    const current = picked[key] ?? []
    // Append on add so the array preserves toggle order.
    const next = current.includes(label) ? current.filter(v => v !== label) : [...current, label]
    const nextPicked = { ...picked, [key]: next }

    setPicked(nextPicked)
    setAnswers(withAnswer(answers, key, serializeMultiSelect(next, texts[key] ?? '')))
  }

  const setOtherText = (value: string) => {
    if (!question) {
      return
    }

    const key = question.question
    const nextTexts = { ...texts, [key]: value }

    setTexts(nextTexts)

    // Typing into the free-text row auto-checks it; clearing it unchecks.
    // Single-select rows have nothing to check, so this is multiSelect-only.
    if (question.multiSelect) {
      const current = picked[key] ?? []

      const next = value.trim()
        ? current.includes(OTHER)
          ? current
          : [...current, OTHER]
        : current.filter(v => v !== OTHER)

      const nextPicked = { ...picked, [key]: next }

      setPicked(nextPicked)
      setAnswers(withAnswer(answers, key, serializeMultiSelect(next, value)))
    }
  }

  const submitOther = () => {
    if (!question) {
      return
    }

    const typed = (texts[question.question] ?? '').trim()

    // DELIBERATE DIVERGENCE from upstream, which cancels the WHOLE dialog when
    // Enter lands on an empty free-text row (select.tsx onEmptyInputSubmit).
    // That is a footgun — an accidental Enter throws away every answer already
    // given. Here it simply does nothing.
    if (!typed) {
      return
    }

    if (question.multiSelect) {
      setTyping(false)

      return
    }

    pickSingle(typed)
  }

  // Main dispatch. Gated off while the free-text row has focus so the
  // TextInput receives those keystrokes instead (Ink runs every mounted
  // useInput — there is no focus stack).
  useInput(
    (ch, key) => {
      if (inSubmitView) {
        if (key.escape) {
          onAnswer(null)
        } else if (key.upArrow) {
          setSubmitSel(0)
        } else if (key.downArrow) {
          setSubmitSel(1)
        } else if (key.leftArrow || (key.tab && key.shift)) {
          goto(index - 1)
        } else {
          const digit = parseInt(ch, 10)
          const picked = digit >= 1 && digit <= SUBMIT_ROWS.length ? digit - 1 : key.return ? submitSel : -1

          if (picked >= 0) {
            onAnswer(picked === CANCEL_ROW ? null : answers)
          }
        }

        return
      }

      if (!question) {
        return
      }

      const optionCount = question.options?.length ?? 0

      const action = questionAction(ch, key, sel, {
        multiSelect: !!question.multiSelect,
        optionCount,
        rows: rowCount(question),
        typing
      })

      switch (action.kind) {
        case 'cancel':
          onAnswer(null)

          break

        case 'move':
          setSel(s => s + action.delta)

          break
        case 'pick': {
          const label = question.options?.[action.index]?.label

          if (label === undefined) {
            break
          }

          question.multiSelect ? toggleMulti(label) : pickSingle(label)

          break
        }

        case 'question':
          goto(index + action.delta)

          break

        case 'submit':
          goto(index + 1)

          break

        case 'typing':
          setSel(optionCount)
          setTyping(true)

          break

        default:
          break
      }
    },
    { isActive: !typing }
  )

  // Esc inside the free-text row means "back to the option list", not "cancel
  // the dialog". Needs its own handler because TextInput deliberately passes
  // Esc through (textInput.tsx shouldPassThroughToGlobalHandler) rather than
  // swallowing it.
  useInput(
    (_ch, key) => {
      if (key.escape) {
        setTyping(false)
      }
    },
    { isActive: typing }
  )

  const navBar = (() => {
    const headers = questions.map((q, i) => q.header || `Q${i + 1}`)
    const labels = navChipLabels(headers, Math.min(index, questions.length - 1), cols)
    // Upstream hides the arrows entirely for a lone single-select question
    // (hideSubmit already implies there is exactly one).
    const showArrows = !hideSubmit

    return (
      <Box flexDirection="row" marginBottom={1}>
        {showArrows ? <Text color={index === 0 ? t.color.muted : undefined}>{'← '}</Text> : null}
        {questions.map((q, i) =>
          labels[i] ? (
            <Text inverse={i === index} key={i}>
              {' '}
              {answered(q) ? '☒' : '☐'} {labels[i]}{' '}
            </Text>
          ) : null
        )}
        {hideSubmit ? null : <Text inverse={inSubmitView}> ✔ Submit </Text>}
        {showArrows ? <Text color={inSubmitView ? t.color.muted : undefined}>{' →'}</Text> : null}
      </Box>
    )
  })()

  if (inSubmitView) {
    const entries = Object.entries(answers).filter(([, a]) => !!a)

    return (
      <Box flexDirection="column">
        <Rule t={t} />
        {navBar}
        <Text bold>Review your answers</Text>

        <Box flexDirection="column" marginTop={1}>
          {allAnswered ? null : (
            <Text color={t.color.warn}>⚠ You have not answered all questions</Text>
          )}
          {entries.map(([q, a]) => (
            <Box flexDirection="column" key={q} marginLeft={1}>
              <Text>● {safe(q)}</Text>
              <Box marginLeft={2}>
                <Text color={t.color.ok}>→ {safe(a)}</Text>
              </Box>
            </Box>
          ))}
          <Text color={t.color.muted}>Ready to submit your answers?</Text>
          {SUBMIT_ROWS.map((label, i) => (
            <Text key={label}>
              <Text color={submitSel === i ? t.color.permission : undefined}>{submitSel === i ? '❯' : ' '} </Text>
              <Text color={t.color.muted} dimColor>
                {i + 1}.{' '}
              </Text>
              <Text color={submitSel === i ? t.color.permission : undefined}>{label}</Text>
            </Text>
          ))}
        </Box>

        <Rule t={t} />
        <Text color={t.color.muted} dimColor>
          Enter to submit · ↑/↓ to navigate · ← to go back · Esc to cancel
        </Text>
      </Box>
    )
  }

  if (!question) {
    return null
  }

  const options = question.options ?? []
  // Clamp the cursor into the option range for windowing: the free-text and
  // submit rows sit past the last option and must not scroll the list.
  const shown = windowItems(options, Math.min(sel, Math.max(options.length - 1, 0)), VISIBLE_OPTIONS)
  const other = otherIndex(question)
  const submit = submitIndex(question)
  const key = question.question
  const chosen = picked[key] ?? []
  const text = texts[key] ?? ''
  const single = answers[key]

  return (
    <Box flexDirection="column">
      <Rule t={t} />
      {navBar}
      <Text bold>{question.question}</Text>

      <Box flexDirection="column" marginTop={1}>
        {shown.offset > 0 ? (
          <Text color={t.color.muted} dimColor>
            {`  ↑ ${shown.offset} more`}
          </Text>
        ) : null}
        {shown.items.map((opt, idx) => {
          const i = shown.offset + idx
          const at = sel === i
          const isOn = question.multiSelect ? chosen.includes(opt.label) : single === opt.label

          return (
            <Box flexDirection="column" key={i}>
              <Text wrap="truncate-end">
                <Text color={at ? t.color.permission : undefined}>{at ? '❯' : ' '} </Text>
                <Text color={t.color.muted} dimColor>
                  {i + 1}.{' '}
                </Text>
                {question.multiSelect ? (
                  <Text color={isOn ? t.color.ok : undefined}>[{isOn ? '✔' : ' '}] </Text>
                ) : null}
                <Text bold={at} color={isOn ? t.color.ok : at ? t.color.permission : undefined}>
                  {opt.label}
                </Text>
                {!question.multiSelect && isOn ? <Text color={t.color.ok}> ✔</Text> : null}
              </Text>
              {opt.description ? (
                <Box marginLeft={5}>
                  <Text color={t.color.muted} dimColor wrap="truncate-end">
                    {opt.description}
                  </Text>
                </Box>
              ) : null}
            </Box>
          )
        })}
        {shown.offset + VISIBLE_OPTIONS < options.length ? (
          <Text color={t.color.muted} dimColor>
            {`  ↓ ${options.length - shown.offset - VISIBLE_OPTIONS} more`}
          </Text>
        ) : null}

        {/* Auto-injected free-text row. Upstream never shows the word "Other" —
            the row is the placeholder until something is typed.

            A Box, not a Text: TextInput renders a Box internally, and Ink
            throws "<Box> can't be nested inside <Text>" the moment this row
            switches into edit mode. */}
        <Box flexDirection="row">
          <Text color={sel === other ? t.color.permission : undefined}>{sel === other ? '❯' : ' '} </Text>
          <Text color={t.color.muted} dimColor>
            {other + 1}.{' '}
          </Text>
          {question.multiSelect ? (
            <Text color={chosen.includes(OTHER) ? t.color.ok : undefined}>
              [{chosen.includes(OTHER) ? '✔' : ' '}]{' '}
            </Text>
          ) : null}
          {typing ? (
            <TextInput
              columns={Math.max(20, Math.min(OTHER_COLUMNS, cols - 8))}
              focus={typing}
              onChange={setOtherText}
              onSubmit={submitOther}
              value={text}
            />
          ) : (
            <Text color={text ? undefined : t.color.muted} dimColor={!text} wrap="truncate-end">
              {text || OTHER_PLACEHOLDER}
            </Text>
          )}
        </Box>

        {submit >= 0 ? (
          <Text>
            <Text color={sel === submit ? t.color.permission : undefined}>{sel === submit ? '❯' : ' '} </Text>
            <Text bold>
              {'   '}
              {index === questions.length - 1 ? 'Submit' : 'Next'}
            </Text>
          </Text>
        ) : null}
      </Box>

      <Rule t={t} />
      <Text color={t.color.muted} dimColor>
        {typing
          ? 'Enter to accept · Esc to go back'
          : questions.length === 1
            ? `Enter to select · ↑/↓ to navigate${question.multiSelect ? ' · Space to toggle' : ''} · Esc to cancel`
            : `Enter to select · Tab/Arrow keys to navigate${question.multiSelect ? ' · Space to toggle' : ''} · Esc to cancel`}
      </Text>
    </Box>
  )
}
