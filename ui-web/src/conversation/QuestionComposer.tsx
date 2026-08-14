import { useCallback, useEffect, useMemo, useState } from 'react'

import type { PendingQuestion, QuestionRequestPayload } from '../gateway/protocol.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { CheckIcon, ChevronRightIcon, HelpIcon, XIcon } from '../ui/icons.tsx'
import css from './QuestionComposer.module.css'

interface Draft {
  custom: string
  selected: string[]
  skipped: boolean
}

/**
 * Split the "(Recommended)" suffix the tool asks callers to append.
 *
 * Display only — the returned `value` keeps the original label, because that
 * string is what gets sent back as the user's answer.
 */
export function parseRecommended(label: string): { recommended: boolean; text: string } {
  const match = /^(.*?)\s*\((?:recommended)\)\s*$/i.exec(label)

  return match === null
    ? { recommended: false, text: label }
    : { recommended: true, text: match[1] ?? label }
}

/**
 * Fold one question's draft into the single string the tool expects, or
 * `undefined` when the question should carry no answer at all.
 *
 * A skipped question is *omitted* rather than answered with an empty string:
 * the tool renders the answer map verbatim for the model, and `"question": ""`
 * reads as the user having said something empty rather than nothing.
 */
export function answerOf(draft: Draft): string | undefined {
  if (draft.skipped) return undefined

  const parts = [...draft.selected, draft.custom.trim()].filter(part => part !== '')

  return parts.length === 0 ? undefined : parts.join(', ')
}

/** Whether the draft carries enough to move on (answered, or deliberately skipped). */
function settled(draft: Draft): boolean {
  return draft.skipped || answerOf(draft) !== undefined
}

/** Build the answer map from every draft, keyed by question text. */
export function answersOf(
  questions: PendingQuestion[],
  drafts: Draft[],
): Record<string, string> {
  const answers: Record<string, string> = {}

  questions.forEach((question, index) => {
    const draft = drafts[index]

    if (draft === undefined) return

    const answer = answerOf(draft)

    if (answer !== undefined) answers[question.question] = answer
  })

  return answers
}

export interface QuestionComposerProps {
  onRespond: (action: 'decline' | 'submit', answers: Record<string, string>) => void
  request: QuestionRequestPayload
}

/**
 * The agent's questions, as a composer takeover.
 *
 * Same seat as the permission ask, and for the same reason: the agent's worker
 * thread is blocked on this answer, so there is nothing else to type. One
 * question at a time rather than a stacked form — a set can run to four, each
 * with several options and prose, which is taller than the composer seat and
 * would push the actions out of reach.
 */
