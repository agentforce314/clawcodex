import css from './StateDot.module.css'

export type RunState = 'done' | 'error' | 'running' | 'warning'

export interface StateDotProps {
  label?: string
  size?: number
  state: RunState
}

const CHASE_CELLS = [
  { x: 0, y: 0 },
  { x: 5, y: 0 },
  { x: 5, y: 5 },
  { x: 0, y: 5 },
] as const

/**
 * The run-state glyph beside a tool row or a terminal card.
 *
 * Running is a four-cell chase rather than a spinner: it reads as activity at
 * 8px, where a spinner reads as a smudge. The label is visually hidden but
 * present — the state is otherwise carried by color and motion alone.
 */
export function StateDot({ label, size = 8, state }: StateDotProps) {
  if (state === 'running') {
    return (
      <>
        <svg
          aria-hidden="true"
          className={css.matrix}
          height={size + 2}
          viewBox="0 0 9 9"
          width={size + 2}
        >
          {CHASE_CELLS.map((cell, index) => (
            <rect
              className={css.cell}
              height="4"
              key={`${cell.x}-${cell.y}`}
              rx="1"
              style={{ animationDelay: `${index * -125}ms` }}
              width="4"
              x={cell.x}
              y={cell.y}
            />
          ))}
        </svg>
        {label !== undefined && <span className="cc-sr-only">{label}</span>}
      </>
    )
  }

  return (
    <>
      <span
        aria-hidden="true"
        className={css.dot}
        data-state={state}
        style={{ height: size, width: size }}
      />
      {label !== undefined && <span className="cc-sr-only">{label}</span>}
    </>
  )
}
