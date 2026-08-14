import { useMemo, useState } from 'react'

import type { EffortOptionsResult } from '../gateway/protocol.ts'
import { Menu, type MenuEntry } from '../ui/primitives/Menu.tsx'
import { ChevronDownIcon } from '../ui/icons.tsx'
import css from './Pickers.module.css'

/** What each rung actually buys, in the order the ladder climbs. */
const HINTS: Record<string, string> = {
  auto: 'Let the provider decide',
  low: 'Fastest, least thinking',
  medium: 'Balanced',
  high: 'More thinking, slower',
  xhigh: 'Much more thinking',
  max: 'Everything the model has',
}

/** Title case for a rung the hint table has never heard of. */
function label(level: string): string {
  if (level === 'xhigh') return 'X-High'

  return level.charAt(0).toUpperCase() + level.slice(1)
}

/**
 * Menu rows for the ladder, with `auto` on top.
 *
 * `effort_options` documents that it never returns `auto` and that "the caller
 * prepends it, since 'let the provider decide' is meaningful exactly when some
 * real level is also on offer" — so it is added here, and only here.
 */
export function buildEffortMenu(levels: string[]): MenuEntry[] {
  const rungs = levels.filter(level => level !== 'auto')

  if (rungs.length === 0) return []

  return ['auto', ...rungs].map(level => ({
    hint: HINTS[level],
    id: level,
    label: label(level),
  }))
}

export interface EffortSelectProps {
  disabled?: boolean
  onChange: (effort: string) => void
  options: EffortOptionsResult
}

/**
 * The reasoning-effort chip, beside the model it belongs to.
 *
 * Renders nothing at all when the model reports `supported: false`. That is
 * the contract's own instruction — a model that takes no effort parameter
 * would show a picker whose every choice is silently dropped — and it is why
 * the chip comes and goes as the model changes.
 */
export function EffortSelect({ disabled = false, onChange, options }: EffortSelectProps) {
  const [open, setOpen] = useState(false)

  const items = useMemo(() => buildEffortMenu(options.levels ?? []), [options.levels])

  if (options.supported !== true || items.length === 0) return null

  const current = options.current ?? ''
  // An unset level IS "auto": the provider is deciding because nothing told it
  // otherwise, so the menu marks the row that describes what is happening.
  const selectedId = current === '' ? 'auto' : current

  return (
    <Menu
      align="end"
      anchor={
        <button
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={`Reasoning effort: ${label(selectedId)}`}
          className={css.trigger}
          disabled={disabled}
          onClick={() => {
            setOpen(value => !value)
          }}
          title={HINTS[selectedId] ?? 'Reasoning effort'}
          type="button"
        >
          <span className={css.triggerLabel}>{label(selectedId)}</span>
          <span className={[css.chevron, open ? css.chevronOpen : ''].filter(Boolean).join(' ')}>
            <ChevronDownIcon size={12} />
          </span>
        </button>
      }
      items={items}
      onClose={() => {
        setOpen(false)
      }}
      onSelect={id => {
        setOpen(false)

        if (id !== selectedId) onChange(id)
      }}
      open={open}
      selectedId={selectedId}
      side="top"
    />
  )
}
