/**
 * The `@` file-mention token in a draft.
 *
 * A mention is a token at the caret, not a prefix of the whole draft the way a
 * slash command is — "look at @src/cli.py and tell me…" mentions a file in the
 * middle of a sentence, and the same message can mention several.
 */
export interface MentionToken {
  /** Index of the `@` itself. */
  start: number
  /** Index just past the token (the caret). */
  end: number
  /** Everything between the `@` and the caret. */
  query: string
}

/**
 * The mention being typed at `caret`, or `null` when there isn't one.
 *
 * The `@` has to open a word — at the start of the draft or after whitespace —
 * so `me@example.com` and `arr@[0]` are text, not triggers. The token runs to
 * the caret and cannot contain whitespace, which is what ends a mention: once
 * the user types a space the path is settled and the menu should close.
 */
export function mentionAt(text: string, caret: number): MentionToken | null {
  const at = Math.max(0, Math.min(caret, text.length))

  for (let index = at - 1; index >= 0; index -= 1) {
    const char = text[index] as string

    if (/\s/.test(char)) return null

    if (char === '@') {
      const before = index === 0 ? '' : (text[index - 1] as string)

      // Opening a word: start of draft, or preceded by whitespace. Anything
      // else (a letter, a digit, a bracket) means this @ is part of something.
      if (before !== '' && !/\s/.test(before)) return null

      return { end: at, query: text.slice(index + 1, at), start: index }
    }
  }

  return null
}

/**
 * Replace the token with `@path `, and report where the caret lands.
 *
 * The trailing space is the reason the caret has to be reported: it closes the
 * mention (so the menu shuts) and leaves the user typing the next word rather
 * than back inside a path they already chose. The format matches
 * `workspace_search.file_insertion`, which is what every other ClawCodex
 * surface inserts.
 */
export function applyMention(
  text: string,
  token: MentionToken,
  path: string,
): { caret: number; text: string } {
  const insertion = `@${path} `
  const next = text.slice(0, token.start) + insertion + text.slice(token.end)

  return { caret: token.start + insertion.length, text: next }
}
