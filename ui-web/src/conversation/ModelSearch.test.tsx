import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { buildModelMenu, ModelSelect } from './ModelSelect.tsx'

afterEach(cleanup)
const models = { providers: [
  { name: 'Direct', slug: 'direct', models: ['alpha-fast', 'alpha-pro', 'beta'] },
  { name: 'Proxy', slug: 'proxy', models: ['alpha-pro'] },
] }

it('ranks fuzzy names within providers and retains the exact provider identity', () => {
  expect([...buildModelMenu(models, 'apr').choices.values()]).toEqual([
    { model: 'alpha-pro', provider: 'direct' }, { model: 'alpha-pro', provider: 'proxy' },
  ])
  expect(buildModelMenu(models, 'absent').items).toEqual([])
})

it('searches with the keyboard, announces no matches, and waits for the switch', async () => {
  let finish!: () => void
  const change = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
  render(<ModelSelect models={models} onChange={change} />)
  fireEvent.click(screen.getByRole('button', { name: 'Model: Model' }))
  const search = screen.getByRole('combobox', { name: 'Search models' })
  expect(document.activeElement).toBe(search)
  fireEvent.change(search, { target: { value: 'missing' } })
  expect(screen.getByText('No matching models.')).toBeTruthy()
  fireEvent.change(search, { target: { value: 'apr' } })
  fireEvent.keyDown(search, { key: 'Enter' })
  expect(change).toHaveBeenCalledWith('alpha-pro', 'direct')
  expect(screen.getByText('Switching model…')).toBeTruthy()
  finish()
  await waitFor(() => expect(screen.queryByText('Switching model…')).toBeNull())
})