export function QuestionComposer({ onRespond, request }: QuestionComposerProps) {
  const questions = request.questions
  const [index, setIndex] = useState(0)
  const [drafts, setDrafts] = useState<Draft[]>(() =>
    questions.map(() => ({ custom: '', selected: [], skipped: false })),
  )
  const [error, setError] = useState<string | null>(null)

  // A fresh request reuses this component; reset rather than carrying the
  // previous set's answers into it.
  useEffect(() => {
    setIndex(0)
    setDrafts(questions.map(() => ({ custom: '', selected: [], skipped: false })))
    setError(null)
  }, [questions])

  const question = questions[index]
  const draft = drafts[index] ?? { custom: '', selected: [], skipped: false }
  const last = index === questions.length - 1
  const multi = question?.multi_select === true

  const decline = useCallback(() => {
    onRespond('decline', {})
  }, [onRespond])

  // Escape declines. The agent is blocked until this resolves, so the panel
  // has to answer the gesture people use to dismiss things — and declining is
  // a real answer the tool reports as "User declined", not a silent drop.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') decline()
    }

    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [decline])

  const patch = useCallback(
    (update: (current: Draft) => Draft) => {
      setDrafts(current => current.map((item, at) => (at === index ? update(item) : item)))
      setError(null)
    },
    [index],
  )

  const submit = useCallback(
    (values: Draft[]) => {
      const missing = values.findIndex(item => !settled(item))

      if (missing >= 0) {
        setIndex(missing)
        setError('Choose an option, type an answer, or skip this question.')

        return
      }

      onRespond('submit', answersOf(questions, values))
    },
    [onRespond, questions],
  )

  const choose = (label: string) => {
    patch(current => {
      if (multi) {
        return {
          ...current,
          selected: current.selected.includes(label)
            ? current.selected.filter(item => item !== label)
            : [...current.selected, label],
          skipped: false,
        }
      }

      // Single-select: picking an option replaces both the previous pick and
      // any typed answer, so the two inputs cannot silently combine.
      return { custom: '', selected: [label], skipped: false }
    })
  }

  const advance = () => {
    if (!settled(draft)) {
      setError('Choose an option, type an answer, or skip this question.')

      return
    }

    if (last) {
      submit(drafts)

      return
    }

    setIndex(current => current + 1)
    setError(null)
  }

  const skip = () => {
    const next = drafts.map((item, at) =>
      at === index ? { custom: '', selected: [], skipped: true } : item,
    )

    setDrafts(next)
    setError(null)

    if (last) submit(next)
    else setIndex(current => current + 1)
  }

  const options = useMemo(
    () => (question?.options ?? []).map(option => ({ ...option, ...parseRecommended(option.label) })),
    [question],
  )

  if (question === undefined) return null

  const titleId = `question-${request.request_id}-${String(index)}`

  return (
    <div className={css.root}>
      <section aria-labelledby={titleId} className={css.card} role="dialog">
        <div className={css.strip}>
          <HelpIcon size={14} />
          <span className={css.stripLabel}>
            {questions.length === 1
              ? 'The agent has a question'
              : `Question ${String(index + 1)} of ${String(questions.length)}`}
          </span>
          <button
            aria-label="Decline to answer"
            className={css.close}
            onClick={decline}
            title="Decline to answer (Esc)"
            type="button"
          >
            <XIcon size={14} />
          </button>
        </div>

        <div className={css.body}>
          {question.header !== undefined && question.header !== '' && (
            <div className={css.eyebrow}>{question.header}</div>
          )}
          <h2 className={css.title} id={titleId}>
            {question.question}
          </h2>

          {options.length > 0 && (
            <div className={css.options} role={multi ? 'group' : 'radiogroup'}>
              {options.map((option, at) => {
                const picked = draft.selected.includes(option.label)
                // Models routinely echo the label as the description; a line
                // that repeats the one above it is noise, so it is dropped
                // rather than rendered twice.
                const hint =
                  option.description !== undefined &&
                  option.description.trim() !== '' &&
                  option.description.trim() !== option.label.trim() &&
                  option.description.trim() !== option.text.trim()
                const hintId = `${titleId}-hint-${String(at)}`

                return (
                  <button
                    aria-checked={picked}
                    // The accessible name is the label verbatim — which is also
                    // the answer value — rather than the rendered content, so a
                    // screen reader is not read the index badge and the whole
                    // description as the option's identity.
                    aria-describedby={hint ? hintId : undefined}
                    aria-label={option.label}
                    className={[css.option, picked ? css.optionPicked : ''].filter(Boolean).join(' ')}
                    key={option.label}
                    onClick={() => {
                      choose(option.label)
                    }}
                    role={multi ? 'checkbox' : 'radio'}
                    type="button"
                  >
                    <span className={[css.mark, picked ? css.markOn : ''].filter(Boolean).join(' ')}>
                      {picked ? <CheckIcon size={11} /> : multi ? null : String(at + 1)}
                    </span>
                    <span className={css.optionText}>
                      <span className={css.optionLabel}>
                        {option.text}
                        {option.recommended && <span className={css.badge}>Recommended</span>}
                      </span>
                      {hint && (
                        <span className={css.optionHint} id={hintId}>
                          {option.description}
                        </span>
                      )}
                    </span>
                  </button>
                )
              })}
            </div>
          )}

          <input
            aria-label={options.length === 0 ? 'Your answer' : 'A different answer'}
            className={css.custom}
            onChange={event => {
              const value = event.target.value

              patch(current => ({
                ...current,
                custom: value,
                // Typing replaces a single-select pick for the same reason
                // picking clears the text; a multi-select answer is additive,
                // so its checkmarks stand.
                selected: multi ? current.selected : [],
                skipped: false,
              }))
            }}
            onKeyDown={event => {
              if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
              event.preventDefault()
              advance()
            }}
            placeholder={options.length === 0 ? 'Type your answer…' : 'Or type something else…'}
            value={draft.custom}
          />

          {error !== null && <div className={css.error}>{error}</div>}
        </div>

        <div className={css.actions}>
          <div className={css.actionsLeft}>
            {index > 0 && (
              <Button
                onClick={() => {
                  setIndex(current => current - 1)
                  setError(null)
                }}
                size="sm"
                variant="ghost"
              >
                Back
              </Button>
            )}
            <Button onClick={skip} size="sm" variant="ghost">
              Skip
            </Button>
          </div>
          <Button onClick={advance} size="sm" variant="primary">
            {last ? 'Send answers' : 'Next'}
            {!last && (
              <span className={css.nextIcon}>
                <ChevronRightIcon size={12} />
              </span>
            )}
          </Button>
        </div>
      </section>
    </div>
  )
}
