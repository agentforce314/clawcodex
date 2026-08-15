import { Fragment, type ReactNode } from 'react'

/**
 * Bare http(s) URLs in plain text → anchors, for tool output that carries
 * references (web search results, fetch citations). Everything else passes
 * through as text. Only absolute http(s) URLs qualify — the same policy the
 * markdown renderer applies to authored links.
 */
const URL_PATTERN = /https?:\/\/[^\s<>"')\]]+/g

/** Trailing punctuation reads as prose, not as part of the address. */
const TRAILING_PUNCTUATION = /[.,;:!?]+$/

export function linkifyText(text: string, keyBase = 'link'): ReactNode {
  if (!text.includes('http')) return text

  const nodes: ReactNode[] = []
  let last = 0
  let count = 0

  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index

    let url = match[0]
    const trimmed = TRAILING_PUNCTUATION.exec(url)

    if (trimmed !== null) url = url.slice(0, -trimmed[0].length)
    if (url.length < 'http://x'.length) continue

    if (start > last) nodes.push(text.slice(last, start))

    nodes.push(
      <a href={url} key={`${keyBase}-${count}`} rel="noopener noreferrer" target="_blank">
        {url}
      </a>,
    )

    count += 1
    last = start + url.length
  }

  if (nodes.length === 0) return text
  if (last < text.length) nodes.push(text.slice(last))

  return nodes.map((node, index) =>
    typeof node === 'string' ? <Fragment key={`${keyBase}-t${index}`}>{node}</Fragment> : node,
  )
}
