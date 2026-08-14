import { useEffect, useRef, useState } from 'react'

import type { ContextUsageResult } from '../gateway/protocol.ts'
import css from './ContextMeter.module.css'

export interface ContextMeterProps {
  usage: ContextUsageResult | null
}

/** A ring is only legible above a few percent; below that it reads as empty. */
const RADIUS = 8
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

const TINTS = ['var(--cc-static-ink-400)', 'rgb(167, 139, 250)', 'var(--cc-static-blue-450)']

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${Math.round(value / 1_000)}k`

  return String(value)
}

/**
 * How much of the context window this session is holding.
 *
 * The ring is the resting form — a number would demand reading on every
 * glance. The breakdown (which categories hold the tokens) is one click away,
 * because that is the part a user acts on when they decide to compact.
 */
export function ContextMeter({ usage }: ContextMeterProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return

    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node) === true) return

      setOpen(false)
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }

    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (usage === null) return null

  const max = usage.max_tokens ?? 0
  const total = usage.total_tokens ?? 0
  const percent =
    usage.percentage !== undefined ? usage.percentage : max > 0 ? (total / max) * 100 : undefined

  if (percent === undefined) return null

  const clamped = Math.max(0, Math.min(100, percent))
  const breakdown = (usage.categories ?? []).filter(category => category.tokens > 0)
  const tone = clamped >= 90 ? css.critical : clamped >= 75 ? css.high : ''

  return (
    <div className={[css.root, tone].filter(Boolean).join(' ')} ref={root}>
      <button
        aria-label={`Context: ${Math.round(clamped)}% used`}
        className={css.trigger}
        onClick={() => {
          setOpen(value => !value)
        }}
        title={`Context: ${Math.round(clamped)}% of ${formatTokens(max)}`}
        type="button"
      >
        <svg height="20" viewBox="0 0 20 20" width="20">
          <circle className={css.track} cx="10" cy="10" r={RADIUS} />
          <circle
            className={css.fill}
            cx="10"
            cy="10"
            r={RADIUS}
            strokeDasharray={`${(clamped / 100) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
            transform="rotate(-90 10 10)"
          />
        </svg>
      </button>
      {open && (
        <div className={css.panel}>
          <div className={css.header}>
            <span>Context</span>
            <span className={css.figures}>
              {formatTokens(total)}
              {max > 0 && ` / ${formatTokens(max)}`}
            </span>
          </div>
          <div className={css.bar}>
            {breakdown.length === 0 ? (
              <span className={css.segment} style={{ width: `${clamped}%` }} />
            ) : (
              breakdown.map((category, index) => (
                <span
                  className={css.segment}
                  key={category.name}
                  style={
                    {
                      '--meter-tint': TINTS[index % TINTS.length],
                      width: max > 0 ? `${(category.tokens / max) * 100}%` : '0%',
                    } as React.CSSProperties
                  }
                />
              ))
            )}
          </div>
          <dl className={css.rows}>
            {breakdown.map((category, index) => (
              <div className={css.row} key={category.name}>
                <dt>
                  <span
                    className={css.swatch}
                    style={{ '--meter-tint': TINTS[index % TINTS.length] } as React.CSSProperties}
                  />
                  {category.name}
                </dt>
                <dd>{formatTokens(category.tokens)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}
