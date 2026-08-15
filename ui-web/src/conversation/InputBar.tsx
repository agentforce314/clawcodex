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
import { attachImage, searchFiles } from '../state/actions.ts'
import { $commands, $notice } from '../state/store.ts'
import { ArrowUpIcon, PlusIcon, StopIcon, XIcon } from '../ui/icons.tsx'
import { ContextMeter } from './ContextMeter.tsx'
import {
  insertPlaceholder,
  liveAttachments,
  removePlaceholder,
  type Attachment,
} from './attachments.ts'
import { applyMention, mentionAt, type MentionToken } from './mentions.ts'
import { EffortSelect } from './EffortSelect.tsx'
import { ModelSelect } from './ModelSelect.tsx'
import { PermissionSelect, type ApprovalMode } from './PermissionSelect.tsx'
import css from './InputBar.module.css'

export interface InputBarProps {
  approvalMode?: ApprovalMode
  draft: string
  effort: EffortOptionsResult
  hero?: boolean
  /** False when the session's model would 400 on an image. */
  vision?: boolean
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
  vision = true,
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

  // The @ mention being typed, tracked from the CARET: unlike a slash command,
  // a mention can sit anywhere in the draft and a message can hold several.
  const [mention, setMention] = useState<MentionToken | null>(null)
  const [files, setFiles] = useState<string[]>([])

  const syncMention = useCallback((element: HTMLTextAreaElement | null) => {
    if (element === null) return

    setMention(mentionAt(element.value, element.selectionStart))
  }, [])

