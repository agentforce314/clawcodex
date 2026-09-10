import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { formatTokens } from '../state/trajectory.ts'
import {
  describeStatus,
  formatRunDuration,
  subagentCounts,
  type SubagentEntry,
} from '../state/subagents.ts'
import { ChevronDownIcon, SwitchIcon } from '../ui/icons.tsx'
import { StateDot, type RunState } from '../ui/primitives/StateDot.tsx'
import css from './SubagentChip.module.css'

export interface SubagentChipProps {
  /** The entry the column is showing, for the switcher's highlight. */
  currentKey?: string | null
  entries: readonly SubagentEntry[]
  onOpen: (key: string) => void
  /**
   * `count` is the parent's "N subagents ▾"; `switcher` is the child's own
   * name with a way to its siblings.
   */
  variant: 'count' | 'switcher'
}

function dotState(entry: SubagentEntry): RunState {
  switch (entry.status) {
    case 'running':
      return 'running'
    case 'failed':
      return 'error'
    case 'interrupted':
    case 'killed':
      return 'warning'
    default:
      return 'done'
  }
}

/** `Explore · deepseek-v4-flash · running` — the line under a row's label. */
function secondaryLine(entry: SubagentEntry): string {
  return [entry.subagentType, entry.model, entry.activity ?? describeStatus(entry.status)]
    .filter((part): part is string => part !== undefined && part !== '')
    .join(' · ')
}

function countLabel(total: number, running: number): string {
  const noun = total === 1 ? 'subagent' : 'subagents'

  return running > 0 ? `${total} ${noun}, ${running} running` : `${total} ${noun}`
}

/**
 * The header's subagent control: how many there are, with the list behind it.
 *
 * Reads the catalog the conversation state folds (`subagentCatalog`); it
 * neither polls nor holds agents of its own. Rendered on the session title as
 * a count, and on a child's title as a switcher between siblings — the same
 * list, the two ways the reference client offers it.
 */
export function SubagentChip({ currentKey, entries, onOpen, variant }: SubagentChipProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement | null>(null)
  const { running, total } = subagentCounts(entries)
  const current = currentKey === undefined || currentKey === null
    ? undefined
    : entries.find(entry => entry.key === currentKey)

  useEffect(() => {
    if (!open) return

    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node) === true) return

      setOpen(false)
    }

    document.addEventListener('pointerdown', onPointerDown)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [open])

  // A session with no delegations has nothing to count; the switcher, once
  // there is a child to show, always has at least that child.
  if (total === 0 && variant === 'count') return null

  const onKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    if (event.key === 'Escape' && open) {
      event.stopPropagation()
      setOpen(false)
    }
  }

  return (
    <span className={css.root} onKeyDown={onKeyDown} ref={root}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={
          variant === 'switcher'
            ? `Subagent: ${current?.label ?? 'subagent'}`
            : countLabel(total, running)
        }
        className={variant === 'switcher' ? css.switcher : css.trigger}
        onClick={() => {
          setOpen(value => !value)
        }}
        type="button"
      >
        {variant === 'switcher' ? (
          <>
            <span className={css.switcherTitle}>{current?.label ?? 'subagent'}</span>
            <SwitchIcon size={14} />
          </>
        ) : (
          <>
            {running > 0 && (
              <span className={css.activity}>
                <StateDot state="running" />
              </span>
            )}
            <span>{`${total} ${total === 1 ? 'subagent' : 'subagents'}`}</span>
            <ChevronDownIcon className={open ? css.chevronOpen : undefined} size={14} />
          </>
        )}
      </button>
      {open && (
        <div aria-label="Subagents" className={css.menu} role="listbox">
          {entries.map(entry => {
            const selected = entry.key === currentKey
            const secondary = secondaryLine(entry)
            const tokens = entry.tokens === undefined ? undefined : `${formatTokens(entry.tokens)} tok`
            const duration =
              entry.durationMs === undefined ? undefined : formatRunDuration(entry.durationMs)

            return (
              <button
                aria-selected={selected}
                className={[css.row, selected ? css.rowCurrent : ''].filter(Boolean).join(' ')}
                key={entry.key}
                onClick={() => {
                  setOpen(false)
                  onOpen(entry.key)
                }}
                role="option"
                type="button"
              >
                <span className={css.rowDot}>
                  <StateDot label={describeStatus(entry.status)} state={dotState(entry)} />
                </span>
                <span className={css.rowBody}>
                  <span className={css.rowLabel}>{entry.label}</span>
                  {secondary !== '' && <span className={css.rowSecondary}>{secondary}</span>}
                </span>
                {(tokens !== undefined || duration !== undefined) && (
                  <span className={css.rowMetrics}>
                    {tokens !== undefined && <span>{tokens}</span>}
                    {duration !== undefined && <span>{duration}</span>}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </span>
  )
}
