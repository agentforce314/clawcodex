import { describe, expect, it } from 'vitest'

import type { ModelOptionsResult } from '../gateway/protocol.ts'
import { buildModelMenu, selectedModelId } from './ModelSelect.tsx'

const CATALOG: ModelOptionsResult = {
  providers: [
    { models: ['deepseek-v4-pro', 'deepseek-v4-flash'], name: 'deepseek' },
    { models: [], name: 'empty-provider' },
    { models: ['deepseek/deepseek-v4-pro', 'openai/gpt-5.6'], name: 'openrouter' },
  ],
}

describe('buildModelMenu', () => {
  it('groups models under their provider', () => {
    const { items } = buildModelMenu(CATALOG)

    const describe_ = (entry: (typeof items)[number]): unknown =>
      'text' in entry ? `# ${entry.text}` : 'label' in entry ? entry.label : '—'

    expect(items.map(describe_)).toEqual([
      '# deepseek',
      'deepseek-v4-pro',
      'deepseek-v4-flash',
      '# openrouter',
      'deepseek/deepseek-v4-pro',
      'openai/gpt-5.6',
    ])
  })

  it('omits a provider that offers nothing, heading included', () => {
    const { items } = buildModelMenu(CATALOG)

    expect(items.some(entry => 'text' in entry && entry.text === 'empty-provider')).toBe(false)
  })

  it('gives every row a positional id that maps back to a real pair', () => {
    // Positional rather than `provider<sep>model`: no separator is provably
    // absent from every provider name AND every model id — openrouter ships
    // `deepseek/deepseek-v4-pro` — and a parse that is usually right is worse
    // than no parse.
    const { choices } = buildModelMenu(CATALOG)

    expect([...choices.values()]).toEqual([
      { model: 'deepseek-v4-pro', provider: 'deepseek' },
      { model: 'deepseek-v4-flash', provider: 'deepseek' },
      { model: 'deepseek/deepseek-v4-pro', provider: 'openrouter' },
      { model: 'openai/gpt-5.6', provider: 'openrouter' },
    ])
  })

  it('handles an empty catalog without inventing rows', () => {
    expect(buildModelMenu({}).items).toEqual([])
    expect(buildModelMenu({ providers: [] }).choices.size).toBe(0)
  })
})

describe('selectedModelId', () => {
  const { choices } = buildModelMenu(CATALOG)

  it('marks the exact provider+model pair', () => {
    const id = selectedModelId(choices, 'deepseek-v4-pro', 'deepseek')

    expect(choices.get(id ?? '')).toEqual({ model: 'deepseek-v4-pro', provider: 'deepseek' })
  })

  it('distinguishes the same model offered by two providers', () => {
    const direct = selectedModelId(choices, 'deepseek-v4-pro', 'deepseek')
    const proxied = selectedModelId(choices, 'deepseek/deepseek-v4-pro', 'openrouter')

    expect(direct).not.toBe(proxied)
  })

  it('falls back to the model when the provider name does not match', () => {
    // The session can report a provider the catalog spells differently; the
    // model alone still identifies the row well enough to mark it.
    const id = selectedModelId(choices, 'deepseek-v4-flash', 'DeepSeek (direct)')

    expect(choices.get(id ?? '')?.model).toBe('deepseek-v4-flash')
  })

  it('marks nothing when the model is unknown or absent', () => {
    expect(selectedModelId(choices, 'gpt-9', 'openai')).toBeUndefined()
    expect(selectedModelId(choices, '', '')).toBeUndefined()
  })
})
