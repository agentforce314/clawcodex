import { useEffect, useState } from 'react'

export interface MathProps {
  display?: boolean
  tex: string
}

let katexPromise: Promise<typeof import('katex').default> | null = null

async function katex(): Promise<typeof import('katex').default> {
  katexPromise ??= (async () => {
    // The stylesheet carries the font faces, so it must land with the module.
    const [lib] = await Promise.all([import('katex'), import('katex/dist/katex.min.css')])

    return lib.default
  })()

  return katexPromise
}

/**
 * A TeX span or block, typeset on demand.
 *
 * KaTeX and its font faces are ~300kB, so nothing loads until a message
 * actually contains math; until then (and if the expression does not parse)
 * the raw TeX shows, which is still readable.
 */
export function Math({ display = false, tex }: MathProps) {
  const [html, setHtml] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    void katex()
      .then(lib =>
        lib.renderToString(tex, { displayMode: display, output: 'html', throwOnError: false }),
      )
      .then(result => {
        if (!cancelled) setHtml(result)
      })
      .catch(() => {
        /* leave the raw TeX in place */
      })

    return () => {
      cancelled = true
    }
  }, [display, tex])

  if (html === null) {
    return display ? <pre>{tex}</pre> : <code>{tex}</code>
  }

  // KaTeX's own output; the TeX source never reaches the DOM unescaped.
  return display ? (
    <div className="katex-display-wrap" dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  )
}
