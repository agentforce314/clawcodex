/**
 * `infoAfterModelSwitch` — the single place a completed model switch is folded
 * back into `ui.info`.
 *
 * The regression it exists to prevent: the stats line renders
 * `profile_name · model` as one phrase, and switching a model used to patch
 * `model` alone, so a cross-provider selection displayed the NEW model beside
 * the OLD provider (`anthropic · gpt-5.6-luna`).
 */
import { describe, expect, it } from 'vitest'

import { infoAfterModelSwitch, modelPickerCommands } from '../domain/modelSwitch.js'
import type { SessionInfo } from '../types.js'

const base = (): SessionInfo => ({
  cwd: '/w/app',
  model: 'claude-opus-5',
  profile_name: 'anthropic',
  skills: {},
  tools: {}
})

describe('infoAfterModelSwitch', () => {
  it('moves the provider label along with the model', () => {
    const next = infoAfterModelSwitch(base(), 'gpt-5.6-luna', 'openai')

    expect(next.model).toBe('gpt-5.6-luna')
    expect(next.profile_name).toBe('openai')
  })

  it('keeps the current provider when the switch reports none', () => {
    // An absent provider means "unchanged", not "unknown" — a same-provider
    // switch and an older backend both look like this, and blanking a correct
    // label would be a worse bug than the stale one.
    const next = infoAfterModelSwitch(base(), 'claude-sonnet-5')

    expect(next.model).toBe('claude-sonnet-5')
    expect(next.profile_name).toBe('anthropic')
  })

  it('preserves the rest of the session info', () => {
    const next = infoAfterModelSwitch({ ...base(), reasoning_effort: 'high' }, 'gpt-5.6-luna', 'openai')

    expect(next.cwd).toBe('/w/app')
    expect(next.reasoning_effort).toBe('high')
  })

  it('does not mutate the info it was given', () => {
    const info = base()
    infoAfterModelSwitch(info, 'gpt-5.6-luna', 'openai')

    expect(info.model).toBe('claude-opus-5')
    expect(info.profile_name).toBe('anthropic')
  })

  it('seeds a minimal info when the switch beats the backend init', () => {
    const next = infoAfterModelSwitch(null, 'gpt-5.6-luna', 'openai')

    expect(next).toEqual({ model: 'gpt-5.6-luna', profile_name: 'openai', skills: {}, tools: {} })
  })

  it('leaves profile_name unset when there is neither prior info nor a provider', () => {
    expect(infoAfterModelSwitch(null, 'gpt-5.6-luna')).toEqual({ model: 'gpt-5.6-luna', skills: {}, tools: {} })
  })
})

describe('modelPickerCommands', () => {
  const value = 'claude-opus-5 --provider anthropic --tui-session'

  it('dispatches the model switch, then the effort', () => {
    expect(modelPickerCommands(value, 'xhigh')).toEqual([`/model ${value}`, '/effort xhigh'])
  })

  it('emits no /effort when step 3 was skipped or answered auto', () => {
    // Leaving the session's level untouched is the point: `auto` and "this
    // model has no ladder" both mean "don't set one", not "reset to default".
    expect(modelPickerCommands(value)).toEqual([`/model ${value}`])
    expect(modelPickerCommands(value, '')).toEqual([`/model ${value}`])
  })
})
