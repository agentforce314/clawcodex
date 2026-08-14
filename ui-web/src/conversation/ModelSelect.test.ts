import { describe, expect, it } from 'vitest'

import type { ModelOptionsResult } from '../gateway/protocol.ts'
import { buildModelMenu, qualifiedModel, selectedModelId } from './ModelSelect.tsx'

const CATALOG: ModelOptionsResult = {
  providers: [
    { models: ['deepseek-v4-pro', 'deepseek-v4-flash'], name: 'deepseek' },
    { models: [], name: 'empty-provider' },
    { models: ['deepseek/deepseek-v4-pro', 'openai/gpt-5.6'], name: 'openrouter' },
  ],
}

/** The real shape: a display name beside the id the backend answers to. */
const SLUGGED: ModelOptionsResult = {
  providers: [
    { models: ['deepseek-v4-flash'], name: 'DeepSeek', slug: 'deepseek' },
    { models: ['claude-sonnet-4-6'], name: 'Anthropic Claude', slug: 'anthropic' },
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

describe('provider identity', () => {
  it('sends the slug, not the display name', () => {
    // "Unknown provider: DeepSeek" — the backend answers to "deepseek".
    const { choices } = buildModelMenu(SLUGGED)

    expect([...choices.values()]).toEqual([
      { model: 'deepseek-v4-flash', provider: 'deepseek' },
      { model: 'claude-sonnet-4-6', provider: 'anthropic' },
    ])
  })

  it('still shows the display name as the heading', () => {
    const { items } = buildModelMenu(SLUGGED)
    const headings = items.filter(entry => 'text' in entry).map(entry => (entry as { text: string }).text)

    expect(headings).toEqual(['DeepSeek', 'Anthropic Claude'])
  })

  it('falls back to the name when a row carries no slug', () => {
    // Which is the shape every existing fixture uses.
    const { choices } = buildModelMenu(CATALOG)

    expect([...choices.values()][0]).toEqual({ model: 'deepseek-v4-pro', provider: 'deepseek' })
  })
})

describe('qualifiedModel', () => {
  it('names the provider alongside the model', () => {
    // The same id comes from more than one provider, with different keys and
    // different bills; a bare name does not say which is running.
    expect(qualifiedModel('deepseek-v4-pro', 'deepseek')).toBe('deepseek:deepseek-v4-pro')
    expect(qualifiedModel('deepseek/deepseek-v4-pro', 'openrouter')).toBe(
      'openrouter:deepseek/deepseek-v4-pro',
    )
  })

  it('stays bare rather than showing a dangling colon', () => {
    // An unqualified name is incomplete; a separator with nothing after it is
    // broken.
    expect(qualifiedModel('deepseek-v4-pro')).toBe('deepseek-v4-pro')
    expect(qualifiedModel('deepseek-v4-pro', '')).toBe('deepseek-v4-pro')
  })

  it('falls back to a placeholder when there is no model at all', () => {
    expect(qualifiedModel('')).toBe('Model')
    expect(qualifiedModel('', 'deepseek')).toBe('Model')
  })
})
