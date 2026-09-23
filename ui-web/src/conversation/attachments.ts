/**
 * Attachments, tracked through the draft text.
 *
 * The backend queues an attached image or file and drains it into the next
 * prompt — but only if its chip (`[Image #N]` or `[File #N]`) is still in the
 * text at submit. That is the agent's own contract (`_drain_pending_images`:
 * *"An image whose [Image #N] chip is gone from the text is DROPPED. That is
 * how the chip doubles as un-attach"*, and `_drain_pending_files` says the
 * same), and it is why the draft, not a separate list, is the source of
 * truth for what will actually be sent.
 */

export type AttachmentKind = 'file' | 'image'

export interface Attachment {
  /** The number the backend assigned; the `#N` in the chip. */
  id: number
  kind: AttachmentKind
  name: string
  /** A file's byte size, for its card. */
  size?: number
  /** An image's preview URL; the composer revokes its own object URLs when the attachment goes. */
  url?: string
}

/** The chip text for an attachment, exactly as the backend matches it. */
export function placeholderFor(id: number, kind: AttachmentKind = 'image'): string {
  return kind === 'file' ? `[File #${String(id)}]` : `[Image #${String(id)}]`
}

/** Whether the draft still claims this attachment. */
export function isAttached(draft: string, id: number, kind: AttachmentKind = 'image'): boolean {
  return draft.includes(placeholderFor(id, kind))
}

/**
 * The attachments the draft still claims, in the order their chips appear.
 *
 * Ordering by position rather than by id keeps the strip matching what the
 * reader sees in their own text after they have moved a chip around.
 */
export function liveAttachments(draft: string, all: Attachment[]): Attachment[] {
  return all
    .filter(item => isAttached(draft, item.id, item.kind))
    .sort(
      (a, b) =>
        draft.indexOf(placeholderFor(a.id, a.kind)) - draft.indexOf(placeholderFor(b.id, b.kind)),
    )
}

/**
 * Insert a chip at `caret`, and report where the caret lands.
 *
 * Spacing is deliberate: a chip welded to the preceding word ("see this[Image
 * #1]") reads badly and, worse, is harder to delete cleanly — and deleting it
 * is the un-attach gesture.
 */
export function insertPlaceholder(
  draft: string,
  caret: number,
  id: number,
  kind: AttachmentKind = 'image',
): { caret: number; text: string } {
  const at = Math.max(0, Math.min(caret, draft.length))
  const before = draft.slice(0, at)
  const after = draft.slice(at)
  const lead = before === '' || /\s$/.test(before) ? '' : ' '
  // A trailing space only where one is wanted: at the end of the draft it
  // leaves the caret ready for the next word, but before existing text it
  // would double the space already there.
  const trail = after === '' || !/^\s/.test(after) ? ' ' : ''
  const insertion = `${lead}${placeholderFor(id, kind)}${trail}`

  return { caret: at + insertion.length, text: before + insertion + after }
}

/** Drop the chip for `id` from the draft, collapsing the space it leaves. */
export function removePlaceholder(draft: string, id: number, kind: AttachmentKind = 'image'): string {
  const chip = placeholderFor(id, kind)
  const at = draft.indexOf(chip)

  if (at < 0) return draft

  let head = draft.slice(0, at)
  let tail = draft.slice(at + chip.length)

  // Close the gap the chip leaves. At an edge that means dropping the orphaned
  // space entirely; in the middle it means collapsing two spaces into one.
  if (head === '') tail = tail.replace(/^\s+/, '')
  else if (tail === '') head = head.replace(/\s+$/, '')
  else if (/\s$/.test(head) && /^\s/.test(tail)) tail = tail.replace(/^\s/, '')

  return head + tail
}

/** The largest file the backend accepts (its `MAX_ATTACHED_FILE_BYTES`). */
export const MAX_FILE_BYTES = 10 * 1024 * 1024

/** A byte count as people read it: `512 B`, `12.3 KB`, `1.2 MB`. */
export function formatBytes(size: number): string {
  if (size < 1024) return `${String(size)} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`

  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

/** The upper-cased extension a file card shows, or nothing for a bare name. */
export function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.')

  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toUpperCase() : ''
}
