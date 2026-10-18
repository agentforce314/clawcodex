import { map } from 'nanostores'
import { removePlaceholder, type Attachment } from '../conversation/attachments.ts'

export interface DraftDocument { text: string; attachments: Attachment[]; runtimeId?: string }
const KEY = 'clawcodex.web.drafts.v1'
const EMPTY: DraftDocument = { text: '', attachments: [] }

function readDrafts(): Record<string, DraftDocument> {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? '{}')
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {}
    const drafts: Record<string, DraftDocument> = {}
    for (const [id, value] of Object.entries(saved)) {
      if (!value || typeof value.text !== 'string' || !Array.isArray(value.attachments)) continue
      drafts[id] = { text: value.text, ...(typeof value.runtimeId === 'string' && { runtimeId: value.runtimeId }), attachments: value.attachments.filter((item: unknown): item is Attachment => {
        if (!item || typeof item !== 'object') return false
        const a = item as Record<string, unknown>
        return Number.isInteger(a.id) && Number(a.id) >= 0 && typeof a.name === 'string' && ['image', 'file'].includes(String(a.kind)) &&
          (a.size === undefined || typeof a.size === 'number' && Number.isFinite(a.size) && a.size >= 0) &&
          (a.url === undefined || typeof a.url === 'string' && /^data:image\//.test(a.url))
      }) }
    }
    return drafts
  } catch { return {} }
}

/** Tab-local drafts survive reload, without sharing unsent prompts with other windows. */
export const $drafts = map<Record<string, DraftDocument>>(readDrafts())
export function getDraft(id: string): DraftDocument { return $drafts.get()[id] ?? EMPTY }

function save(id: string, draft: DraftDocument): void {
  $drafts.setKey(id, draft)
  persist()
}

function persist(): void {
  try { sessionStorage.setItem(KEY, JSON.stringify($drafts.get())) }
  catch {
    // Large image previews can exceed the tab's quota. Retain names, markers
    // and text durably; the full preview remains available in this page.
    try {
      const small = Object.fromEntries(Object.entries($drafts.get()).map(([key, doc]) =>
        [key, { ...doc, attachments: doc.attachments.map(({ url: _url, ...item }) => item) }]))
      sessionStorage.setItem(KEY, JSON.stringify(small))
    } catch { /* Storage disabled: drafts still survive navigation in this page. */ }
  }
}

export function setDraftText(id: string, text: string): void { save(id, { ...getDraft(id), text }) }
export function addDraftAttachment(id: string, attachment: Attachment, runtimeId = id): void {
  const draft = getDraft(id)
  save(id, { ...draft, runtimeId, attachments: [...draft.attachments.filter(a => a.id !== attachment.id || a.kind !== attachment.kind), attachment] })
}
export function clearDraftAttachments(id: string): void { save(id, { ...getDraft(id), attachments: [] }) }

/** A replacement runtime can restore text, but its upload identifiers belong to the old agent. */
export function adoptDraft(from: string, to: string, runtimeId = to): boolean {
  const source = $drafts.get()[to] ?? $drafts.get()[from]
  if (!source) return false
  const stale = source.attachments.length > 0 && source.runtimeId !== runtimeId
  let text = source.text
  if (stale) {
    for (const attachment of source.attachments) {
      while (text.includes(attachment.kind === 'file' ? `[File #${attachment.id}]` : `[Image #${attachment.id}]`)) {
        text = removePlaceholder(text, attachment.id, attachment.kind)
      }
    }
  }
  if (from !== to) $drafts.setKey(from, undefined)
  save(to, stale ? { text, attachments: [] } : source)
  return stale
}

export function resetDrafts(): void {
  $drafts.set({})
  try { sessionStorage.removeItem(KEY) } catch { /* Storage may be disabled. */ }
}
