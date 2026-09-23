import { useStore } from '@nanostores/react'
import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from 'react'

import type {
  ContextUsageResult,
  EffortOptionsResult,
  ModelOptionsResult,
} from '../gateway/protocol.ts'
import { attachFile, attachImage, searchFiles } from '../state/actions.ts'
import { $commands, $notice, $sessionAttaching } from '../state/store.ts'
import { ArrowUpIcon, PlusIcon, SlashSquareIcon, StopIcon, XIcon } from '../ui/icons.tsx'
import { FileTypeIcon } from '../ui/primitives/FileTypeIcon.tsx'
import { ContextMeter } from './ContextMeter.tsx'
import {
  insertPlaceholder,
  fileExtension,
  formatBytes,
  liveAttachments,
  removePlaceholder,
  type Attachment,
  type AttachmentKind,
} from './attachments.ts'
import { aliasOf, bareName, menuRows, rankRows, sectionRows, type MenuRow } from './command-menu.ts'
import { applyMention, mentionAt, type MentionToken } from './mentions.ts'
import { EffortSelect } from './EffortSelect.tsx'
import { ModelSelect } from './ModelSelect.tsx'
import { PermissionSelect, type ApprovalMode } from './PermissionSelect.tsx'
import css from './InputBar.module.css'
import pickerCss from './Pickers.module.css'

