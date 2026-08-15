import { useEffect, useState } from 'react'

import { highlightToHtml, normalizeLanguage } from '../markdown/highlight.ts'
import { CopyButton } from './CopyButton.tsx'
import css from './CodeBlock.module.css'

export interface CodeBlockProps {
  className?: string
  code: string
  language?: string
  /**
   * False while the fence is still growing at the tail of a streaming reply.
   * An unsettled block renders plain text: highlighting is async, so a block
   * re-highlighted per delta would keep showing the PREVIOUS delta's colored
   * copy — the reader watches code that lags the stream by one round trip.
   * The one swap to colored happens when the text is final.
   */
  settled?: boolean
}

/**
 * A fenced code block: sticky banner (language + copy) over the source.
 *
 * Highlighting is progressive by design — the plain text paints immediately
 * and is replaced when the grammar chunk lands. A block must never fail to
 * display over a missing grammar.
 */
export function CodeBlock({ className, code, language, settled = true }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null)
  const resolved = normalizeLanguage(language)

  useEffect(() => {
    if (resolved === undefined || !settled) {
      setHtml(null)

      return
    }

    let cancelled = false

    void highlightToHtml(code, resolved).then(result => {
      if (!cancelled) setHtml(result)
    })

    return () => {
      cancelled = true
    }
  }, [code, resolved, settled])

  return (
    <div className={[css.block, className].filter(Boolean).join(' ')}>
      <div className={css.bannerWrap}>
        <div className={css.banner}>
          <span className={css.infostring}>{resolved ?? language ?? 'text'}</span>
          <CopyButton className={css.copyButton} text={code} />
        </div>
      </div>
      {html === null ? (
        <pre className={css.pre}>
          <code>{code}</code>
        </pre>
      ) : (
        // Shiki output only: the input is the model's code text, escaped by
        // shiki's own HTML encoder before it ever reaches this attribute.
        <div className={css.pre} dangerouslySetInnerHTML={{ __html: html }} />
      )}
    </div>
  )
}
