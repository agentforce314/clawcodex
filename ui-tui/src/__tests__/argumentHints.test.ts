import { describe, expect, it } from 'vitest'

import { argumentHintFor, ghostArgumentHint } from '../app/slash/argumentHints.js'

// User report: slash commands had no value suggestions. The ghost hint fires
// on exactly `/name ` (one trailing space, no args) — the original CC's
// hasExactlyOneTrailingSpace gate — and resolves local-registry hints first
// (dispatch order), then catalog hints (gateway/workflow commands).
describe('argumentHintFor', () => {
  it('resolves a local registry command hint', () => {
    expect(argumentHintFor('skills')).toBe('[list | inspect <name> | search <query>]')
  })

  it('resolves via a local alias (dispatch consults aliases too)', () => {
    // /bg is a SLASHES name, but dispatch resolves it to the local
    // `background` command via its alias — the hint must follow dispatch.
    expect(argumentHintFor('bg')).toBe('<prompt>')
  })

  it('prefers the local hint over a catalog hint for shadowed names', () => {
    // Local /compact (transcript display toggle) shadows the gateway
    // /compact (conversation compaction); dispatch runs the local one.
    expect(argumentHintFor('compact', { '/compact': '[<instructions>]' })).toBe('[on|off|toggle]')
  })

  it('falls back to catalog hints for gateway/workflow commands', () => {
    expect(argumentHintFor('effort', { '/effort': '[minimal|low|medium|high|auto|ultracode]' })).toBe(
      '[minimal|low|medium|high|auto|ultracode]'
    )
    expect(argumentHintFor('deep-research', { '/deep-research': '<question>' })).toBe('<question>')
  })

  it('returns undefined for unknown commands', () => {
    expect(argumentHintFor('frobnicate', {})).toBeUndefined()
  })
})

describe('ghostArgumentHint', () => {
  const catalog = { '/effort': '[minimal|low|medium|high|auto|ultracode]' }

  it('fires on exactly one trailing space after a known command', () => {
    expect(ghostArgumentHint('/skills ', null)).toBe('[list | inspect <name> | search <query>]')
    expect(ghostArgumentHint('/effort ', catalog)).toBe('[minimal|low|medium|high|auto|ultracode]')
  })

  it('stays hidden while the name is still being typed', () => {
    expect(ghostArgumentHint('/skills', null)).toBeUndefined()
  })

  it('clears once a real argument or extra space is typed', () => {
    expect(ghostArgumentHint('/skills l', null)).toBeUndefined()
    expect(ghostArgumentHint('/skills  ', null)).toBeUndefined()
    expect(ghostArgumentHint('/skills list ', null)).toBeUndefined()
  })

  it('ignores non-slash input and bare slashes', () => {
    expect(ghostArgumentHint('skills ', null)).toBeUndefined()
    expect(ghostArgumentHint('/ ', null)).toBeUndefined()
    expect(ghostArgumentHint('', null)).toBeUndefined()
  })
})
