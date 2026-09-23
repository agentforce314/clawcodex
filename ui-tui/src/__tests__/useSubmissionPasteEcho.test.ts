import { PassThrough } from 'stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ComposerState, PasteSnippet } from '../app/interfaces.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'
import { useSubmission } from '../app/useSubmission.js'
import { truncateUserPrompt } from '../domain/messages.js'
import type { GatewayClient } from '../gatewayClient.js'
import { pasteTokenLabel } from '../lib/text.js'
import type { Msg } from '../types.js'

afterEach(() => {
  resetUiState()
})

const PASTED = Array.from({ length: 14 }, (_, i) => `line ${i + 1}`).join('\n')
const LABEL = pasteTokenLabel(PASTED, 14)

// Mounts useSubmission against stub composer/gateway; `setSnips` re-renders
// with a new `pasteSnips`, standing in for `clearIn()` after a submit.
const mount = (respond: (method: string) => Promise<unknown> = m =>
  Promise.resolve(m === 'session.steer' ? { status: 'queued' } : {})) => {
  const appended: Msg[] = []
  const requests: { method: string; text?: string }[] = []
  const queue: string[] = []
  let lastUserMsg = ''
  let api!: ReturnType<typeof useSubmission>

  const gw = {
    request: (method: string, params: { text?: string }) => {
      requests.push({ method, text: params.text })

      return respond(method)
    }
  } as unknown as GatewayClient

  const composerActions = {
    clearIn: () => {},
    enqueue: (text: string) => queue.push(text),
    pushHistory: () => {}
  }

  const Probe = ({ snips }: { snips: PasteSnippet[] }) => {
    api = useSubmission({
      appendMessage: msg => appended.push(msg),
      composerActions: composerActions as never,
      composerRefs: { queueEditRef: { current: null }, queueRef: { current: queue } } as never,
      composerState: { pasteSnips: snips } as unknown as ComposerState,
      gw,
      maybeGoodVibes: () => {},
      setLastUserMsg: v => {
        lastUserMsg = v
      },
      slashRef: { current: () => false },
      submitRef: { current: () => {} },
      sys: () => {}
    })

    return null
  }

  const stdout = new PassThrough()
  const stdin = new PassThrough()
  Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
  Object.assign(stdin, { isTTY: false })

  const instance = renderSync(React.createElement(Probe, { snips: [{ label: LABEL, text: PASTED }] }), {
    patchConsole: false,
    stderr: new PassThrough() as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  return {
    api: () => api,
    appended,
    lastUserMsg: () => lastUserMsg,
    queue,
    requests,
    setSnips: (snips: PasteSnippet[]) => instance.rerender(React.createElement(Probe, { snips })),
    unmount: () => {
      instance.unmount()
      instance.cleanup()
    }
  }
}

const submitted = (requests: { method: string; text?: string }[]) =>
  requests.filter(r => r.method === 'prompt.submit').map(r => r.text)

describe('useSubmission — collapsed paste expansion', () => {
  it('echoes the EXPANDED paste to the transcript, not the [[ … ]] label', () => {
    patchUiState({ busy: false, sid: 'sid-1' })
    const h = mount()

    h.api().send(`review this: ${LABEL}`)

    expect(submitted(h.requests)).toEqual([`review this: ${PASTED}`])
    expect(h.appended.map(m => m.text)).toEqual([`review this: ${PASTED}`])
    h.unmount()
  })

  it('records the expanded text for /retry, which re-sends after the snippets are cleared', () => {
    patchUiState({ busy: false, sid: 'sid-1' })
    const h = mount()

    h.api().send(`review this: ${LABEL}`)
    h.setSnips([])
    h.api().send(h.lastUserMsg())

    expect(submitted(h.requests)).toEqual([`review this: ${PASTED}`, `review this: ${PASTED}`])
    expect(h.appended.map(m => m.text)).toEqual([`review this: ${PASTED}`, `review this: ${PASTED}`])
    h.unmount()
  })

  it('expands a paste queued while busy once the queue drains after clearIn', () => {
    patchUiState({ busy: true, busyInputMode: 'queue', sid: 'sid-1' })
    const h = mount()

    h.api().dispatchSubmission(`review this: ${LABEL}`)
    // The label (not the paste) is queued, so `{!cmd}` interpolation can
    // never run over pasted content.
    expect(h.queue).toEqual([`review this: ${LABEL}`])

    h.setSnips([])
    patchUiState({ busy: false })
    h.api().sendQueued(h.queue.shift()!)

    expect(submitted(h.requests)).toEqual([`review this: ${PASTED}`])
    expect(h.appended.map(m => m.text)).toEqual([`review this: ${PASTED}`])
    h.unmount()
  })

  it('expands the paste on the steer path', () => {
    patchUiState({ busy: true, busyInputMode: 'steer', sid: 'sid-1' })
    const h = mount()

    h.api().dispatchSubmission(`also: ${LABEL}`)

    expect(h.requests.filter(r => r.method === 'session.steer').map(r => r.text)).toEqual([`also: ${PASTED}`])
    h.unmount()
  })
})

describe('useSubmission — paste fallbacks queue the label, not the paste', () => {
  const flush = () => new Promise(r => setTimeout(r, 0))

  it('keeps the snippet when steer is rejected, and expands it when the queue drains', async () => {
    patchUiState({ busy: true, busyInputMode: 'steer', sid: 'sid-1' })
    const h = mount(m => Promise.resolve(m === 'session.steer' ? { status: 'rejected' } : {}))

    h.api().dispatchSubmission(`also: ${LABEL}`)
    await flush()
    expect(h.queue).toEqual([`also: ${LABEL}`])

    h.setSnips([])
    patchUiState({ busy: false })
    h.api().sendQueued(h.queue.shift()!)

    expect(submitted(h.requests)).toEqual([`also: ${PASTED}`])
    h.unmount()
  })

  it('re-queues the label when prompt.submit reports session busy', async () => {
    patchUiState({ busy: false, sid: 'sid-1' })
    let first = true

    const h = mount(m => {
      if (m === 'prompt.submit' && first) {
        first = false

        return Promise.reject(new Error('session busy'))
      }

      return Promise.resolve({})
    })

    h.api().send(`review this: ${LABEL}`)
    await flush()
    expect(h.queue).toEqual([`review this: ${LABEL}`])

    h.setSnips([])
    h.api().sendQueued(h.queue.shift()!)

    expect(submitted(h.requests)).toEqual([`review this: ${PASTED}`, `review this: ${PASTED}`])
    h.unmount()
  })
})

describe('truncateUserPrompt', () => {
  it('leaves prompts up to 10k chars intact', () => {
    const text = 'x'.repeat(10_000)

    expect(truncateUserPrompt(text)).toBe(text)
  })

  it('truncates from 10,001 chars', () => {
    const out = truncateUserPrompt('x'.repeat(10_001))

    expect(out).toBe(`${'x'.repeat(2_500)}\n… +0 lines …\n${'x'.repeat(2_500)}`)
  })

  it('keeps head + tail of huge prompts with a hidden-line count', () => {
    // 2000 rows × 9 chars − 1 = 17,999 chars; 277 newlines in each of the
    // head and tail, 1999 in total → 1722 after the head − 277 in the tail.
    const text = Array.from({ length: 2_000 }, (_, i) => `row ${String(i).padStart(4, '0')}`).join('\n')
    const out = truncateUserPrompt(text)

    expect(out).toBe(`${text.slice(0, 2_500)}\n… +1445 lines …\n${text.slice(-2_500)}`)
  })
})
