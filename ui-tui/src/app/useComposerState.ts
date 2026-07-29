import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { useStdin, withInkSuspended } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'
import { useCallback, useMemo, useState } from 'react'

import type { PasteEvent } from '../components/textInput.js'
import type { ImageAttachResponse, InputDetectDropResponse } from '../gatewayTypes.js'
import { useCompletion } from '../hooks/useCompletion.js'
import { useInputHistory } from '../hooks/useInputHistory.js'
import { useQueue } from '../hooks/useQueue.js'
import { isUsableClipboardText, readClipboardText } from '../lib/clipboard.js'
import { resolveEditor } from '../lib/editor.js'
import { readOsc52Clipboard } from '../lib/osc52.js'
import { isRemoteShellSession } from '../lib/terminalSetup.js'
import { pasteTokenLabel, stripTrailingPasteNewlines } from '../lib/text.js'
import { formatImageRef } from '../protocol/imageRef.js'

import type { MaybePromise, PasteSnippet, UseComposerStateOptions, UseComposerStateResult } from './interfaces.js'
import { $isBlocked } from './overlayStore.js'
import { getUiState } from './uiStore.js'

const PASTE_SNIP_MAX_COUNT = 32
const PASTE_SNIP_MAX_TOTAL_BYTES = 4 * 1024 * 1024

const trimSnips = (snips: PasteSnippet[]): PasteSnippet[] => {
  let total = 0
  const out: PasteSnippet[] = []

  for (let i = snips.length - 1; i >= 0; i--) {
    const snip = snips[i]!
    const size = snip.text.length

    if (out.length >= PASTE_SNIP_MAX_COUNT || total + size > PASTE_SNIP_MAX_TOTAL_BYTES) {
      break
    }

    total += size
    out.unshift(snip)
  }

  return out.length === snips.length ? snips : out
}

/** Append an `[Image #N]` chip on its own line at the end of the composer.
 *
 *  Block-level rather than at-cursor on purpose: the chip is a standing
 *  attachment indicator, not pasted prose, and putting it on its own line keeps
 *  it visible and easy to delete (deleting it un-attaches the image). All three
 *  attach routes — Ctrl+V hotkey, Cmd+V empty-paste, dropped path — use this so
 *  the chip lands in the same place regardless of how the image arrived. */
export function appendImageChip(value: string, id: number, extra = ''): { cursor: number; value: string } {
  // Trailing space ONLY when the chip is last, so paste-then-type does not
  // fuse into `[Image #1]what`. The reference does this lazily
  // (pendingSpaceAfterPillRef); an eager space is the same benefit for a
  // fraction of the machinery. Skipped before a newline, where it would just
  // be invisible trailing whitespace.
  const chip = formatImageRef(id)
  const body = extra ? `${chip}\n${extra}` : `${chip} `
  const next = !value ? body : value.endsWith('\n') ? `${value}${body}` : `${value}\n${body}`

  return { cursor: next.length, value: next }
}

/** Insert text at the cursor position, adding spacing to separate from adjacent non-whitespace. */
function insertAtCursor(value: string, cursor: number, text: string): { cursor: number; value: string } {
  const lead = cursor > 0 && !/\s/.test(value[cursor - 1] ?? '') ? ' ' : ''
  const tail = cursor < value.length && !/\s/.test(value[cursor] ?? '') ? ' ' : ''
  const insert = `${lead}${text}${tail}`

  return {
    cursor: cursor + insert.length,
    value: value.slice(0, cursor) + insert + value.slice(cursor)
  }
}

/**
 * Quick client-side heuristic to detect text that looks like a dropped file path.
 * When this returns true the composer sends RPC calls to the server for actual
 * validation.
 *
 * Keep in sync with `looks_like_dropped_path` in `src/utils/image_paste.py`,
 * which is a deliberate mirror of this function. (This comment used to point at
 * `_detect_file_drop() in cli.py`, which never existed — and the absence of a
 * real counterpart is how the two rules drifted far enough apart that the
 * backend gate accepted `README.md` as a path.)
 *
 * It also gates the SUBMIT path (`useSubmission.ts`), not just pastes, so
 * loosening it means ordinary prompts get sent to the backend for path
 * resolution and can be rewritten to `@<abspath>`.
 */
