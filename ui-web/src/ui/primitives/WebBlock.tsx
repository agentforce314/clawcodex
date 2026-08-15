import { useMemo, useState } from 'react'

import { linkifyText } from '../linkify.tsx'
import { CopyButton } from './CopyButton.tsx'
import { headTailCap, splitByCap } from './head-tail-cap.ts'
import css from './WebBlock.module.css'

export interface WebBlockProps {
  className?: string
  /** Search results or extracted page text; URLs inside become links. */
  text: string
  /** The fetched address, shown as the card's source line. */
  url?: string
}

const MAX_LINES = 40

/**
 * Card for the web tools. The one thing a search result card owes the reader
 * that a generic output card does not: its references are FOLLOWABLE — every
 * URL is an anchor, and a fetch names its source at the top.
 */
export function WebBlock({ className, text, url }: WebBlockProps) {
  const [expanded, setExpanded] = useState(false)

  const lines = useMemo(() => text.replace(/\n$/, '').split('\n'), [text])
  const cap = headTailCap(lines.length, MAX_LINES)
  const folded = cap.hidden > 0 && !expanded
  const { head, tail } = folded ? splitByCap(lines, cap) : { head: lines, tail: [] }

  const row = (line: string, key: string) => (
    <div className={css.line} key={key}>
      {line === '' ? ' ' : linkifyText(line, key)}
    </div>
  )

  return (
    <div className={[css.block, className].filter(Boolean).join(' ')}>
      <div className={css.banner}>
        {url === undefined || url === '' ? (
          <span className={css.label}>results</span>
        ) : (
          <a className={css.source} href={url} rel="noopener noreferrer" target="_blank">
            {url}
          </a>
        )}
        <CopyButton className={css.copyButton} text={text} />
      </div>
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
    </div>
  )
}
