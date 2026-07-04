import { findSlashCommand } from './registry.js'

/** Ghost-hint gate: exactly `/name ` — one trailing space, no args typed yet.
 *  Mirrors the original CC's hasExactlyOneTrailingSpace (useTypeahead.tsx),
 *  so the hint appears right after completing a command and disappears the
 *  moment a real argument (or a second space) is typed. */
const GHOST_RE = /^\/([^\s/][^\s]*) $/

/**
 * Argument hint for a slash command name (no leading `/`).
 *
 * The TUI-local registry wins over the catalog — dispatch consults it first
 * (createSlashHandler), so a local command that shadows a gateway one (e.g.
 * /compact, /model) must also shadow its hint. The catalog covers the rest:
 * gateway-dispatched SLASHES commands and backend workflow commands.
 */
export function argumentHintFor(
  name: string,
  catalogHints?: null | Record<string, string>
): string | undefined {
  const local = findSlashCommand(name)

  if (local?.argumentHint) {
    return local.argumentHint
  }

  return catalogHints?.[`/${name.toLowerCase()}`]
}

/** Hint to ghost-render after the input, or undefined when the input isn't an
 *  exactly-completed `/command ` (or the command has no hint). */
export function ghostArgumentHint(
  input: string,
  catalogHints?: null | Record<string, string>
): string | undefined {
  const m = GHOST_RE.exec(input)

  return m ? argumentHintFor(m[1]!, catalogHints) : undefined
}
