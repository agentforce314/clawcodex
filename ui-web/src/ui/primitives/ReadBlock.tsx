import { useMemo, useState } from 'react'

import { CopyButton } from './CopyButton.tsx'
import css from './ReadBlock.module.css'

export interface ReadBlockProps {
  className?: string
  /** File contents. `cat -n` style numbering is detected and reused. */
  content: string
  label?: string
}

const HEAD_LINES = 40
const NUMBERED = /^\s*(\d+)\t(.*)$/

interface ReadLine {
  number: number
  text: string
}

/**
 * Split file content into numbered lines.
 *
 * The agent's Read tool emits `N\ttext` rows, so the numbers are the file's
 * own — not a re-count of a window that started at line 400. Content without
 * that shape falls back to 1-based numbering of what is present.
 */
function parseLines(content: string): ReadLine[] {
  const raw = content.replace(/\n$/, '').split('\n')

  return raw.map((line, index) => {
    const match = NUMBERED.exec(line)

    if (match === null) return { number: index + 1, text: line }

    return { number: Number.parseInt(match[1] ?? '0', 10), text: match[2] ?? '' }
  })
}

export function ReadBlock({ className, content, label }: ReadBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const lines = useMemo(() => parseLines(content), [content])

  const overflowing = lines.length > HEAD_LINES && !expanded
  const shown = overflowing ? lines.slice(0, HEAD_LINES) : lines
  const plain = useMemo(() => lines.map(line => line.text).join('\n'), [lines])

  return (
    <div className={[css.block, className].filter(Boolean).join(' ')}>
      <div className={css.banner}>
        <span className={css.label} title={label}>
          {label ?? 'file'}
        </span>
        <span className={css.action}>
          <span className={css.count}>
            {lines.length} {lines.length === 1 ? 'line' : 'lines'}
          </span>
          <CopyButton className={css.copyButton} text={plain} />
        </span>
      </div>
      <div className={css.body}>
        {shown.map(line => (
          <div className={css.line} key={line.number}>
            <span className={css.gutter}>{line.number}</span>
            <span className={css.content}>{line.text === '' ? ' ' : line.text}</span>
          </div>
        ))}
        {overflowing && (
          <button
            className={css.expand}
            onClick={() => {
              setExpanded(true)
            }}
            type="button"
          >
            … show {lines.length - HEAD_LINES} more lines
          </button>
        )}
      </div>
    </div>
  )
}
