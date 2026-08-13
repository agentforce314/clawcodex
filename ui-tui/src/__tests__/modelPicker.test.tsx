/**
 * The /model picker's three steps: provider → model → effort.
 *
 * Step 3 is conditional by design — a model that accepts no effort parameter
 * gets the two-step flow it always had, because a list whose every row is a
 * silent no-op is worse than no list. These drive real keys through a mounted
 * component, since the stage transitions ARE the behavior.
 */
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ModelPicker } from '../components/modelPicker.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const ARROW_DOWN = '[B'
const ENTER = '\r'
const ESC = ''

// Ink brackets every frame in a synchronized-update pair (BSU … ESU) and, into
// a PassThrough, flushes each one more than once — so the accumulated output
// contains EVERY stage this picker has ever painted. Asserting a stage against
// it would pass for any stage already visited, which is exactly the mistake
// that let a broken Esc target go unnoticed. Assert against the last frame.
const BSU = '[?2026h'
const ESU = '[?2026l'

const lastFrame = (output: string): string => {
  const frames = output
    .split(BSU)
    .map(chunk => chunk.split(ESU)[0] ?? '')
    .filter(frame => stripAnsi(frame).trim() !== '')

  return stripAnsi(frames.at(-1) ?? '')
}

const PROVIDERS = [
  {
    authenticated: true,
    is_current: true,
    models: ['claude-opus-5', 'claude-haiku-4-5'],
    name: 'anthropic',
    slug: 'anthropic',
    total_models: 2
  }
]

interface FakeReplies {
  effort?: { current?: string; levels?: string[]; supported?: boolean }
  /** Hold the effort reply open so the loading state can be driven. */
  effortDeferred?: boolean
  effortRejects?: boolean
}

function mount(replies: FakeReplies = {}) {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let out = ''

  stdout.on('data', (c: Buffer) => {
    out += c.toString()
  })
  Object.assign(stdout, { columns: 100, rows: 40 })
  Object.assign(stdin, { isTTY: true, ref: () => {}, setRawMode: () => {}, unref: () => {} })

  const onCancel = vi.fn()
  const onSelect = vi.fn()
  const requests: Array<{ method: string; params: unknown }> = []
  let releaseEffort = () => {}

  const gw = {
    request: (method: string, params: unknown) => {
      requests.push({ method, params })

      if (method === 'model.options') {
        return Promise.resolve({ model: 'claude-opus-5', provider: 'anthropic', providers: PROVIDERS })
      }

      if (method === 'model.effort_options') {
        if (replies.effortRejects) {
          return Promise.reject(new Error('backend said no'))
        }

        const answer = replies.effort ?? { current: '', levels: [], supported: false }

        if (replies.effortDeferred) {
          return new Promise(resolve => {
            releaseEffort = () => resolve(answer)
          })
        }

        return Promise.resolve(answer)
      }

      return Promise.resolve({})
    }
  }

  const app = renderSync(
    React.createElement(ModelPicker, {
      gw: gw as never,
      onCancel,
      onSelect,
      sessionId: 'sid-1',
      t: DEFAULT_THEME
    }),
    {
      exitOnCtrlC: false,
      patchConsole: false,
      stderr: stderr as NodeJS.WriteStream,
      stdin: stdin as NodeJS.ReadStream,
      stdout: stdout as NodeJS.WriteStream
    }
  )

  return {
    /** The stage currently on screen — see lastFrame. */
    frame: () => lastFrame(out),
    onCancel,
    onSelect,
    output: () => stripAnsi(out),
    async press(seq: string, settle = 30) {
      stdin.write(seq)
      await delay(settle)
    },
    async releaseEffort(settle = 30) {
      releaseEffort()
      await delay(settle)
    },
    requests,
    unmount: () => app.unmount()
  }
}

const FULL_LADDER = { current: '', levels: ['low', 'medium', 'high', 'xhigh', 'max'], supported: true }

/** Walk to the model list, then select the first model. */
async function toEffortStage(p: ReturnType<typeof mount>) {
  await delay(30)
  await p.press(ENTER) // provider → model
  await p.press(ENTER) // model → effort (or apply, when unsupported)
}

describe('ModelPicker step 3 — effort', () => {
  it('labels the flow as three steps', async () => {
    const p = mount()
    await delay(30)

    expect(p.frame()).toContain('step 1/3')
    p.unmount()
  })

  it('offers auto plus the levels the model accepts', async () => {
    const p = mount({ effort: FULL_LADDER })
    await toEffortStage(p)

    const frame = p.frame()

    expect(frame).toContain('step 3/3')
    expect(frame).toContain('1. auto')
    expect(frame).toContain('2. low')
    expect(frame).toContain('6. max')
    p.unmount()
  })

  it('asks the backend about the model just chosen, not the live one', async () => {
    const p = mount({ effort: FULL_LADDER })
    await delay(30)
    await p.press(ENTER)
    await p.press(ARROW_DOWN) // second model
    await p.press(ENTER)

    const ask = p.requests.find(r => r.method === 'model.effort_options')

    expect(ask?.params).toEqual({ model: 'claude-haiku-4-5', provider: 'anthropic' })
    p.unmount()
  })

  it('applies the model with the chosen level', async () => {
    const p = mount({ effort: FULL_LADDER })
    await toEffortStage(p)
    await p.press(ARROW_DOWN) // auto → low
    await p.press(ENTER)

    expect(p.onSelect).toHaveBeenCalledWith(expect.stringContaining('claude-opus-5 --provider anthropic'), 'low')
    p.unmount()
  })

  it('sends no effort when auto is chosen', async () => {
    // `auto` means "clear the override", which is the picker's own row — not
    // a level to hand to /effort.
    const p = mount({ effort: FULL_LADDER })
    await toEffortStage(p)
    await p.press(ENTER)

    expect(p.onSelect).toHaveBeenCalledWith(expect.any(String), undefined)
    p.unmount()
  })

  it('preselects the session’s live level so Enter-through changes nothing', async () => {
    const p = mount({ effort: { ...FULL_LADDER, current: 'xhigh' } })
    await toEffortStage(p)
    await p.press(ENTER)

    expect(p.onSelect).toHaveBeenCalledWith(expect.any(String), 'xhigh')
    p.unmount()
  })

  it('skips the step entirely for a model with no effort ladder', async () => {
    const p = mount({ effort: { levels: [], supported: false } })
    await toEffortStage(p)

    expect(p.onSelect).toHaveBeenCalledWith(expect.stringContaining('claude-opus-5'), undefined)
    expect(p.output()).not.toContain('step 3/3')
    p.unmount()
  })

  it('applies the model anyway when the capability lookup fails', async () => {
    // The model is already chosen by this point; a failed lookup must not
    // strand the user on a blank step.
    const p = mount({ effortRejects: true })
    await toEffortStage(p)

    expect(p.onSelect).toHaveBeenCalledWith(expect.stringContaining('claude-opus-5'), undefined)
    p.unmount()
  })

  it('goes back to the model list, not out to the providers', async () => {
    const p = mount({ effort: FULL_LADDER })
    await toEffortStage(p)

    expect(p.frame()).toContain('step 3/3')

    // A lone ESC is the prefix of every escape SEQUENCE, so the input parser
    // holds it until it can rule one out — give it longer than a plain key.
    await p.press(ESC, 200)

    const frame = p.frame()

    expect(frame).toContain('step 2/3')
    expect(frame).not.toContain('step 1/3')
    expect(p.onCancel).not.toHaveBeenCalled()
    p.unmount()
  })

  it('does not dispatch a switch when backing out of step 3', async () => {
    const p = mount({ effort: FULL_LADDER })
    await toEffortStage(p)
    // A lone ESC is the prefix of every escape SEQUENCE, so the input parser
    // holds it until it can rule one out — give it longer than a plain key.
    await p.press(ESC, 200)

    expect(p.onSelect).not.toHaveBeenCalled()
    p.unmount()
  })

  it('ignores Enter while the ladder is still loading', async () => {
    // Without the loading guard this applies with no effort AND the reply
    // then lands on a stage the user has already left — one keystroke, two
    // switches.
    const p = mount({ effort: FULL_LADDER, effortDeferred: true })
    await toEffortStage(p)

    expect(p.frame()).toContain('loading effort levels…')

    await p.press(ENTER)

    expect(p.onSelect).not.toHaveBeenCalled()

    await p.releaseEffort()
    await p.press(ENTER)

    expect(p.onSelect).toHaveBeenCalledTimes(1)
    p.unmount()
  })

  it('honors allowEffortStep=false for draft-only callers', async () => {
    // The new-prompt-session picker captures a draft and has no session to
    // set an effort on, so it must keep the two-step flow.
    const stdout = new PassThrough()
    const stdin = new PassThrough()
    Object.assign(stdout, { columns: 100, rows: 40 })
    Object.assign(stdin, { isTTY: true, ref: () => {}, setRawMode: () => {}, unref: () => {} })
    const onSelect = vi.fn()
    const requests: string[] = []

    const app = renderSync(
      React.createElement(ModelPicker, {
        allowEffortStep: false,
        gw: {
          request: (method: string) => {
            requests.push(method)

            return Promise.resolve(
              method === 'model.options'
                ? { model: 'claude-opus-5', provider: 'anthropic', providers: PROVIDERS }
                : {}
            )
          }
        } as never,
        onCancel: vi.fn(),
        onSelect,
        sessionId: 'sid-1',
        t: DEFAULT_THEME
      }),
      {
        exitOnCtrlC: false,
        patchConsole: false,
        stderr: new PassThrough() as NodeJS.WriteStream,
        stdin: stdin as NodeJS.ReadStream,
        stdout: stdout as NodeJS.WriteStream
      }
    )

    await delay(30)
    stdin.write(ENTER)
    await delay(30)
    stdin.write(ENTER)
    await delay(30)

    expect(onSelect).toHaveBeenCalledWith(expect.stringContaining('claude-opus-5'), undefined)
    expect(requests).not.toContain('model.effort_options')
    app.unmount()
  })
})
