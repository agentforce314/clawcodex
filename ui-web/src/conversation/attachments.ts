/**
 * Image attachments, tracked through the draft text.
 *
 * The backend queues an attached image and drains it into the next prompt —
 * but only if its `[Image #N]` chip is still in the text at submit. That is
 * the agent's own contract (`_drain_pending_images`: *"An image whose
 * [Image #N] chip is gone from the text is DROPPED. That is how the chip
 * doubles as un-attach"*), and it is why the draft, not a separate list, is
 * the source of truth for what will actually be sent.
 */

export interface Attachment {
  /** The number the backend assigned; the `[Image #N]` in the text. */
  id: number
  name: string
  /** Object URL for the thumbnail, revoked when the attachment goes. */
  url: string
}

/** The chip text for an attachment, exactly as the backend matches it. */
export function placeholderFor(id: number): string {
  return `[Image #${String(id)}]`
}

/** Whether the draft still claims this attachment. */
export function isAttached(draft: string, id: number): boolean {
  return draft.includes(placeholderFor(id))
}

/**
 * The attachments the draft still claims, in the order their chips appear.
 *
 * Ordering by position rather than by id keeps the thumbnail strip matching
 * what the reader sees in their own text after they have moved a chip around.
 */
export function liveAttachments(draft: string, all: Attachment[]): Attachment[] {
  return all
    .filter(item => isAttached(draft, item.id))
    .sort((a, b) => draft.indexOf(placeholderFor(a.id)) - draft.indexOf(placeholderFor(b.id)))
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
): { caret: number; text: string } {
  const at = Math.max(0, Math.min(caret, draft.length))
  const before = draft.slice(0, at)
  const after = draft.slice(at)
  const lead = before === '' || /\s$/.test(before) ? '' : ' '
  // A trailing space only where one is wanted: at the end of the draft it
  // leaves the caret ready for the next word, but before existing text it
  // would double the space already there.
  const trail = after === '' || !/^\s/.test(after) ? ' ' : ''
  const insertion = `${lead}${placeholderFor(id)}${trail}`

  return { caret: at + insertion.length, text: before + insertion + after }
}

/** Drop the chip for `id` from the draft, collapsing the space it leaves. */
export function removePlaceholder(draft: string, id: number): string {
  const chip = placeholderFor(id)
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
