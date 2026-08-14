import { useEffect, useState } from 'react'

import { highlightToHtml, normalizeLanguage } from '../markdown/highlight.ts'
import { CopyButton } from './CopyButton.tsx'
import css from './CodeBlock.module.css'

export interface CodeBlockProps {
  className?: string
  code: string
  language?: string
}

/**
 * A fenced code block: sticky banner (language + copy) over the source.
 *
 * Highlighting is progressive by design — the plain text paints immediately
 * and is replaced when the grammar chunk lands. That ordering matters while a
 * reply is streaming: the block is re-rendered on every delta, and waiting for
 * a highlight would leave the reader watching an empty card.
 */
export function CodeBlock({ className, code, language }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null)
  const resolved = normalizeLanguage(language)

  useEffect(() => {
    if (resolved === undefined) {
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
  }, [code, resolved])

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
