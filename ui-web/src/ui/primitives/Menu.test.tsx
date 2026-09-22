import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MENU_MAX_HEIGHT, Menu, menuPlacement } from './Menu.tsx'

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
    expect(add.querySelector('svg[class*="check"]')).toBeNull()

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

describe('menuPlacement', () => {
  it('caps the list to the room on its side, never past the design height', () => {
    expect(menuPlacement('bottom', 300, 900)).toEqual({ maxHeight: MENU_MAX_HEIGHT, side: 'bottom' })
    expect(menuPlacement('bottom', 300, 260)).toEqual({ maxHeight: 260, side: 'bottom' })
    expect(menuPlacement('top', 350, 900)).toEqual({ maxHeight: 350, side: 'top' })
  })

  it('opens on the other side when its own has too little room and the other has more', () => {
    // A control in the lower half of a short window: below is cramped.
    expect(menuPlacement('bottom', 500, 120)).toEqual({ maxHeight: MENU_MAX_HEIGHT, side: 'top' })
    // A composer picker at the bottom of the window keeps opening upward.
    expect(menuPlacement('top', 600, 40)).toEqual({ maxHeight: MENU_MAX_HEIGHT, side: 'top' })
    // Cramped on both sides: stay put, but never smaller than a few rows.
    expect(menuPlacement('bottom', 60, 90)).toEqual({ maxHeight: 120, side: 'bottom' })
  })
})
