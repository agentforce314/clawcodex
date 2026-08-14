import type { ReactNode } from 'react'

import { ChevronDownIcon, ChevronRightIcon } from '../icons.tsx'
import type { RunState } from './StateDot.tsx'
import css from './DisclosureRow.module.css'

export interface DisclosureRowProps {
  body?: ReactNode
  expanded?: boolean
  /** The glyph at rest; it cross-fades to a chevron on hover when expandable. */
  icon: ReactNode
  onToggle?: () => void
  state?: RunState
  summary?: ReactNode
  summaryTone?: 'default' | 'error'
  title: string
  trailing?: ReactNode
}

/**
 * The one-line row every tool call, reasoning block and compaction marker is
 * built from: a 24px line reading `[icon] Title · summary`, with the body
 * disclosed underneath.
 *
 * The whole line is the hit target when it can expand, and the leading glyph
 * doubles as the disclosure affordance — so the row costs no extra chevron
 * column in the flow's left margin.
 */
export function DisclosureRow({
  body,
  expanded = false,
  icon,
  onToggle,
  state,
  summary,
  summaryTone = 'default',
  title,
  trailing,
}: DisclosureRowProps) {
  const expandable = onToggle !== undefined && body !== undefined

  return (
    <div className={css.root} data-state={state}>
      <button
        aria-expanded={expandable ? expanded : undefined}
        className={css.row}
        data-expandable={expandable ? '' : undefined}
        data-expanded={expanded ? '' : undefined}
        disabled={!expandable}
        onClick={onToggle}
        type="button"
      >
        <span className={css.leading}>
          <span className={css.iconIdle}>{icon}</span>
          {expandable && (
            <span className={css.chevron}>
              {expanded ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
            </span>
          )}
        </span>
        <span className={css.title}>{title}</span>
        {summary !== undefined && summary !== '' && (
          <>
            <span className={css.sep} />
            <span
              className={[css.summary, summaryTone === 'error' ? css.summaryError : '']
                .filter(Boolean)
                .join(' ')}
            >
              {summary}
            </span>
          </>
        )}
        {trailing !== undefined && <span className={css.trailing}>{trailing}</span>}
      </button>
      {expandable && expanded && <div className={css.body}>{body}</div>}
    </div>
  )
}
