import { SearchIcon } from '../ui/icons.tsx'
import css from './TrajectoryToolbar.module.css'

export interface TrajectoryToolbarProps {
  /** True when timeline blocks use recorded durations instead of equal widths. */
  actualDuration: boolean
  allStepsCollapsed: boolean
  allTurnsCollapsed: boolean
  /** Matches for the current query, or null when there is no query. */
  matchCount: number | null
  onActualDurationChange: (actualDuration: boolean) => void
  onSearchQueryChange: (query: string) => void
  onToggleAllSteps: () => void
  onToggleAllTurns: () => void
  searchQuery: string
}

/**
 * The trajectory's control strip.
 *
 * `Duration` is the consequential one: it switches the timeline between
 * one-block-per-operation (structure — how many steps, in what order) and
 * true elapsed widths (cost — where the time actually went). They answer
 * different questions, so it is a toggle rather than a default.
 */
export function TrajectoryToolbar({
  actualDuration,
  allStepsCollapsed,
  allTurnsCollapsed,
  matchCount,
  onActualDurationChange,
  onSearchQueryChange,
  onToggleAllSteps,
  onToggleAllTurns,
  searchQuery,
}: TrajectoryToolbarProps) {
  return (
    <div aria-label="Trajectory toolbar" className={css.root} role="toolbar">
      <div className={css.inner}>
        <div className={css.actions}>
          <button
            aria-pressed={actualDuration}
            className={css.action}
            onClick={() => {
              onActualDurationChange(!actualDuration)
            }}
            title={actualDuration ? 'Show equal-width operations' : 'Show actual durations'}
            type="button"
          >
            <svg
              aria-hidden="true"
              className={css.actionIcon}
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeWidth={1.5}
              viewBox="0 0 16 16"
            >
              <circle cx="8" cy="8" r="5.25" />
              <path d="M8 4.75V8l2.25 1.5" />
            </svg>
            Duration
          </button>
          <button
            aria-pressed={allTurnsCollapsed}
            className={css.action}
            onClick={onToggleAllTurns}
            title={allTurnsCollapsed ? 'Expand every turn' : 'Collapse every turn'}
            type="button"
          >
            <span aria-hidden="true" className={css.actionIcon}>
              {allTurnsCollapsed ? '⊞' : '⊟'}
            </span>
            Turns
          </button>
          <button
            aria-pressed={allStepsCollapsed}
            className={css.action}
            onClick={onToggleAllSteps}
            title={allStepsCollapsed ? 'Expand every step' : 'Collapse tool calls under each step'}
            type="button"
          >
            <span aria-hidden="true" className={css.actionIcon}>
              {allStepsCollapsed ? '⊞' : '⊟'}
            </span>
            Calls
          </button>
        </div>
        <div className={css.search}>
          <SearchIcon size={11} />
          <input
            aria-label="Search the trajectory"
            className={css.searchInput}
            onChange={event => {
              onSearchQueryChange(event.currentTarget.value)
            }}
            placeholder="Search"
            type="search"
            value={searchQuery}
          />
          {matchCount !== null && <span className={css.count}>{matchCount}</span>}
        </div>
      </div>
    </div>
  )
}
