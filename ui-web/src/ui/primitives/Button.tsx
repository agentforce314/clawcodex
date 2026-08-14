import type { ButtonHTMLAttributes, ReactNode } from 'react'

import css from './Button.module.css'

export type ButtonVariant = 'ghost' | 'outline' | 'primary'
export type ButtonSize = 'md' | 'sm'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: ReactNode
  size?: ButtonSize
  variant?: ButtonVariant
}

/**
 * The one button. Variants and sizes own their padding, radius and chrome —
 * call sites pass a `variant`, never a className that re-specifies those.
 */
export function Button({
  children,
  className,
  icon,
  size = 'md',
  type = 'button',
  variant = 'ghost',
  ...rest
}: ButtonProps) {
  const classes = [css.button, css[size], css[variant], className].filter(Boolean).join(' ')

  return (
    <button className={classes} type={type} {...rest}>
      {icon !== undefined && <span className={css.icon}>{icon}</span>}
      {children}
    </button>
  )
}