  // Debounced: a mention is typed one keystroke at a time, and every query
  // costs an `rg --files` on the backend. 120ms is under the gap between
  // keystrokes for anyone typing a path, so the menu still feels immediate.
  useEffect(() => {
    if (mention === null) {
      setFiles([])

      return
    }

    let live = true
    const timer = setTimeout(() => {
      void searchFiles(mention.query).then(result => {
        // A reply that lands after the token moved on describes a query the
        // user is no longer typing.
        if (live) setFiles(result)
      })
    }, 120)

    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [mention])

  useEffect(() => {
    setHighlight(0)
  }, [suggestions.length, files.length])

  const accept = useCallback(
    (name: string) => {
      onDraftChange(`${name} `)
      textarea.current?.focus()
    },
    [onDraftChange],
  )

  // Every image the session has accepted this composer session. What actually
  // SENDS is whatever the draft still claims — see attachments.ts.
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const picker = useRef<HTMLInputElement | null>(null)

  const shown = useMemo(() => liveAttachments(draft, attachments), [attachments, draft])

  // Object URLs outlive the component unless revoked, and a long session can
  // paste a lot of screenshots.
  useEffect(
    () => () => {
      for (const item of attachments) URL.revokeObjectURL(item.url)
    },
    [attachments],
  )

  const attach = useCallback(
    async (file: File | Blob, name: string) => {
      const id = await attachImage(file, name)

      if (id === null) return

      const element = textarea.current
      const caret = element === null ? draft.length : element.selectionStart
      const next = insertPlaceholder(draft, caret, id)

      setAttachments(current => [...current, { id, name, url: URL.createObjectURL(file) }])
      onDraftChange(next.text)

      requestAnimationFrame(() => {
        const live = textarea.current

        if (live === null) return

        live.focus()
        live.setSelectionRange(next.caret, next.caret)
      })
    },
    [draft, onDraftChange],
  )

  /**
   * Say why an image was ignored, rather than swallowing it in silence.
   *
   * A paste that does nothing visible reads as a broken paste. Naming the
   * model is the one piece of information that makes it actionable.
   */
  const refuseImage = useCallback(() => {
    const name = sessionModel === undefined || sessionModel === '' ? 'This model' : sessionModel

    $notice.set({ text: `${name} cannot read images — switch models to attach one.`, tone: 'error' })
  }, [sessionModel])

  const dropAttachment = useCallback(
    (id: number) => {
      onDraftChange(removePlaceholder(draft, id))
      textarea.current?.focus()
    },
    [draft, onDraftChange],
  )

  const acceptFile = useCallback(
    (path: string) => {
      if (mention === null) return

      const next = applyMention(draft, mention, path)

      onDraftChange(next.text)
      setMention(null)
      setFiles([])

      // The caret has to be restored after React commits the new value, or it
      // snaps to the end and the next mention in the sentence is unreachable.
      requestAnimationFrame(() => {
        const element = textarea.current

        if (element === null) return

        element.focus()
        element.setSelectionRange(next.caret, next.caret)
      })
    },
    [draft, mention, onDraftChange],
  )

  const submit = useCallback(() => {
    const text = draft.trim()

    if (text === '') return

    onSubmit(text)
    onDraftChange('')
  }, [draft, onDraftChange, onSubmit])

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // The file menu is checked first: it is anchored at the caret, so it is
      // the one the user is looking at when both could be open.
      if (files.length > 0 && mention !== null) {
        if (event.key === 'ArrowDown') {
          event.preventDefault()
          setHighlight(value => (value + 1) % files.length)

          return
        }

        if (event.key === 'ArrowUp') {
          event.preventDefault()
          setHighlight(value => (value - 1 + files.length) % files.length)

          return
        }

        if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
          const choice = files[highlight]

          if (choice !== undefined && !event.nativeEvent.isComposing) {
            event.preventDefault()
            acceptFile(choice)

            return
          }
        }

        if (event.key === 'Escape') {
          // Dismiss the menu only. Clearing the draft — what Escape does to a
          // half-typed slash command — would throw away a sentence over a
          // mention the user decided against.
          event.preventDefault()
          setMention(null)
          setFiles([])

          return
        }
      }

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
    [accept, acceptFile, files, highlight, mention, onDraftChange, submit, suggestions],
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
        {mention !== null && files.length > 0 && (
          <div className={css.popover} role="listbox">
            {files.map((path, index) => (
              <button
                className={css.option}
                data-active={index === highlight ? '' : undefined}
                key={path}
                onClick={() => {
                  acceptFile(path)
                }}
                onPointerEnter={() => {
                  setHighlight(index)
                }}
                type="button"
              >
                <span className={css.optionName}>{path.split('/').pop()}</span>
                <span className={css.optionDescription}>{path}</span>
              </button>
            ))}
          </div>
        )}
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
        {shown.length > 0 && (
          <div className={css.attachments}>
            {shown.map(item => (
              <div className={css.thumb} key={item.id}>
                <img alt={item.name} src={item.url} />
                <button
                  aria-label={`Remove ${item.name}`}
                  className={css.thumbRemove}
                  onClick={() => {
                    dropAttachment(item.id)
                  }}
                  title="Remove this image"
                  type="button"
                >
                  <XIcon size={10} />
                </button>
                <span className={css.thumbTag}>#{item.id}</span>
              </div>
            ))}
          </div>
        )}
        <div className={css.scroll}>
          <textarea
            aria-label="Message ClawCodex"
            className={css.input}
            onDragOver={event => {
              // Accept the drag even when the model cannot read images: the
              // drop handler is where the reason gets explained, and refusing
              // here would make the file bounce with no explanation at all.
              if (event.dataTransfer.types.includes('Files')) event.preventDefault()
            }}
            onDrop={event => {
              const file = [...event.dataTransfer.files].find(item =>
                item.type.startsWith('image/'),
              )

              if (file === undefined) return

              event.preventDefault()

              if (!vision) {
                refuseImage()

                return
              }

              void attach(file, file.name)
            }}
            onPaste={event => {
              // Only take over when an image is actually on the clipboard; a
              // normal text paste must keep working.
              const item = [...event.clipboardData.items].find(entry =>
                entry.type.startsWith('image/'),
              )

              if (item === undefined) return

              const file = item.getAsFile()

              if (file === null) return

              event.preventDefault()

              // A model that cannot read images gets told so. Attaching anyway
              // is a hard 400 that kills the turn.
              if (!vision) {
                refuseImage()

                return
              }
              void attach(file, file.name === '' ? 'pasted-image.png' : file.name)
            }}
            onChange={event => {
              onDraftChange(event.target.value)
              syncMention(event.target)
            }}
            // Clicking or arrowing through the draft moves the caret without
            // changing the text, and a mention is defined by where the caret
            // is — so the token has to be re-read on selection too.
            onKeyDown={onKeyDown}
            onSelect={event => {
              syncMention(event.currentTarget)
            }}
            placeholder={running ? 'Queue a follow-up…' : 'Ask ClawCodex to build something…'}
            ref={textarea}
            rows={hero ? 2 : 1}
            spellCheck={false}
            value={draft}
          />
        </div>
        <div className={css.row}>
          <div className={css.modes}>
            {vision && (
              <>
            <input
              accept="image/*"
              className={css.hiddenPicker}
              onChange={event => {
                const file = event.target.files?.[0]

                if (file !== undefined) void attach(file, file.name)
                // Clear it, or picking the same file twice is inert.
                event.target.value = ''
              }}
              ref={picker}
              tabIndex={-1}
              type="file"
            />
            <button
              aria-label="Attach an image"
              className={css.attachButton}
              onClick={() => {
                picker.current?.click()
              }}
              title="Attach an image"
              type="button"
            >
              <PlusIcon size={14} />
            </button>
              </>
            )}
            <PermissionSelect
              // Passed through undefined until session.info reports a mode:
              // PermissionSelect displays the Full Access default but still
              // treats every pick as a real change while the mode is unknown.
              onChange={onApprovalModeChange}
              value={approvalMode}
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
