import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { askUserSummary, formatToolResult } from '../gatewayClient.js'

const SRC = join(import.meta.dirname, '..')

describe('askUserSummary', () => {
  it('renders one "· question → answer" row per answer', () => {
    expect(
      askUserSummary({ answers: { 'Which posts?': 'The two un-sourced posts', 'How deep?': 'Facts only' } })
    ).toBe('· Which posts? → The two un-sourced posts\n· How deep? → Facts only')
  })

  it('renders the declined line', () => {
    expect(askUserSummary({ declined: true })).toBe('User declined to answer questions')
  })

  it('says so when the review step submitted nothing', () => {
    expect(askUserSummary({ answers: {} })).toBe('No answers submitted')
  })
})

describe('formatToolResult for AskUserQuestion', () => {
  // The bug this whole change exists to fix: with no ask_user hook the tool
  // returns its own questions as a "pending" result, and the default mapper
  // JSON-dumps them straight into the transcript.
  const pending = JSON.stringify({
    questions: [
      { question: 'Which posts should I research and enrich?', header: 'Scope', options: [{ label: 'a' }] },
      { question: 'How aggressively should I edit?', header: 'Editing depth', options: [{ label: 'b' }] }
    ],
    status: 'pending'
  })

  it('collapses the legacy pending blob to one line', () => {
    expect(formatToolResult('AskUserQuestion', pending)).toBe(
      'Waiting on 2 questions (no interactive surface)'
    )
  })

  it('singularizes one question', () => {
    const one = JSON.stringify({ questions: [{ question: 'Ship it?' }], status: 'pending' })
    expect(formatToolResult('AskUserQuestion', one)).toBe('Waiting on 1 question (no interactive surface)')
  })

  it('never renders the raw JSON', () => {
    const out = formatToolResult('AskUserQuestion', pending)
    expect(out).not.toContain('{')
    expect(out).not.toContain('multiSelect')
    expect(out.split('\n')).toHaveLength(1)
  })

  it('passes through a payload that is not the pending shape', () => {
    expect(formatToolResult('AskUserQuestion', 'something else entirely')).toBe('something else entirely')
  })

  it('prefers the structured envelope over the model-facing prose', () => {
    const prose = 'User has answered your questions: "Which posts?"="Two". You can now continue.'
    expect(formatToolResult('AskUserQuestion', prose, false, undefined, undefined, {
      answers: { 'Which posts?': 'Two' }
    })).toBe('· Which posts? → Two')
  })

  it('leaves errors to the error path', () => {
    expect(formatToolResult('AskUserQuestion', 'boom', true)).toBe('Error: boom')
  })
})

describe('overlay registry wiring', () => {
  // The question overlay has to be registered in several hand-maintained lists;
  // missing one fails silently (an invisible-but-blocking dialog, or a dead
  // Ctrl+C). Anchor tests so a refactor that drops one fails loudly.
  const read = (p: string) => readFileSync(join(SRC, p), 'utf8')

  it('is folded into $isBlocked, which unmounts the composer', () => {
    const store = read('app/overlayStore.ts')
    // Both halves are duplicated by hand in this file.
    expect(store).toMatch(/questions,/)
    expect(store).toMatch(/questions \|\|/)
    expect(store).toMatch(/questions: null,/)
  })

  it('is in the promptOverlay list so keystrokes reach the dialog', () => {
    expect(read('app/useInputHandlers.ts')).toMatch(/promptOverlay =[\s\S]{0,200}overlay\.questions/)
  })

  it('has a Ctrl+C branch, or cancelling would be a silent no-op', () => {
    expect(read('app/useInputHandlers.ts')).toMatch(/overlay\.questions\)[\s\S]{0,800}question\.respond/)
  })

  it('is mounted in the PromptZone priority chain', () => {
    expect(read('components/appOverlays.tsx')).toMatch(/overlay\.questions[\s\S]{0,300}<QuestionPrompt/)
  })

  it('maps the gateway event onto the overlay slot', () => {
    expect(read('app/createGatewayEventHandler.ts')).toMatch(
      /case 'question\.request':[\s\S]{0,300}patchOverlayState/
    )
  })

  it('replies over the control channel', () => {
    const gw = read('gatewayClient.ts')
    expect(gw).toMatch(/ask_user_question/)
    expect(gw).toMatch(/case 'question\.respond'/)
    expect(gw).toMatch(/action: 'submit'/)
    expect(gw).toMatch(/action: 'cancel'/)
  })
})
