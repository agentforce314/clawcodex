import { pick } from '../lib/text.js'

export const PLACEHOLDERS = [
  'Ask me anything…',
  'Try "explain this codebase"',
  'Try "write a test for…"',
  'Try "refactor the auth module"',
  'Try "/help" for commands',
  'Try "fix the lint errors"',
  'Try "how does the config loader work?"'
]

export const PLACEHOLDER = pick(PLACEHOLDERS)

/**
 * The tab-acceptable query inside a composer placeholder: `Try "explain this
 * codebase"` suggests the query `explain this codebase`; a placeholder with no
 * quoted span ('Ask me anything…') suggests nothing. An open-ended stub
 * (`Try "write a test for…"`) drops the ellipsis and keeps one trailing space
 * so the accepted text reads as a sentence the user finishes typing.
 *
 * Original CC accepts its prompt suggestion the same way — plain Tab on an
 * empty input inserts the suggestion text (useTypeahead.tsx handleKeyDown);
 * there the suggestion state already holds the bare query, while ours is
 * embedded in the `Try "…"` placeholder string, hence this extraction.
 */
export function suggestedQuery(placeholder: string): null | string {
  const quoted = /"([^"]+)"/.exec(placeholder)?.[1]

  if (!quoted) {
    return null
  }

  const openEnded = /(?:…|\.{3})$/.test(quoted)
  const query = quoted.replace(/(?:…|\.{3})$/, '').trimEnd()

  if (!query) {
    return null
  }

  return openEnded ? `${query} ` : query
}
