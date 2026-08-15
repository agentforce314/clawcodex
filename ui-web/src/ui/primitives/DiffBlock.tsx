import { useMemo, useState } from 'react'

import { CopyButton } from './CopyButton.tsx'
import { headTailCap, splitByCap } from './head-tail-cap.ts'
import css from './DiffBlock.module.css'

export interface DiffBlockProps {
  className?: string
  /** Unified diff, as produced by the gateway's `inline_diff` result field. */
  diff: string
}

type DiffLineKind = 'add' | 'context' | 'del' | 'gap' | 'path'

interface DiffLine {
  kind: DiffLineKind
  text: string
}

const HEAD_LINES = 60

/**
 * Parse a unified diff into display lines.
 *
 * The `+`/`-` markers are stripped here and re-drawn by CSS, so the copy
 * affordance yields the same text the reader sees. `@@` hunk headers become a
 * neutral gap marker: the line numbers are noise in a review context, but the
 * *fact* of a jump is not.
 */
function parseDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = []

  for (const raw of diff.split('\n')) {
    if (raw.startsWith('+++ ')) {
      out.push({ kind: 'path', text: raw.slice(4) })
      continue
    }

    // The `---` side is redundant beside `+++` for the single-file diffs the
    // agent produces, and a "/dev/null" line reads as an error to a human.
    if (raw.startsWith('--- ')) continue

    if (raw.startsWith('@@')) {
      out.push({ kind: 'gap', text: '⋯' })
      continue
    }

    if (raw.startsWith('+')) {
      out.push({ kind: 'add', text: raw.slice(1) })
      continue
    }

    if (raw.startsWith('-')) {
      out.push({ kind: 'del', text: raw.slice(1) })
      continue
    }

    out.push({ kind: 'context', text: raw.startsWith(' ') ? raw.slice(1) : raw })
  }

  // A trailing blank from the split is not a diff line.
  while (out.length > 0 && out[out.length - 1]?.text === '' && out[out.length - 1]?.kind === 'context') {
    out.pop()
  }

  return out
}

export function DiffBlock({ className, diff }: DiffBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const lines = useMemo(() => parseDiff(diff), [diff])

  const added = lines.filter(line => line.kind === 'add').length
  const removed = lines.filter(line => line.kind === 'del').length
  const cap = headTailCap(lines.length, HEAD_LINES)
  const folded = cap.hidden > 0 && !expanded
  const { head, tail } = folded ? splitByCap(lines, cap) : { head: lines, tail: [] }

  const row = (line: DiffLine, key: string) => (
    <div className={[css.line, css[line.kind]].filter(Boolean).join(' ')} key={key}>
      {line.text === '' ? ' ' : line.text}
    </div>
  )

  return (
    <div className={[css.block, className].filter(Boolean).join(' ')}>
      <CopyButton className={css.copyButton} text={diff} />
      <div className={css.body}>
        {head.map((line, index) => row(line, `h${index}`))}
        {cap.hidden > 0 && (
          <button
            aria-expanded={expanded}
            className={css.expand}
            onClick={() => {
              setExpanded(value => !value)
            }}
            type="button"
          >
            {expanded ? 'collapse' : `… ${cap.hidden} more lines`}
          </button>
        )}
        {tail.map((line, index) => row(line, `t${index}`))}
      </div>
      <div className={css.footer}>
        └ +{added} −{removed}
      </div>
    </div>
  )
}
