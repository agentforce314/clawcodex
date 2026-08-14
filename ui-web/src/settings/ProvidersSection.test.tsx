import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ModelOption } from '../gateway/protocol.ts'
import { ProvidersSection, credentialState, orderProviders, takesKey } from './ProvidersSection.tsx'

afterEach(cleanup)

const DEEPSEEK: ModelOption = {
  authenticated: true,
  auth_type: 'api_key',
  is_current: true,
  key_env: 'DEEPSEEK_API_KEY',
  name: 'DeepSeek',
  slug: 'deepseek',
  total_models: 4,
}
const OPENAI: ModelOption = {
  authenticated: true,
  auth_type: 'api_key',
  name: 'OpenAI',
  slug: 'openai',
  total_models: 9,
}
const GROQ: ModelOption = { authenticated: false, name: 'Groq', slug: 'groq', total_models: 3 }

describe('orderProviders', () => {
  it('puts the running provider first, then the configured ones', () => {
    // The catalog knows ~30 providers and a user has set up two.
    const order = orderProviders([GROQ, OPENAI, DEEPSEEK]).map(p => p.slug)

    expect(order).toEqual(['deepseek', 'openai', 'groq'])
  })

  it('sorts the unconfigured rest by name', () => {
    const zed: ModelOption = { authenticated: false, name: 'Zed', slug: 'zed' }
    const ace: ModelOption = { authenticated: false, name: 'Ace', slug: 'ace' }

    expect(orderProviders([zed, ace]).map(p => p.slug)).toEqual(['ace', 'zed'])
  })

  it('does not mutate the array it was given', () => {
    const input = [GROQ, DEEPSEEK]

    orderProviders(input)

    expect(input[0]?.slug).toBe('groq')
  })
})

describe('credentialState', () => {
  it('distinguishes a key from a sign-in', () => {
    // "Key set" invites Replace; "Signed in" does not mean the same thing.
    expect(credentialState(DEEPSEEK)).toBe('Key set')
    expect(credentialState({ ...OPENAI, auth_type: 'subscription' })).toBe('Signed in')
    expect(credentialState({ ...OPENAI, auth_type: 'oauth' })).toBe('Signed in')
  })

  it('says so when there is nothing', () => {
    expect(credentialState(GROQ)).toBe('No key')
  })
})

describe('ProvidersSection', () => {
  const props = { onDisconnect: vi.fn(), onSave: vi.fn().mockResolvedValue(true) }

  it('marks the provider the session is running on', () => {
    render(<ProvidersSection {...props} providers={[DEEPSEEK]} />)

    expect(screen.getByText('Running')).toBeTruthy()
  })

  it('offers Add for an unconfigured provider and Replace for a configured one', () => {
    render(<ProvidersSection {...props} providers={[DEEPSEEK, GROQ]} />)

    expect(screen.getByRole('button', { name: 'Replace key' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add key' })).toBeTruthy()
  })

  it('offers Disconnect only where there is something to disconnect', () => {
    render(<ProvidersSection {...props} providers={[GROQ]} />)

    expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull()
  })

  it('names the environment variable a key could come from', () => {
    // A key exported in the shell cannot be removed from here, and this is the
    // only clue to where it came from.
    render(<ProvidersSection {...props} providers={[DEEPSEEK]} />)

    expect(screen.getByText('DEEPSEEK_API_KEY')).toBeTruthy()
  })

  it('masks the key field', () => {
    // Settings pages get screen-shared.
    render(<ProvidersSection {...props} providers={[GROQ]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add key' }))

    expect(screen.getByLabelText('API key for Groq').getAttribute('type')).toBe('password')
  })

  it('sends the slug and the typed key', async () => {
    const onSave = vi.fn().mockResolvedValue(true)
    render(<ProvidersSection onDisconnect={vi.fn()} onSave={onSave} providers={[GROQ]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add key' }))
    fireEvent.change(screen.getByLabelText('API key for Groq'), { target: { value: ' sk-abc ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(onSave).toHaveBeenCalledWith('groq', 'sk-abc')
  })

  it('will not save an empty key', () => {
    const onSave = vi.fn()
    render(<ProvidersSection onDisconnect={vi.fn()} onSave={onSave} providers={[GROQ]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add key' }))
    fireEvent.change(screen.getByLabelText('API key for Groq'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(onSave).not.toHaveBeenCalled()
  })

  it('clears the field after a save, so a secret is not left on screen', async () => {
    const onSave = vi.fn().mockResolvedValue(false)
    render(<ProvidersSection onDisconnect={vi.fn()} onSave={onSave} providers={[GROQ]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add key' }))
    const field = screen.getByLabelText('API key for Groq')
    fireEvent.change(field, { target: { value: 'sk-abc' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    // Even on failure: a failed save is a reason to retype, not to leave it up.
    await waitFor(() => {
      expect((screen.getByLabelText('API key for Groq') as HTMLInputElement).value).toBe('')
    })
  })

  it('reports the provider to disconnect by slug', () => {
    const onDisconnect = vi.fn()
    render(<ProvidersSection onDisconnect={onDisconnect} onSave={vi.fn()} providers={[DEEPSEEK]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }))

    expect(onDisconnect).toHaveBeenCalledWith('deepseek')
  })

  it('says so when there is nothing to show', () => {
    render(<ProvidersSection {...props} providers={[]} />)

    expect(screen.getByText('No providers to show.')).toBeTruthy()
  })
})

describe('providers that take no key', () => {
  // ollama, sglang and vllm are local endpoints: auth_type "none". There is
  // nothing to set and nothing to disconnect.
  const OLLAMA: ModelOption = {
    authenticated: true,
    auth_type: 'none',
    key_env: 'OLLAMA_API_KEY',
    name: 'Ollama (local)',
    slug: 'ollama',
    total_models: 1,
  }

  it('knows which providers take one', () => {
    expect(takesKey(OLLAMA)).toBe(false)
    expect(takesKey(DEEPSEEK)).toBe(true)
  })

  it('says so rather than claiming a key is set', () => {
    expect(credentialState(OLLAMA)).toBe('No key needed')
  })

  it('offers neither Add, Replace nor Disconnect', () => {
    render(<ProvidersSection onDisconnect={vi.fn()} onSave={vi.fn()} providers={[OLLAMA]} />)

    expect(screen.queryByRole('button', { name: /key/i })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull()
  })

  it('does not name an environment variable that is not used', () => {
    render(<ProvidersSection onDisconnect={vi.fn()} onSave={vi.fn()} providers={[OLLAMA]} />)

    expect(screen.queryByText('OLLAMA_API_KEY')).toBeNull()
  })
})
