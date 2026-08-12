import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('node:child_process', () => ({ spawn: () => new EventEmitter() }))

vi.hoisted(() => {
  process.env.FORCE_COLOR = '3'
  process.env.COLORTERM = 'truecolor'
  delete process.env.NO_COLOR
})

import { ToolTrail } from '../components/thinking.js'
import {
  briefCallOfTrailLine,
  briefClauses,
  type BriefCounts,
  briefRuns,
  briefText,
  classifyBriefTool,
  emptyBriefCounts
} from '../domain/toolBrief.js'
import { buildToolTrailLine, stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

// ── classification ──────────────────────────────────────────────────────────

describe('classifyBriefTool', () => {
  it('buckets the file/search tools', () => {
    expect(classifyBriefTool('Read(src/a.py)')).toBe('read')
    expect(classifyBriefTool('Grep(TODO)')).toBe('search')
    expect(classifyBriefTool('Glob(src/*.py)')).toBe('search')
  })

  it('buckets a shell call on its command, the way upstream does', () => {
    expect(classifyBriefTool('Bash(ls src)')).toBe('list')
    expect(classifyBriefTool('Bash(grep -rn def src)')).toBe('search')
    expect(classifyBriefTool('Bash(cat docs/readme.md)')).toBe('read')
    expect(classifyBriefTool('Bash(echo hello)')).toBe('bash')
    // A command that merely *starts* with those letters is still a command.
    expect(classifyBriefTool('Bash(lsof -i)')).toBe('bash')
    expect(classifyBriefTool('Bash(catalog --build)')).toBe('bash')
  })

  it('keeps edits, delegations and questions out of the tally', () => {
    expect(classifyBriefTool('Edit(a.py)')).toBe('edit')
    expect(classifyBriefTool('Write(b.py)')).toBe('edit')
    expect(classifyBriefTool('Delegate Task(audit)')).toBe('agent')
    expect(classifyBriefTool('AskUserQuestion(pick one)')).toBe('ask')
  })

  it('falls back to the catch-all bucket', () => {
    expect(classifyBriefTool('WebSearch(rust release)')).toBe('other')
    expect(classifyBriefTool('Mcp Github List Prs(open)')).toBe('other')
  })

  it('ignores a legacy duration suffix on resumed trail lines', () => {
    expect(classifyBriefTool('Read(a.py) (1.2s)')).toBe('read')
  })
})

// ── vocabulary ──────────────────────────────────────────────────────────────

describe('briefText', () => {
  const counts = (over: Partial<BriefCounts>): BriefCounts => ({ ...emptyBriefCounts(), ...over })

  it('capitalizes only the first clause and joins the rest with commas', () => {
    expect(briefText(counts({ bash: 1, list: 1, read: 3 }))).toBe(
      'Read 3 files, listed 1 directory, ran 1 shell command'
    )
  })

  it('orders clauses search → read → list → other → shell', () => {
    expect(briefText(counts({ bash: 1, list: 1, other: 1, read: 1, search: 1 }))).toBe(
      'Searched for 1 pattern, read 1 file, listed 1 directory, called 1 tool, ran 1 shell command'
    )
  })

  it('uses the gerund and an ellipsis while the run is live', () => {
    expect(briefText(counts({ read: 1 }), true)).toBe('Reading 1 file…')
    expect(briefText(counts({ search: 2 }), true)).toBe('Searching for 2 patterns…')
  })

  it('pluralizes each noun independently', () => {
    expect(briefText(counts({ list: 2 }))).toBe('Listed 2 directories')
    expect(briefText(counts({ read: 1 }))).toBe('Read 1 file')
  })

  it('is empty when nothing collapsible ran', () => {
    expect(briefText(counts({ edit: 3 }))).toBe('')
    expect(briefClauses(counts({ agent: 1 }))).toEqual([])
  })
})

// ── runs ────────────────────────────────────────────────────────────────────

describe('briefRuns', () => {
  const id = (s: string) => s

  it('folds a consecutive stretch into one brief run', () => {
    const runs = briefRuns(['Read(a)', 'Read(b)', 'Bash(echo hi)'], id)

    expect(runs).toHaveLength(1)
    expect(runs[0]!.kind).toBe('brief')
    expect(runs[0]!.items).toHaveLength(3)
  })

  it('breaks the run at a standalone call and keeps source order', () => {
    const runs = briefRuns(['Read(a)', 'Edit(b)', 'Bash(echo hi)'], id)

    expect(runs.map(r => r.kind)).toEqual(['brief', 'flat', 'brief'])
    expect(runs[1]!.items).toEqual(['Edit(b)'])
  })

  it('never merges two standalone calls into one block', () => {
    const runs = briefRuns(['Edit(a)', 'Edit(b)'], id)

    expect(runs.map(r => r.kind)).toEqual(['flat', 'flat'])
  })
})

describe('briefCallOfTrailLine', () => {
  it('recovers the call from a completed trail line', () => {
    expect(briefCallOfTrailLine(buildToolTrailLine('Read', 'src/a.py', false, 'Read 8 lines'))).toBe('Read(src/a.py)')
  })

  it('recovers the label from a drafting line', () => {
    expect(briefCallOfTrailLine('drafting Write…')).toBe('Write')
  })
})

// ── render ──────────────────────────────────────────────────────────────────

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 40 })
  Object.assign(stdin, { isTTY: false })
  stdout.on('data', (chunk: Buffer) => {
    output += chunk.toString()
  })

  const instance = renderSync(element, { stderr: stderr as never, stdin: stdin as never, stdout: stdout as never })

  instance.unmount()

  return output
}

