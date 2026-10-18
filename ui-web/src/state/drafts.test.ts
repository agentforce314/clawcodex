import { afterEach, expect, it } from 'vitest'
import { addDraftAttachment, adoptDraft, clearDraftAttachments, getDraft, resetDrafts, setDraftText } from './drafts.ts'

afterEach(resetDrafts)
it('isolates text and accepted attachment metadata by session', () => {
  setDraftText('one', '/plan fix [File #7]')
  addDraftAttachment('one', { id: 7, kind: 'file', name: 'notes.txt', size: 12 })
  setDraftText('two', 'another prompt')
  expect(getDraft('one').text).toBe('/plan fix [File #7]')
  expect(getDraft('two').attachments).toEqual([])
  expect(sessionStorage.getItem('clawcodex.web.drafts.v1')).toContain('notes.txt')
  clearDraftAttachments('one')
  expect(getDraft('one').attachments).toEqual([])
})
it('restores text to a replacement runtime but removes stale upload markers', () => {
  setDraftText('old', 'Inspect [Image #2] then [File #3] carefully')
  addDraftAttachment('old', { id: 2, kind: 'image', name: 'screen.png', url: 'data:image/png;base64,YQ==' })
  addDraftAttachment('old', { id: 3, kind: 'file', name: 'notes.txt' })
  expect(adoptDraft('old', 'new')).toBe(true)
  expect(getDraft('new')).toEqual({ text: 'Inspect then carefully', attachments: [] })
  setDraftText('new', 'edited')
  adoptDraft('old', 'new')
  expect(getDraft('new').text).toBe('edited')
})

it('keeps a saved conversation draft across runtime replacements and consumes welcome drafts', () => {
  setDraftText('welcome:/project', 'First draft')
  adoptDraft('welcome:/project', 'stored', 'runtime-one')
  expect(getDraft('welcome:/project').text).toBe('')
  expect(getDraft('stored').text).toBe('First draft')
  addDraftAttachment('stored', { id: 4, kind: 'file', name: 'notes.txt' }, 'runtime-one')
  setDraftText('stored', 'Read [File #4]')
  expect(adoptDraft('stored', 'stored', 'runtime-one')).toBe(false)
  expect(getDraft('stored').attachments).toHaveLength(1)
  expect(adoptDraft('stored', 'stored', 'runtime-two')).toBe(true)
  expect(getDraft('stored')).toEqual({ text: 'Read', attachments: [] })
})
