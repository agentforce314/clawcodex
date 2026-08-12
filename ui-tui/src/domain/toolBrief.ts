import { parseToolTrailResultLine, splitToolDuration, toolTrailLabel } from '../lib/text.js'

/**
 * The original Claude Code transcript does not list every tool call on its own
 * row. A run of calls collapses into a single muted summary line — the
 * "brief":
 *
 *   Read 3 files, listed 1 directory, ran 1 shell command
 *
 * and ctrl+o (details `expanded`) swaps it back for the full `⏺ Tool(args)`
 * list. This module owns the brief's vocabulary: which bucket a call falls
 * into, whether that bucket collapses at all, and which clauses it produces.
 *
 * Three rules carry most of the look:
 *   - only the FIRST clause is capitalized; the rest stay lowercase, joined
 *     by ", " ("Read 3 files, listed 1 directory").
 *   - a still-running group uses the gerund and trails an ellipsis
 *     ("Reading 1 file…"); a settled one uses the past tense.
 *   - upstream only folds tools that describe themselves as a search/read
 *     shaped call (plus shell commands and MCP bridges). Everything else —
 *     edits, delegations, answers — keeps its own row, because its detail
 *     rows carry information a tally would destroy. See STANDALONE below.
 */
export type BriefBucket = 'agent' | 'answer' | 'bash' | 'edit' | 'list' | 'other' | 'read' | 'search'

export type BriefCounts = Record<BriefBucket, number>

/**
 * Only the read/search band and shell commands fold. Everything else renders
 * as its own `⏺ Tool(args)` block even in the collapsed view — which is what
 * upstream does, and the reason its collapsed message type is literally named
 * `collapsed_read_search`. Captured from Claude Code 2.1.228: a lone `Bash`
 * folds to "Ran 1 shell command", while a lone `WebSearch` keeps
 * `⏺ Web Search("…")` + `⎿ Did 1 search in 2s`, and a lone `Agent` keeps
 * `⏺ Agent(…)` + `⎿ Done (2 tool uses · 48.0k tokens · 11s)`.
 *
 *   edit   — the patch itself is the point (upstream renders `⏺ Write(f)`
 *            with the content under it, and never tallies it away).
 *   agent  — the row anchors the inline subagent tree.
 *   answer — AskUserQuestion, clarify, advisor, vision_analyze, ExitPlanMode:
 *            the detail rows ARE the result (the user's own choices, the
 *            advisor's opinion, the plan).
 *   other  — WebSearch, WebFetch, Skill, MCP bridges: their `⎿` rows carry a
 *            result no tally can stand in for.
 *
 * The point of the brief is the high-volume, low-information calls — reading,
 * searching, listing, shelling out. Those are what bury a transcript.
 */
const STANDALONE: ReadonlySet<BriefBucket> = new Set<BriefBucket>(['agent', 'answer', 'edit', 'other'])

export const isCollapsibleBucket = (bucket: BriefBucket): boolean => !STANDALONE.has(bucket)

export const emptyBriefCounts = (): BriefCounts => ({
  agent: 0,
  answer: 0,
  bash: 0,
  edit: 0,
  list: 0,
  other: 0,
  read: 0,
  search: 0
})

// Labels, not wire names: the trail carries what toolTrailLabel() produced, so
// `vision_analyze` arrives as `Vision Analyze`.
const READ_TOOLS = new Set(['Read'])
const SEARCH_TOOLS = new Set(['Glob', 'Grep'])
const EDIT_TOOLS = new Set(['Edit', 'NotebookEdit', 'Write'])
const AGENT_TOOLS = new Set(['Agent', 'Delegate Task', 'Task'])
const ANSWER_TOOLS = new Set(['Advisor', 'AskUserQuestion', 'Clarify', 'ExitPlanMode', 'Vision Analyze'])

/** `Read(src/a.py)` → `Read`. Legacy trail lines may still carry a `(1.2s)`
 *  duration suffix, so strip that before splitting on the arg paren. */
export const briefToolName = (call: string): string => {
  const { label } = splitToolDuration(call)
  const paren = label.indexOf('(')

  return (paren > 0 ? label.slice(0, paren) : label).trim()
}

/** `Bash(ls src)` → `ls src`. Empty when the call carries no args. */
export const briefToolArgs = (call: string): string => {
  const { label } = splitToolDuration(call)
  const paren = label.indexOf('(')

  return paren > 0 ? label.slice(paren + 1).replace(/\)$/, '') : ''
}

// Upstream buckets a shell call on the *command*, not the tool: `ls` reads as
// "listed 1 directory" and `grep`/`rg` as "searched for 1 pattern", so a
// transcript full of shell-driven exploration still reads as exploration.
// Anything else is a shell command.
//
// Deliberately a prefix match on the bare command, so anything wrapped —
// `sudo ls`, `FOO=1 ls`, `cd x && ls`, a pipeline that greps midway — falls
// through to "shell command". Under-claiming reads fine; a `cat > file.py`
// heredoc counted as a read would not. The args this matches against are the
// compactPreview head, but every pattern is anchored, so truncation can never
// change a verdict.
const LIST_COMMAND = /^\s*(?:\$\s*)?(?:ls|tree)(?:\s|$)/
const SEARCH_COMMAND = /^\s*(?:\$\s*)?(?:ag|ack|grep|egrep|fgrep|rg)(?:\s|$)/
const READ_COMMAND = /^\s*(?:\$\s*)?(?:bat|cat|head|less|tail)(?:\s|$)/