export interface InputBarProps {
  approvalMode?: ApprovalMode
  draft: string
  effort: EffortOptionsResult
  hero?: boolean
  /** Nano mode (docs/nano.md) — renders a chip beside the model, like the TUI's. */
  nano?: boolean
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

/** The menu's design height: fits the two headings and a dozen rows. */
const MENU_MAX_HEIGHT = 400

/** Clearance the menu keeps from the top of the window when it is clamped. */
const MENU_SAFE_MARGIN = 12

/** DOM id of one option row (the `aria-activedescendant` target). */
function optionId(index: number): string {
  return `cc-command-option-${String(index)}`
}

/** The command being typed: the draft is one exactly while a slash opens it and no argument has begun. */
function typedCommand(draft: string): string | null {
  const trimmed = draft.trimStart()

  return trimmed.startsWith('/') && !/\s/.test(trimmed) ? trimmed.slice(1) : null
}

/** Keeps the click on a menu row or the launcher from stealing focus off the textarea. */
function keepFocus(event: MouseEvent): void {
  event.preventDefault()
}

/**
 * The composer.
 *
 * One card in two positions: centred in the empty state, docked at the bottom
 * of the transcript once a conversation exists. The transition between them is
 * a position move of the same component, never a different control.
 *
 * The `+` button and a typed `/` open the same menu: an Add section (the image
 * picker, plan, goal) and a Commands section in usage order, each row with a
 * glyph, a title and the catalog's description. Picking a command that takes
 * an argument claims the draft as `/name `; picking a bare one runs it.
 */
export function InputBar({
  approvalMode,
  draft,
  effort,
  hero = false,
  models,
  nano = false,
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
  const attaching = useStore($sessionAttaching)
  const textarea = useRef<HTMLTextAreaElement | null>(null)
  const card = useRef<HTMLDivElement | null>(null)
  const [highlight, setHighlight] = useState(0)

  // Auto-grow: the textarea is always exactly as tall as its content, and the
  // wrapper above it is the scrollport that caps the height. Measuring needs
  // the height reset first, or scrollHeight only ever grows.
  useLayoutEffect(() => {
    const element = textarea.current

    if (element === null) return

    element.style.height = 'auto'
    element.style.height = `${String(element.scrollHeight)}px`
  }, [draft, hero])

  // The launcher: the `+` opened the menu with nothing typed. It closes on a
  // pick, on Escape, on a pointer outside the card, and on the next keystroke.
  const [launcher, setLauncher] = useState(false)
  const rows = useMemo(() => menuRows(commands, vision), [commands, vision])
  const typed = useMemo(() => typedCommand(draft), [draft])

  const menu = useMemo<MenuRow[] | null>(() => {
    if (typed !== null) {
      const matched = typed === '' ? sectionRows(rows) : rankRows(rows, typed)

      return matched.length > 0 ? matched : null
    }

    return launcher ? sectionRows(rows) : null
  }, [launcher, rows, typed])

  useEffect(() => {
    if (!launcher) return

    const onPointerDown = (event: PointerEvent): void => {
      if (event.target instanceof Node && card.current?.contains(event.target) === true) return

      setLauncher(false)
    }

    document.addEventListener('pointerdown', onPointerDown, true)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [launcher])

  // The menu is bottom-anchored above the card, so its cap is the smaller of
  // the design height and the space above the card, re-measured while open.
  const [menuMaxHeight, setMenuMaxHeight] = useState(MENU_MAX_HEIGHT)

  useLayoutEffect(() => {
    if (menu === null) return

    const measure = (): void => {
      const top = card.current?.getBoundingClientRect().top ?? MENU_MAX_HEIGHT
      const room = Math.floor(top - 4 - MENU_SAFE_MARGIN)

      setMenuMaxHeight(Math.max(88, Math.min(MENU_MAX_HEIGHT, room)))
    }

    measure()
    window.addEventListener('resize', measure)

    return () => {
      window.removeEventListener('resize', measure)
    }
  }, [menu])

  // A gradient at the foot of the list says there is more below it, and goes
  // once the last row is in view.
  const menuViewport = useRef<HTMLDivElement | null>(null)
  const [overflowBelow, setOverflowBelow] = useState(false)

  const updateOverflow = useCallback(() => {
    const viewport = menuViewport.current

    setOverflowBelow(
      viewport !== null && viewport.scrollTop + viewport.clientHeight < viewport.scrollHeight - 1,
    )
  }, [])

  useLayoutEffect(() => {
    updateOverflow()
  }, [menu, menuMaxHeight, updateOverflow])

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
  }, [menu, files.length])

  // Focus stays in the textarea (combobox pattern), so the browser never
  // scrolls the active option into view on keyboard moves — do it here.
  useEffect(() => {
    if (menu === null) return

    const element = document.getElementById(optionId(highlight))

    if (element !== null && typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ block: 'nearest' })
    }
  }, [highlight, menu])

  // Every image and file the session has accepted this composer session.
  // What actually SENDS is whatever the draft still claims — see attachments.ts.
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const attachmentUrls = useRef(new Set<string>())
  const picker = useRef<HTMLInputElement | null>(null)
  const filePicker = useRef<HTMLInputElement | null>(null)

  const shown = useMemo(() => liveAttachments(draft, attachments), [attachments, draft])

  // Object URLs outlive the component unless revoked, and a long session can
  // paste a lot of screenshots.
  useEffect(
    () => () => {
      for (const url of attachmentUrls.current) URL.revokeObjectURL(url)
      attachmentUrls.current.clear()
    },
    [],
  )

  const attach = useCallback(
    async (file: File | Blob, name: string, kind: AttachmentKind = 'image') => {
      const id = kind === 'image' ? await attachImage(file, name) : await attachFile(file, name)

      if (id === null) return

      const element = textarea.current
      const caret = element === null ? draft.length : element.selectionStart
      const next = insertPlaceholder(draft, caret, id, kind)

      if (kind === 'image') {
        const url = URL.createObjectURL(file)
        attachmentUrls.current.add(url)
        setAttachments(current => [...current, { id, kind, name, url }])
      } else {
        setAttachments(current => [...current, { id, kind, name, size: file.size }])
      }
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
    (item: Attachment) => {
      onDraftChange(removePlaceholder(draft, item.id, item.kind))
      textarea.current?.focus()
    },
    [draft, onDraftChange],
  )

  /**
   * Files handed over by a drop or a paste: images go the image way (and
   * are refused, with the reason, on a model that cannot read one); every
   * other file is attached as a file.
   */
  const acceptDroppedFiles = useCallback(
    (files: readonly File[]) => {
      for (const file of files) {
        if (file.type.startsWith('image/')) {
          if (!vision) refuseImage()
          else void attach(file, file.name, 'image')
        } else {
          void attach(file, file.name, 'file')
        }
      }
    },
    [attach, refuseImage, vision],
  )

  /**
   * What a pick does. The image row opens the picker. A command that takes an
   * argument claims the draft — `/name `, or `/name <the text already typed>`
   * when the launcher opened over a sentence, so "fix the bug" + Plan reads
   * `/plan fix the bug`. A bare command runs at once, as it would on Enter.
   */
  const accept = useCallback(
    (row: MenuRow) => {
      setLauncher(false)

      const typing = typed !== null

      if (row.action === 'image' || row.action === 'file') {
        if (typing) onDraftChange('')

        ;(row.action === 'image' ? picker : filePicker).current?.click()

        return
      }

      if (row.hint !== undefined) {
        const rest = typing ? '' : draft.trim()

        onDraftChange(rest === '' ? `${row.name} ` : `${row.name} ${rest}`)
        textarea.current?.focus()

        return
      }

      if (typing) onDraftChange('')

      onSubmit(row.name)
      textarea.current?.focus()
    },
    [draft, onDraftChange, onSubmit, typed],
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

    setLauncher(false)
    onSubmit(text)
    onDraftChange('')
    if (!text.startsWith('/')) {
      for (const url of attachmentUrls.current) URL.revokeObjectURL(url)
      attachmentUrls.current.clear()
      setAttachments([])
    }
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

      if (menu !== null) {
        if (event.key === 'ArrowDown') {
          event.preventDefault()
          setHighlight(value => (value + 1) % menu.length)

          return
        }

        if (event.key === 'ArrowUp') {
          event.preventDefault()
          setHighlight(value => (value - 1 + menu.length) % menu.length)

          return
        }

        if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
          const choice = menu[highlight]

          if (choice !== undefined && !event.nativeEvent.isComposing) {
            event.preventDefault()
            accept(choice)

            return
          }
        }

        if (event.key === 'Escape') {
          event.preventDefault()

          // The launcher closes and leaves the draft alone; a half-typed
          // command is cleared, because the slash was the whole draft.
          if (launcher && typed === null) setLauncher(false)
          else onDraftChange('')

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
    [accept, acceptFile, files, highlight, launcher, mention, menu, onDraftChange, submit, typed],
  )

  return (
    <div className={[css.root, hero ? css.hero : ''].filter(Boolean).join(' ')}>
      {notice.text !== '' ? (
        <div
          className={[css.notice, notice.tone === 'error' ? css.noticeError : '']
            .filter(Boolean)
            .join(' ')}
          role="status"
        >
          {notice.text}
        </div>
      ) : (
        // The transcript is up before its runtime is: say so, since a prompt
        // sent now waits for the agent rather than going out at once.
        attaching && (
          <div className={css.notice} role="status">
            Connecting the agent…
          </div>
        )
      )}
      <div className={css.card} ref={card}>
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
        {menu !== null && !(mention !== null && files.length > 0) && (
          <div
            className={css.menu}
            data-command-menu=""
            data-overflow-below={overflowBelow ? '' : undefined}
            style={{ maxHeight: menuMaxHeight }}
          >
            <div
              aria-activedescendant={optionId(highlight)}
              aria-label="Commands"
              className={css.menuViewport}
              onScroll={updateOverflow}
              ref={menuViewport}
              role="listbox"
            >
              {menu.map((row, index) => {
                const alias = aliasOf(row)
                const Icon = row.icon ?? SlashSquareIcon
                const active = index === highlight

                return (
                  <Fragment key={row.name}>
                    {row.section !== undefined && row.section !== menu[index - 1]?.section && (
                      <div className={css.menuSection} role="presentation">
                        {row.section}
                      </div>
                    )}
                    <button
                      aria-selected={active}
                      className={css.menuItem}
                      data-active={active ? '' : undefined}
                      id={optionId(index)}
                      // mousedown, not click: the textarea keeps focus, and the
                      // pick runs before any blur-driven teardown.
                      onMouseDown={event => {
                        event.preventDefault()
                        accept(row)
                      }}
                      // mousemove, not mouseenter: real pointer motion moves the
                      // highlight; keyboard scrolling rows under a resting
                      // pointer must not steal it back.
                      onMouseMove={
                        active
                          ? undefined
                          : () => {
                              setHighlight(index)
                            }
                      }
                      role="option"
                      type="button"
                    >
                      <span aria-hidden className={css.menuIcon}>
                        <Icon size={16} />
                      </span>
                      <span className={css.menuTitle}>{row.label ?? bareName(row.name)}</span>
                      {alias !== undefined && <span className={css.menuAlias}>{alias}</span>}
                      {row.hint !== undefined && <span className={css.menuHint}>{row.hint}</span>}
                      {row.description !== undefined && (
                        <span className={css.menuDescription}>{row.description}</span>
                      )}
                    </button>
                  </Fragment>
                )
              })}
            </div>
          </div>
        )}
        {shown.length > 0 && (
          <div className={css.attachments}>
            {shown.map(item =>
              item.kind === 'image' ? (
                <div className={css.thumb} key={`image-${String(item.id)}`}>
                  <img alt={item.name} src={item.url} />
                  <button
                    aria-label={`Remove ${item.name}`}
                    className={css.thumbRemove}
                    onClick={() => {
                      dropAttachment(item)
                    }}
                    title="Remove this image"
                    type="button"
                  >
                    <XIcon size={10} />
                  </button>
                  <span className={css.thumbTag}>#{item.id}</span>
                </div>
              ) : (
                <div className={css.fileCard} data-file-card="" key={`file-${String(item.id)}`} title={item.name}>
                  <FileTypeIcon className={css.fileIcon} path={item.name} size={18} />
                  <span className={css.fileBody}>
                    <span className={css.fileName}>{item.name}</span>
                    <span className={css.fileMeta}>
                      {[fileExtension(item.name), item.size === undefined ? '' : formatBytes(item.size)]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  <button
                    aria-label={`Remove ${item.name}`}
                    className={css.thumbRemove}
                    onClick={() => {
                      dropAttachment(item)
                    }}
                    title="Remove this file"
                    type="button"
                  >
                    <XIcon size={10} />
                  </button>
                  <span className={css.thumbTag}>#{item.id}</span>
                </div>
              ),
            )}
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
              const files = [...event.dataTransfer.files]

              if (files.length === 0) return

              event.preventDefault()
              acceptDroppedFiles(files)
            }}
            onPaste={event => {
              // Only take over when an image or a file is actually on the
              // clipboard; a normal text paste must keep working.
              const image = [...event.clipboardData.items].find(entry =>
                entry.type.startsWith('image/'),
              )

              if (image !== undefined) {
                const file = image.getAsFile()

                if (file === null) return

                event.preventDefault()

                // A model that cannot read images gets told so. Attaching
                // anyway is a hard 400 that kills the turn.
                if (!vision) {
                  refuseImage()

                  return
                }
                void attach(file, file.name === '' ? 'pasted-image.png' : file.name)

                return
              }

              const files = [...event.clipboardData.files].filter(file => !file.type.startsWith('image/'))

              if (files.length === 0) return

              event.preventDefault()
              acceptDroppedFiles(files)
            }}
            onChange={event => {
              // Typing takes over from the launcher: the draft now says what
              // the menu should show, or that it should not be open.
              setLauncher(false)
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
            placeholder={
              running
                ? 'Queue a follow-up…'
                : hero
                  ? 'Describe what you want to build, / commands, @ files'
                  : 'Message ClawCodex, / commands, @ files'
            }
            ref={textarea}
            rows={hero ? 2 : 1}
            spellCheck={false}
            value={draft}
          />
        </div>
        <div className={css.row}>
          <div className={css.modes}>
            {vision && (
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
            )}
            <input
              aria-label="Attach a file"
              className={css.hiddenPicker}
              onChange={event => {
                const file = event.target.files?.[0]

                if (file !== undefined) void attach(file, file.name, 'file')
                event.target.value = ''
              }}
              ref={filePicker}
              tabIndex={-1}
              type="file"
            />
            <button
              aria-expanded={launcher}
              aria-haspopup="listbox"
              aria-label="Add files or run commands"
              className={css.add}
              onClick={() => {
                setLauncher(open => !open)
                textarea.current?.focus()
              }}
              onMouseDown={keepFocus}
              title="Add files or run commands"
              type="button"
            >
              <PlusIcon size={14} />
            </button>
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
            {/* After effort, matching the TUI's status-line order
                (`model effort nano`). A fact chip, not a control: nano is a
                launch flag, so there is nothing to open or toggle here. */}
            {nano && (
              <span
                className={pickerCss.nanoBadge}
                title="Nano mode: six tools, minimal prompt (launched with --nano)"
              >
                nano
              </span>
            )}
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
