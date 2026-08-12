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
 * What the composer's ghost slot shows while idle. The recap suggestion wins
 * whenever one is armed — that is the whole feature: it appears MID-
 * conversation, right after a turn ends. The static `Try "…"` hint stays
 * fresh-conversation-only (its historical behavior). Busy always blanks the
 * slot, and TextInput itself hides any placeholder once the input has text,
 * so input-emptiness is NOT this function's concern.
 *
 * Regression note: the first ship gated BOTH on `composer.empty`, which is
 * conversation-emptiness (useMainApp: `!historyItems.some(m => m.kind !==
 * 'intro')`), not input-emptiness — so the suggestion ghost could never
 * render in the only state where a suggestion exists. Tab-accept still
 * worked (it checks the real input), which is exactly why the miss was
 * invisible to the state-level tests.
 */
export const composerPlaceholder = (state: {
  busy: boolean
  conversationEmpty: boolean
  pendingSuggestion: null | string
}): string => {
  if (state.busy) {
    return ''
  }

  return state.pendingSuggestion ?? (state.conversationEmpty ? PLACEHOLDER : '')
}

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