describe('ToolTrail brief render', () => {
  const trail = [
    buildToolTrailLine('Read', 'src/alpha.py', false, 'Read 8 lines'),
    buildToolTrailLine('Read', 'src/beta.py', false, 'Read 8 lines'),
    buildToolTrailLine('Bash', 'ls src', false, 'alpha.py\nbeta.py'),
    buildToolTrailLine('Bash', 'echo hello', false, 'hello')
  ]

  it('collapses the whole run to one summary line', () => {
    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail }))
    )

    expect(out).toContain('Read 2 files, listed 1 directory, ran 1 shell command')
    expect(out).not.toContain('Read(src/alpha.py)')
    expect(out).not.toContain('hello')
  })

  it('puts every call back under ctrl+o', () => {
    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail }))
    )

    expect(out).toContain('Read(src/alpha.py)')
    expect(out).toContain('Read(src/beta.py)')
    expect(out).toContain('Bash(echo hello)')
    expect(out).not.toContain('Read 2 files,')
  })

  it('keeps a standalone edit visible while collapsed', () => {
    const withEdit = [trail[0]!, buildToolTrailLine('Edit', 'src/gamma.py', false, 'Updated src/gamma.py')]

    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail: withEdit }))
    )

    expect(out).toContain('Read 1 file')
    expect(out).toContain('Edit(src/gamma.py)')
  })

  it('separates expanded blocks with a blank line', () => {
    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail }))
    )

    const rows = out.split('\n')
    const first = rows.findIndex(r => r.includes('Read(src/alpha.py)'))
    const second = rows.findIndex(r => r.includes('Read(src/beta.py)'))

    // ⏺ call / ⎿ result / blank / ⏺ next call
    expect(second - first).toBe(3)
    expect(rows[second - 1]!.trim()).toBe('')
  })

  // The ⎿ result gutter is three columns wide ("  ⎿  x"). It lives in a
  // string literal because a formatter collapses bare JSX whitespace, and the
  // looser /⎿\s+/ assertions elsewhere cannot see the difference.
  it('keeps the result gutter three columns wide', () => {
    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'expanded', t: DEFAULT_THEME, trail }))
    )

    expect(out.split('\n').find(row => row.includes('⎿'))).toMatch(/^ {2}⎿ {2}\S/)
  })

  it('renders the brief under a two-column gutter, like the ⏺ rows', () => {
    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail }))
    )

    const row = out.split('\n').find(line => line.includes('Read 2 files'))

    expect(row).toMatch(/^ {2}Read 2 files/)
  })
})
