import type { ReactNode } from 'react'

import css from './Pill.module.css'

export interface PillProps {
  active?: boolean
  children: ReactNode
  className?: string
  onClick?: () => void
  title?: string
  tone?: 'default' | 'error'
}

/** Small status/label capsule. Interactive only when given an `onClick`. */
export function Pill({ active, children, className, onClick, title, tone = 'default' }: PillProps) {
  const classes = [
    css.pill,
    onClick !== undefined ? css.interactive : '',
    active === true ? css.active : '',
    tone === 'error' ? css.error : '',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  if (onClick === undefined) {
    return (
      <span className={classes} title={title}>
        {children}
      </span>
    )
  }

  return (
    <button className={classes} onClick={onClick} title={title} type="button">
      {children}
    </button>
  )
}