export const classifyBriefTool = (call: string): BriefBucket => {
  const name = briefToolName(call)

  if (READ_TOOLS.has(name)) {
    return 'read'
  }

  if (SEARCH_TOOLS.has(name)) {
    return 'search'
  }

  if (EDIT_TOOLS.has(name)) {
    return 'edit'
  }

  if (AGENT_TOOLS.has(name)) {
    return 'agent'
  }

  if (ANSWER_TOOLS.has(name)) {
    return 'answer'
  }

  if (name === 'Bash') {
    const args = briefToolArgs(call)

    if (LIST_COMMAND.test(args)) {
      return 'list'
    }

    if (SEARCH_COMMAND.test(args)) {
      return 'search'
    }

    return READ_COMMAND.test(args) ? 'read' : 'bash'
  }

  // Everything else — WebSearch/WebFetch, Skill, MCP bridges, task tools.
  // STANDALONE, so each keeps its own row and its own result.
  return 'other'
}

/**
 * Classification key for a RAW trail line: the `Tool(args)` call when the line
 * is a completed tool result, the drafting label while one is still being
 * written, and the line itself otherwise. Mirrors how ToolTrail derives
 * `group.label`, so the renderer and the height estimator agree on where runs
 * begin and end.
 */
export const briefCallOfTrailLine = (line: string): string => {
  const parsed = parseToolTrailResultLine(line)

  if (parsed) {
    return parsed.call
  }

  if (line.startsWith('drafting ')) {
    return toolTrailLabel(line.slice(9).replace(/…$/, '').trim())
  }

  return line
}

export const countBriefTools = (calls: readonly string[]): BriefCounts => {
  const counts = emptyBriefCounts()

  for (const call of calls) {
    counts[classifyBriefTool(call)]++
  }

  return counts
}

export interface BriefClause {
  /** The tally — rendered bold, between `verb` and `noun`. */
  count: number
  key: BriefBucket
  noun: string
  /** Capitalized on the first clause only. */
  verb: string
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

// Clause order is upstream's: the read/search band first, shell commands last.
// STANDALONE buckets never reach here.
const ORDER: { bucket: BriefBucket; live: string; noun: [string, string]; past: string }[] = [
  { bucket: 'search', live: 'searching for', noun: ['pattern', 'patterns'], past: 'searched for' },
  { bucket: 'read', live: 'reading', noun: ['file', 'files'], past: 'read' },
  { bucket: 'list', live: 'listing', noun: ['directory', 'directories'], past: 'listed' },
  { bucket: 'bash', live: 'running', noun: ['shell command', 'shell commands'], past: 'ran' }
]

export const briefClauses = (counts: BriefCounts, live = false): BriefClause[] => {
  const out: BriefClause[] = []

  for (const spec of ORDER) {
    const count = counts[spec.bucket]

    if (count <= 0) {
      continue
    }

    const verb = live ? spec.live : spec.past

    out.push({
      count,
      key: spec.bucket,
      noun: plural(count, spec.noun[0], spec.noun[1]),
      // Only the opening clause is capitalized — the rest read as a list.
      verb: out.length === 0 ? verb[0]!.toUpperCase() + verb.slice(1) : verb
    })
  }

  return out
}

/** Plain-text form of the brief. The renderer bolds the counts; this is the
 *  same string without styling, for height estimation and tests. */
export const briefText = (counts: BriefCounts, live = false): string => {
  const clauses = briefClauses(counts, live)

  if (!clauses.length) {
    return ''
  }

  return `${clauses.map(c => `${c.verb} ${c.count} ${c.noun}`).join(', ')}${live ? '…' : ''}`
}

/**
 * Split an ordered tool list into render runs: each consecutive stretch of
 * collapsible calls becomes one `brief` run, and each standalone call keeps
 * its own `flat` run. Preserves order, so a Read → Delegate Task → Bash trail
 * reads brief / delegate row / brief rather than reordering the turn.
 *
 * `standalone` forces an item out of the brief regardless of its bucket. The
 * renderer passes failures through it: upstream tallies a failed call away
 * like any other (a `cat` of a missing file still reads "Read 1 file"), but
 * the error message lives only in that call's `⎿` row, so folding it would
 * make the one thing worth reading unreachable without ctrl+o.
 */
export interface BriefRun<T> {
  items: T[]
  kind: 'brief' | 'flat'
}

export const briefRuns = <T>(
  items: readonly T[],
  callOf: (item: T) => string,
  standalone?: (item: T) => boolean
): BriefRun<T>[] => {
  const runs: BriefRun<T>[] = []

  for (const item of items) {
    const collapsible = !standalone?.(item) && isCollapsibleBucket(classifyBriefTool(callOf(item)))
    const kind = collapsible ? 'brief' : 'flat'
    const last = runs.at(-1)

    // Standalone rows never merge with each other — each keeps its own block.
    if (last && last.kind === 'brief' && kind === 'brief') {
      last.items.push(item)
    } else {
      runs.push({ items: [item], kind })
    }
  }

  return runs
}