export function looksLikeDroppedPath(text: string): boolean {
  const trimmed = text.trim()

  if (!trimmed || trimmed.includes('\n')) {
    return false
  }

  // file:// URIs, relative, home-relative, quoted, and Windows drive paths
  if (
    trimmed.startsWith('file://') ||
    trimmed.startsWith('~/') ||
    trimmed.startsWith('./') ||
    trimmed.startsWith('../') ||
    trimmed.startsWith('"/') ||
    trimmed.startsWith("'/") ||
    trimmed.startsWith('"~') ||
    trimmed.startsWith("'~") ||
    /^[A-Za-z]:[/\\]/.test(trimmed) ||
    /^["'][A-Za-z]:[/\\]/.test(trimmed)
  ) {
    return true
  }

  // Bare absolute paths (start with /) — require a second '/' or a '.' to avoid
  // false positives on short strings like "/api" or "/help" which would trigger
  // unnecessary RPC round-trips.
  if (trimmed.startsWith('/')) {
    const rest = trimmed.slice(1)

    return rest.includes('/') || rest.includes('.')
  }

  return false
}

/** What a Ctrl+V/Cmd+V hotkey paste resolved to. */
export type HotkeyPasteDecision =
  | { image: ImageAttachResponse; kind: 'image' }
  | { kind: 'none' }
  | { kind: 'text'; text: string }

/**
 * Decide what a hotkey paste should do, given the clipboard text and a way to
 * ask the backend for a clipboard image.
 *
 * Pure and exported so the ORDERING is testable against the real code. The
 * ordering is the whole point and it is a performance contract, not just
 * behaviour: the image probe shells out to osascript/xclip (~1.5 s for a large
 * clipboard image), so it must run ONLY when the clipboard has no usable text.
 * A test against a reimplementation of this branch would stay green if the two
 * were ever swapped, which is exactly the regression worth preventing.
 *
 * Usable text wins outright. A copied image FILE is not the image case — it
 * carries its path as text, and the dropped-path branch in handleResolvedPaste
 * handles it.
 */
export async function resolveHotkeyPaste({
  probeClipboardImage,
  text
}: {
  probeClipboardImage: () => Promise<ImageAttachResponse | null>
  text: null | string
}): Promise<HotkeyPasteDecision> {
  if (isUsableClipboardText(text)) {
    return { kind: 'text', text }
  }

  const image = await probeClipboardImage().catch(() => null)

  // A `name` means attached. An `error`/`unavailable` is worth surfacing too —
  // otherwise Ctrl+V appears to do nothing at all. A plain `{}` means "no image
  // on the clipboard", which is not a failure and falls through to 'none'.
  if (image?.name || image?.error || image?.unavailable) {
    return { image, kind: 'image' }
  }

  return { kind: 'none' }
}

export function useComposerState({
  gw,
  onClipboardPaste,
  onImageAttached,
  submitRef
}: UseComposerStateOptions): UseComposerStateResult {
  const [input, setInput] = useState('')
  const [pasteSnips, setPasteSnips] = useState<PasteSnippet[]>([])
  const isBlocked = useStore($isBlocked)
  const { querier } = useStdin() as { querier: Parameters<typeof readOsc52Clipboard>[0] }

  const {
    queueRef,
    queueEditRef,
    queuedDisplay,
    queueEditIdx,
    enqueue,
    dequeue,
    removeQ,
    replaceQ,
    setQueueEdit,
    syncQueue
  } = useQueue()

  const { historyRef, historyIdx, setHistoryIdx, historyDraftRef, pushHistory } = useInputHistory()
  const { completions, compIdx, setCompIdx, compReplace, dismissCompletions } = useCompletion(input, isBlocked, gw)

  const clearIn = useCallback(() => {
    setInput('')
    setPasteSnips([])
    setQueueEdit(null)
    setHistoryIdx(null)
    historyDraftRef.current = ''
  }, [historyDraftRef, setQueueEdit, setHistoryIdx])

  const handleResolvedPaste = useCallback(
    async ({
      bracketed,
      cursor,
      text,
      value
    }: Omit<PasteEvent, 'hotkey'>): Promise<null | { cursor: number; value: string }> => {
      const cleanedText = stripTrailingPasteNewlines(text)

      if (!cleanedText || !/[^\n]/.test(cleanedText)) {
        if (bracketed) {
          void onClipboardPaste(true)
        }

        return null
      }

      const sid = getUiState().sid

      if (sid && looksLikeDroppedPath(cleanedText)) {
        try {
          const attached = await gw.request<ImageAttachResponse>('image.attach', {
            path: cleanedText,
            // This route inserts an `[Image #N]` chip below, so the chip is
            // authoritative: deleting it un-attaches the image.
            placeholder: true,
            session_id: sid
          })

          if (attached?.name) {
            // Insert the chip by RETURNING it, not via setInput: the caller
            // applies this {cursor, value} to the composer, so a separate
            // setInput would race it and the chip could be clobbered.
            // Guard the id rather than defaulting to 0: `[Image #0]` is
            // filtered by the backend's ref parser, so it would render as
            // visible garbage AND guarantee the image is dropped.
            if (attached.id) {
              return appendImageChip(value, attached.id, attached.remainder?.trim() ?? '')
            }

            onImageAttached?.(attached)

            return null
          }

          if (attached?.error || attached?.unavailable) {
            onImageAttached?.(attached)

            return null
          }
        } catch {
          // Fall back to generic file-drop detection below.
        }

        try {
          // No chip on this route (it inserts the returned text instead), so
          // no `placeholder` — the backend must not make a chip authoritative
          // for an image whose chip never gets rendered.
          const dropped = await gw.request<InputDetectDropResponse>('input.detect_drop', {
            session_id: sid,
            text: cleanedText
          })

          if (dropped?.matched && dropped.text) {
            return insertAtCursor(value, cursor, dropped.text)
          }
        } catch {
          // Fall through to normal text paste behavior.
        }
      }

      const lineCount = cleanedText.split('\n').length
      const pasteCollapseLines = getUiState().pasteCollapseLines
      const pasteCollapseChars = getUiState().pasteCollapseChars
      const linesHit = pasteCollapseLines > 0 && lineCount >= pasteCollapseLines
      const charsHit = pasteCollapseChars > 0 && cleanedText.length >= pasteCollapseChars

      if (!linesHit && !charsHit) {
        return {
          cursor: cursor + cleanedText.length,
          value: value.slice(0, cursor) + cleanedText + value.slice(cursor)
        }
      }

      const label = pasteTokenLabel(cleanedText, lineCount)
      const inserted = insertAtCursor(value, cursor, label)

      setPasteSnips(prev => trimSnips([...prev, { label, text: cleanedText }]))

      void gw
        .request<{ path?: string }>('paste.collapse', { text: cleanedText })
        .then(r => {
          const path = r?.path

          if (!path) {
            return
          }

          setPasteSnips(prev => prev.map(s => (s.label === label ? { ...s, path } : s)))
        })
        .catch(() => {})

      return inserted
    },
    [gw, onClipboardPaste, onImageAttached]
  )

  const handleTextPaste = useCallback(
    ({
      bracketed,
      cursor,
      hotkey,
      text,
      value
    }: PasteEvent): MaybePromise<null | { cursor: number; value: string }> => {
      if (hotkey) {
        const preferOsc52 = isRemoteShellSession(process.env)

        const readPreferredText = preferOsc52
          ? readOsc52Clipboard(querier).then(async osc52Text => {
              if (isUsableClipboardText(osc52Text)) {
                return osc52Text
              }

              return readClipboardText()
            })
          : readClipboardText().then(async clipText => {
              if (isUsableClipboardText(clipText)) {
                return clipText
              }

              return readOsc52Clipboard(querier)
            })

        return readPreferredText.then(async preferredText => {
          const decision = await resolveHotkeyPaste({
            probeClipboardImage: () =>
              gw.request<ImageAttachResponse>('image.clipboard', { placeholder: true }).catch(() => null),
            text: preferredText
          })

          if (decision.kind === 'text') {
            return handleResolvedPaste({
              bracketed: false, cursor, text: decision.text, value
            })
          }

          if (decision.kind === 'image') {
            const { image } = decision

            // The backend holds the bytes; the composer shows an `[Image #N]`
            // chip, which is both the "attached" indicator and the un-attach
            // control (delete it and the backend drops the image at submit).
            // Returned rather than set, so the caller's value application is the
            // single writer.
            if (image.id) {
              return appendImageChip(value, image.id)
            }

            onImageAttached?.(image)

            return null
          }

          void onClipboardPaste(false)

          return null
        })
      }

      return handleResolvedPaste({ bracketed: !!bracketed, cursor, text, value })
    },
    [gw, handleResolvedPaste, onClipboardPaste, onImageAttached, querier]
  )

  /** Insert an `[Image #N]` chip for a just-attached image.
   *
   *  The chip is the whole UI for an attachment: it shows one is pending, and
   *  deleting it un-attaches (the backend drops any image whose chip is absent
   *  from the submitted text). Placed on its own line when the composer already
   *  has content, which is how the reference renders it. */
  const insertImageRef = useCallback((id: number) => {
    if (!Number.isFinite(id) || id <= 0) {
      return
    }

    setInput(prev => appendImageChip(prev, id).value)
  }, [])

  const openEditor = useCallback(async () => {
    const dir = mkdtempSync(join(tmpdir(), 'clawcodex-'))
    const file = join(dir, 'prompt.md')
    const [cmd, ...args] = resolveEditor()

    writeFileSync(file, input)

    let exitCode: null | number = null

    await withInkSuspended(async () => {
      exitCode = spawnSync(cmd!, [...args, file], { stdio: 'inherit' }).status
    })

    try {
      if (exitCode !== 0) {
        return
      }

      const text = readFileSync(file, 'utf8').trimEnd()

      if (!text) {
        return
      }

      setInput('')
      submitRef.current(text)
    } finally {
      rmSync(dir, { force: true, recursive: true })
    }
  }, [input, submitRef])

  const actions = useMemo(
    () => ({
      clearIn,
      dequeue,
      dismissCompletions,
      enqueue,
      handleTextPaste,
      insertImageRef,
      openEditor,
      pushHistory,
      removeQueue: removeQ,
      replaceQueue: replaceQ,
      setCompIdx,
      setHistoryIdx,
      setInput,
      setPasteSnips,
      setQueueEdit,
      syncQueue
    }),
    [
      clearIn,
      dequeue,
      dismissCompletions,
      enqueue,
      handleTextPaste,
      insertImageRef,
      openEditor,
      pushHistory,
      removeQ,
      replaceQ,
      setCompIdx,
      setHistoryIdx,
      setQueueEdit,
      syncQueue
    ]
  )

  const refs = useMemo(
    () => ({
      historyDraftRef,
      historyRef,
      queueEditRef,
      queueRef,
      submitRef
    }),
    [historyDraftRef, historyRef, queueEditRef, queueRef, submitRef]
  )

  const state = useMemo(
    () => ({
      compIdx,
      compReplace,
      completions,
      historyIdx,
      input,
      pasteSnips,
      queueEditIdx,
      queuedDisplay
    }),
    [compIdx, compReplace, completions, historyIdx, input, pasteSnips, queueEditIdx, queuedDisplay]
  )

  return {
    actions,
    refs,
    state
  }
}
