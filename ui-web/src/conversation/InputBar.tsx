import { useStore } from '@nanostores/react'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'

import type {
  ContextUsageResult,
  EffortOptionsResult,
  ModelOptionsResult,
} from '../gateway/protocol.ts'
import { $commands, $notice } from '../state/store.ts'
import { ArrowUpIcon, StopIcon } from '../ui/icons.tsx'
import { ContextMeter } from './ContextMeter.tsx'
import { EffortSelect } from './EffortSelect.tsx'
import { ModelSelect } from './ModelSelect.tsx'
import { PermissionSelect, type ApprovalMode } from './PermissionSelect.tsx'
import css from './InputBar.module.css'

export interface InputBarProps {
  approvalMode?: ApprovalMode
  draft: string
  effort: EffortOptionsResult
  hero?: boolean
  models: ModelOptionsResult
  onApprovalModeChange: (mode: ApprovalMode) => void
  onDraftChange: (text: string) => void
  onEffortChange: (effort: string) => void
  onModelChange: (model: string, provider?: string) => void
  onStop: () => void
  onSubmit: (text: string) => void
  running: boolean
  sessionModel?: string
  sessionProvider?: string
  usage: ContextUsageResult | null
}

/** Slash rows shown at once; more are reachable by typing, not scrolling. */
const MAX_SUGGESTIONS = 40

/**
 * The composer.
 *
 * One card in two positions: centred in the empty state, docked at the bottom
 * of the transcript once a conversation exists. The transition between them is
 * a position move of the same component, never a different control.
 */
export function InputBar({
  approvalMode,
  draft,
  effort,
  hero = false,
  models,
  onApprovalModeChange,
  onDraftChange,
  onEffortChange,
  onModelChange,
  onStop,
  onSubmit,
  running,
  sessionModel,
  sessionProvider,
  usage,
}: InputBarProps) {
  const commands = useStore($commands)
  const notice = useStore($notice)
  const textarea = useRef<HTMLTextAreaElement | null>(null)
  const [highlight, setHighlight] = useState(0)

  // Auto-grow: the textarea is always exactly as tall as its content, and the
  // wrapper above it is the scrollport that caps the height. Measuring needs
  // the height reset first, or scrollHeight only ever grows.
  useLayoutEffect(() => {
    const element = textarea.current

    if (element === null) return

    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [draft, hero])

  const suggestions = useMemo(() => {
    const trimmed = draft.trimStart()

    // Only while the draft IS a command — a slash inside a sentence is text.
    if (!trimmed.startsWith('/') || /\s/.test(trimmed)) return []

    const needle = trimmed.toLowerCase()

    return commands
      .filter(command => command.name.toLowerCase().startsWith(needle))
      .slice(0, MAX_SUGGESTIONS)
  }, [commands, draft])

  useEffect(() => {
    setHighlight(0)
  }, [suggestions.length])

  const accept = useCallback(
    (name: string) => {
      onDraftChange(`${name} `)
      textarea.current?.focus()
    },
    [onDraftChange],
  )

  const submit = useCallback(() => {
    const text = draft.trim()

    if (text === '') return

    onSubmit(text)
    onDraftChange('')
  }, [draft, onDraftChange, onSubmit])

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (suggestions.length > 0) {
        if (event.key === 'ArrowDown') {
          event.preventDefault()
          setHighlight(value => (value + 1) % suggestions.length)

          return
        }

        if (event.key === 'ArrowUp') {
          event.preventDefault()
          setHighlight(value => (value - 1 + suggestions.length) % suggestions.length)

          return
        }

        if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
          const choice = suggestions[highlight]

          if (choice !== undefined) {
            event.preventDefault()
            accept(choice.name)

            return
          }
        }

        if (event.key === 'Escape') {
          event.preventDefault()
          onDraftChange('')

          return
        }
      }

      // Enter sends, Shift+Enter opens a line. IME composition must never
      // submit: the Enter that commits a candidate is not a send.
      if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault()
        submit()
      }
    },
    [accept, highlight, onDraftChange, submit, suggestions],
  )

  return (
    <div className={[css.root, hero ? css.hero : ''].filter(Boolean).join(' ')}>
      {notice.text !== '' && (
        <div
          className={[css.notice, notice.tone === 'error' ? css.noticeError : '']
            .filter(Boolean)
            .join(' ')}
          role="status"
        >
          {notice.text}
        </div>
      )}
      <div className={css.card}>
        {suggestions.length > 0 && (
          <div className={css.popover} role="listbox">
            {suggestions.map((command, index) => (
              <button
                className={css.option}
                data-active={index === highlight ? '' : undefined}
                key={command.name}
                onClick={() => {
                  accept(command.name)
                }}
                onPointerEnter={() => {
                  setHighlight(index)
                }}
                type="button"
              >
                <span className={css.optionName}>{command.name}</span>
                {command.hint !== undefined && (
                  <span className={css.optionHint}>{command.hint}</span>
                )}
                <span className={css.optionDescription}>{command.description}</span>
              </button>
            ))}
          </div>
        )}
        <div className={css.scroll}>
          <textarea
            aria-label="Message ClawCodex"
            className={css.input}
            onChange={event => {
              onDraftChange(event.target.value)
            }}
            onKeyDown={onKeyDown}
            placeholder={running ? 'Queue a follow-up…' : 'Ask ClawCodex to build something…'}
            ref={textarea}
            rows={hero ? 2 : 1}
            spellCheck={false}
            value={draft}
          />
        </div>
        <div className={css.row}>
          <div className={css.modes}>
            <PermissionSelect
              onChange={onApprovalModeChange}
              value={approvalMode ?? 'manual'}
            />
            <ModelSelect
              models={models}
              onChange={onModelChange}
              sessionModel={sessionModel}
              sessionProvider={sessionProvider}
            />
            <EffortSelect onChange={onEffortChange} options={effort} />
          </div>
          <div className={css.trailing}>
            <ContextMeter usage={usage} />
            {/* Stopping the turn is the important action while one is running,
                so it holds the primary seat — but a draft written meanwhile
                still needs a way out that is not "press Enter and hope", so
                the queue button appears beside it the moment there is one. */}
            {running && (
              <button
                aria-label="Stop"
                className={[css.primary, css.stop].join(' ')}
                onClick={onStop}
                title="Stop the current turn"
                type="button"
              >
                <StopIcon size={16} />
              </button>
            )}
            {(!running || draft.trim() !== '') && (
              <button
                aria-label={running ? 'Queue' : 'Send'}
                className={[css.primary, running ? css.queue : ''].filter(Boolean).join(' ')}
                disabled={draft.trim() === ''}
                onClick={submit}
                title={running ? 'Queue for the next turn (Enter)' : 'Send (Enter)'}
                type="button"
              >
                <ArrowUpIcon size={18} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
