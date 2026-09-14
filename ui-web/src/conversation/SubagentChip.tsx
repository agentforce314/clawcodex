import { useStore } from '@nanostores/react'
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'

import { fetchDelegationStatus, interruptSubagent, setDelegationPaused } from '../state/actions.ts'
import { $delegation } from '../state/store.ts'
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

/** How often the open list re-reads the supervisor: the cap and the pause state. */
const POLL_MS = 2_000

/** The list's width, and the least it keeps from the viewport's edges. */
const MENU_WIDTH = 336
const MENU_INSET = 16

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
 * neither polls for rows nor holds agents of its own. Rendered on the session
 * title as a count, and on a child's title as a switcher between siblings —
 * the same list, the two ways the reference client offers it.
 *
 * It is also the one place a run is stopped: a running row carries Stop, and
 * the foot says how many are running against the session's cap, with the
 * switch that pauses new spawns. The reference puts Stop on a continuable
 * child's composer; here the runs are one-shot, so their controls live with
 * the catalog rather than in a tab of their own beside it.
 */
export function SubagentChip({ currentKey, entries, onOpen, variant }: SubagentChipProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement | null>(null)
  const delegation = useStore($delegation)
  // The list is fixed to the viewport rather than hung off the chip: the
  // conversation column clips what overflows it, and a narrow column would
  // cut the list's right edge off. Placed under the chip, and pulled left
  // when the chip sits too close to the viewport's edge for the width.
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    if (!open) return

    const measure = (): void => {
      const rect = root.current?.getBoundingClientRect()

      if (rect === undefined) return

      const width = Math.min(MENU_WIDTH, window.innerWidth - 2 * MENU_INSET)

      setPlace({
        left: Math.max(MENU_INSET, Math.min(rect.left, window.innerWidth - MENU_INSET - width)),
        top: rect.bottom + 4,
      })
    }

    measure()
    window.addEventListener('resize', measure)

    return () => {
      window.removeEventListener('resize', measure)
    }
  }, [open])
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

  // The supervisor's snapshot — the cap, whether spawning is paused — is read
  // while the list is open, and only then: the rows themselves are live from
  // the transcript's own events.
  useEffect(() => {
    if (!open) return

    void fetchDelegationStatus()

    const timer = setInterval(() => {
      void fetchDelegationStatus()
    }, POLL_MS)

    return () => {
      clearInterval(timer)
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

  const paused = delegation?.paused === true
  const cap = delegation?.max_concurrent_children

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
      {open && place !== null && (
        <div
          aria-label="Subagents"
          className={css.menu}
          role="listbox"
          style={{ left: place.left, top: place.top }}
        >
          {entries.map(entry => {
            const selected = entry.key === currentKey
            const secondary = secondaryLine(entry)
            const tokens = entry.tokens === undefined ? undefined : `${formatTokens(entry.tokens)} tok`
            const duration =
              entry.durationMs === undefined ? undefined : formatRunDuration(entry.durationMs)
            const stoppable = entry.status === 'running'

            return (
              <div
                aria-selected={selected}
                className={[css.row, selected ? css.rowCurrent : ''].filter(Boolean).join(' ')}
                key={entry.key}
                role="option"
              >
                <button
                  className={css.rowOpen}
                  onClick={() => {
                    setOpen(false)
                    onOpen(entry.key)
                  }}
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
                {stoppable && (
                  <button
                    aria-label={`Stop ${entry.label}`}
                    className={css.rowStop}
                    // A run names itself in its first progress frame; until
                    // then there is no id to interrupt.
                    disabled={entry.agentId === undefined}
                    onClick={() => {
                      if (entry.agentId !== undefined) void interruptSubagent(entry.agentId)
                    }}
                    title={
                      entry.agentId === undefined
                        ? 'This run has not reported its id yet'
                        : 'Interrupt this agent'
                    }
                    type="button"
                  >
                    Stop
                  </button>
                )}
              </div>
            )
          })}
          <div className={css.foot}>
            <span className={css.footCount}>
              {running} running
              {/* A missing cap is unknown, not zero — "of 0" would read as a
                  session that can never delegate. */}
              {typeof cap === 'number' ? ` of ${cap}` : ''}
            </span>
            <button
              aria-pressed={paused}
              className={[css.pause, paused ? css.pauseOn : ''].filter(Boolean).join(' ')}
              onClick={() => {
                void setDelegationPaused(!paused)
              }}
              title={
                paused
                  ? 'Allow this session to spawn new agents again'
                  : 'Stop this session spawning new agents; running ones continue'
              }
              type="button"
            >
              {paused ? 'Spawning paused' : 'Pause spawning'}
            </button>
          </div>
        </div>
      )}
    </span>
  )
}
