import type { CSSProperties, ReactNode } from 'react'

import type { AnsiLine, AnsiSpan } from '../ansi.ts'
import css from './AnsiText.module.css'

/**
 * Styled runs of one parsed terminal line.
 *
 * Palette colors (0–15) resolve through the `--cc-ansi-*` tokens so both
 * themes pick readable tones; 256/truecolor values arrive as CSS colors and
 * are applied inline — they are the tool's own choice, not the theme's.
 */
function spanNode(span: AnsiSpan, key: number): ReactNode {
  const classes: string[] = []
  const style: CSSProperties = {}

  if (typeof span.fg === 'number') classes.push(css[`fg${span.fg}`] ?? '')
  else if (typeof span.fg === 'string') style.color = span.fg

  if (typeof span.bg === 'number') classes.push(css[`bg${span.bg}`] ?? '')
  else if (typeof span.bg === 'string') style.backgroundColor = span.bg

  if (span.bold === true) classes.push(css.bold ?? '')
  if (span.dim === true) classes.push(css.dim ?? '')
  if (span.italic === true) classes.push(css.italic ?? '')
  if (span.underline === true) classes.push(css.underline ?? '')

  const className = classes.filter(Boolean).join(' ')

  if (className === '' && span.fg === undefined && span.bg === undefined) {
    return span.text
  }

  return (
    <span className={className === '' ? undefined : className} key={key} style={style}>
      {span.text}
    </span>
  )
}

export function AnsiSpans({ line }: { line: AnsiLine }) {
  if (line.length === 0) return ' '

  return <>{line.map((span, index) => spanNode(span, index))}</>
}
