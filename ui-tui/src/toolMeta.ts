/**
 * Tool display metadata shared by the live tool-progress block, the spinner,
 * and the committed collapsed summary — so "Reading 3 files" reads the same
 * everywhere.
 */
export const TOOL_VERB: Record<string, { verb: string; noun: string }> = {
  Read: { verb: 'Reading', noun: 'files' },
  Edit: { verb: 'Editing', noun: 'files' },
  Write: { verb: 'Writing', noun: 'files' },
  MultiEdit: { verb: 'Editing', noun: 'files' },
  Bash: { verb: 'Running', noun: 'commands' },
  Grep: { verb: 'Searching', noun: 'patterns' },
  Glob: { verb: 'Globbing', noun: 'patterns' },
  LS: { verb: 'Listing', noun: 'dirs' },
  WebFetch: { verb: 'Fetching', noun: 'urls' },
  WebSearch: { verb: 'Searching', noun: 'queries' },
}

/** Read-like tools whose repeated calls collapse into one live "Reading N files" block. */
export const READ_LIKE = new Set(['Read', 'Glob', 'Grep', 'LS'])

/** "Reading 3 files" (collapsed count) or "Reading README.md" (single). */
export function toolActivityLabel(name: string | undefined, args: string | undefined, count: number): string {
  const { verb, noun } = (name && TOOL_VERB[name]) || {
    verb: name ? `Using ${name}` : 'Working',
    noun: '',
  }
  if (count > 1 && noun) return `${verb} ${count} ${noun}`
  const target = (args || '').split(/[\\/]/).pop() || args || ''
  return target ? `${verb} ${target}` : verb
}
