import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Menu } from './Menu.tsx'

afterEach(cleanup)

describe('Menu', () => {
  it('draws footer rows below the scrolling list, after a divider, never checked', () => {
    const onSelect = vi.fn()

    render(
      <Menu
        anchor={<button type="button">Pick</button>}
        footer={[{ id: 'add', label: 'Add…' }]}
        items={[
          { id: 'a', label: 'A' },
          { id: 'b', label: 'B' },
        ]}
        onClose={() => {}}
        onSelect={onSelect}
        open
        selectedId="add"
      />,
    )

    const menu = screen.getByRole('menu')
    const viewport = menu.querySelector('[class*="viewport"]') as HTMLElement
    const add = within(menu).getByRole('menuitem', { name: 'Add…' })

    expect(within(viewport).getAllByRole('menuitem').map(row => row.textContent)).toEqual(['A', 'B'])
    expect(viewport.contains(add)).toBe(false)
    expect(add.previousElementSibling?.getAttribute('role')).toBe('separator')
    // A footer row is an action, not the selection: no check even when its
    // id happens to be the selected one.
    expect(add.querySelector('svg')).toBeNull()

    fireEvent.click(add)
    expect(onSelect).toHaveBeenCalledWith('add')
  })

  it('shows no divider when there is no footer', () => {
    render(
      <Menu anchor={<button type="button">Pick</button>} items={[{ id: 'a', label: 'A' }]} onClose={() => {}} onSelect={() => {}} open />,
    )

    expect(screen.queryByRole('separator')).toBeNull()
  })
})
