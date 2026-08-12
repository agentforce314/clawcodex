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

import { MessageLine } from '../components/messageLine.js'
import { ToolTrail } from '../components/thinking.js'
import {
  briefCallOfTrailLine,
  briefClauses,
  type BriefCounts,
  briefRuns,
  briefText,
  classifyBriefTool,
  emptyBriefCounts,
  isCollapsibleBucket
} from '../domain/toolBrief.js'
import { transcriptTrailWidth } from '../lib/inputMetrics.js'
import { buildToolTrailLine, stripAnsi } from '../lib/text.js'
import { estimatedMsgHeight } from '../lib/virtualHeights.js'
import { DEFAULT_THEME } from '../theme.js'
import type { Msg } from '../types.js'

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

  it('keeps edits, delegations and answer-bearing tools out of the tally', () => {
    expect(classifyBriefTool('Edit(a.py)')).toBe('edit')
    expect(classifyBriefTool('Write(b.py)')).toBe('edit')
    expect(classifyBriefTool('Delegate Task(audit)')).toBe('agent')
    expect(classifyBriefTool('AskUserQuestion(pick one)')).toBe('answer')
    // Labels, not wire names: toolTrailLabel() title-cases and de-snakes.
    expect(classifyBriefTool('Advisor(is this sound?)')).toBe('answer')
    expect(classifyBriefTool('Vision Analyze(shot.png)')).toBe('answer')
    expect(classifyBriefTool('ExitPlanMode(plan)')).toBe('answer')
  })

  it('falls back to the catch-all bucket, which also stands alone', () => {
    expect(classifyBriefTool('WebSearch(rust release)')).toBe('other')
    expect(classifyBriefTool('Mcp Github List Prs(open)')).toBe('other')
    expect(isCollapsibleBucket('other')).toBe(false)
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

  it('orders clauses search → read → list → shell', () => {
    expect(briefText(counts({ bash: 1, list: 1, read: 1, search: 1 }))).toBe(
      'Searched for 1 pattern, read 1 file, listed 1 directory, ran 1 shell command'
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
    // WebSearch and friends keep their own row, so they never tally either.
    expect(briefText(counts({ other: 2 }))).toBe('')
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

  it('breaks a failing call out so its message survives the fold', () => {
    const runs = briefRuns(['Read(a)', 'Bash(boom)', 'Read(c)'], id, item => item === 'Bash(boom)')

    expect(runs.map(r => r.kind)).toEqual(['brief', 'flat', 'brief'])
    expect(runs[1]!.items).toEqual(['Bash(boom)'])
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

// Ink brackets every frame in a synchronized-update pair (BSU … ESU). Into a
// PassThrough — which is not a TTY, so nothing gets overwritten — it flushes
// the same frame more than once and they concatenate, which turns any row
// count into 2n-1. Keep the last frame that painted something.
const BSU = '[?2026h'
const ESU = '[?2026l'

const lastFrame = (output: string): string => {
  const frames = output
    .split(BSU)
    .map(chunk => chunk.split(ESU)[0] ?? '')
    .filter(frame => stripAnsi(frame).trim() !== '')

  return frames.at(-1) ?? ''
}

const renderToString = (element: React.ReactElement, columns = 100): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns, isTTY: false, rows: 40 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', (chunk: Buffer) => {
    output += chunk.toString()
  })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as never,
    stdin: stdin as never,
    stdout: stdout as never
  })

  instance.unmount()
  instance.cleanup()

  return lastFrame(output)
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

  // WebSearch and friends are NOT part of the read/search band upstream folds:
  // captured from Claude Code 2.1.228, a lone Web Search keeps its row and its
  // `⎿ Did 1 search in 2s`. A tally would delete the only interesting part.
  it('keeps a catch-all tool standalone with its result', () => {
    const withSearch = [trail[0]!, buildToolTrailLine('WebSearch', 'rust 1.90', false, 'Did 1 search in 2s')]

    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail: withSearch }))
    )

    expect(out).toContain('WebSearch(rust 1.90)')
    expect(out).toContain('Did 1 search in 2s')
    expect(out).toContain('Read 1 file')
    expect(out).not.toContain('called 1 tool')
  })

  it('breaks a failed call out of the brief so its error stays readable', () => {
    const withError = [
      trail[0]!,
      buildToolTrailLine('Bash', 'cat missing.txt', true, 'Error: No such file or directory'),
      trail[3]!
    ]

    const out = stripAnsi(
      renderToString(React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail: withError }))
    )

    expect(out).toContain('Bash(cat missing.txt)')
    expect(out).toContain('Error: No such file or directory')
    // The successes around it still fold.
    expect(out).toContain('Read 1 file')
    expect(out).toContain('Ran 1 shell command')
  })

  // Bold and faint share SGR close code 22, so a `dim` wrapper rewrites each
  // bold tally's reset into `\e[2m` — faint re-opened, bold never cleared —
  // and every column after the first tally paints bold. stripAnsi cannot see
  // this, so assert on the bytes.
  it('closes each bold tally without leaving the rest of the line bold', () => {
    const out = renderToString(React.createElement(ToolTrail, { detailsMode: 'collapsed', t: DEFAULT_THEME, trail }))
    const BOLD = '\u001b[1m'
    const RESET_INTENSITY = '\u001b[22m'
    const FAINT = '\u001b[2m'

    expect(out).toContain(`${BOLD}2${RESET_INTENSITY}`)
    // No faint is opened anywhere, so no tally's reset can be rewritten into
    // one — bold cannot survive past the tally that opened it.
    expect(out).not.toContain(FAINT)
  })
})

// ── estimate vs paint ───────────────────────────────────────────────────────

// The virtualized transcript positions rows from estimatedMsgHeight before
// Yoga has measured anything, so an estimate that disagrees with the paint
// shows up as scrollbar drift and blank gaps. Assert the two agree on real
// trails rather than trusting the two implementations to stay in step.
const READ_LINE = buildToolTrailLine('Read', 'a.py', false, 'Read 8 lines')

describe('estimatedMsgHeight matches the painted trail', () => {
  // A `kind: 'trail'` block gets the transcript interior with no role gutter,
  // which is narrower than the terminal. Render at that same width — pulled
  // from the helper the estimator uses — or the comparison is against a paint
  // the app never produces, and every wrapping case reads backwards.
  const paintedRows = (msg: Msg, detailsMode: 'collapsed' | 'expanded', cols: number) => {
    const rows = stripAnsi(
      renderToString(
        React.createElement(ToolTrail, {
          detailsMode,
          reasoning: msg.thinking ?? '',
          t: DEFAULT_THEME,
          trail: msg.tools ?? [],
          verboseTrail: msg.toolsVerbose ?? []
        }),
        transcriptTrailWidth(cols)
      )
    ).split('\n')

    while (rows.length && rows[rows.length - 1]!.trim() === '') {
      rows.pop()
    }

    return rows.length
  }

  const trailMsg = (tools: string[], toolsVerbose?: string[]): Msg => ({
    kind: 'trail',
    role: 'system',
    text: '',
    tools,
    ...(toolsVerbose ? { toolsVerbose } : {})
  })

  const cases: [string, Msg][] = [
    ['a plain run', trailMsg([buildToolTrailLine('Read', 'a.py', false, 'Read 8 lines')])],
    [
      'a mixed run',
      trailMsg([
        buildToolTrailLine('Read', 'a.py', false, 'Read 8 lines'),
        buildToolTrailLine('Bash', 'ls src', false, 'a.py\nb.py'),
        buildToolTrailLine('Bash', 'echo hi', false, 'hi')
      ])
    ],
    [
      'a run split by a standalone edit',
      trailMsg([
        buildToolTrailLine('Read', 'a.py', false, 'Read 8 lines'),
        buildToolTrailLine('Edit', 'b.py', false, 'Updated b.py'),
        buildToolTrailLine('Bash', 'echo hi', false, 'hi')
      ])
    ],
    [
      'a run split by a failure',
      trailMsg([
        buildToolTrailLine('Read', 'a.py', false, 'Read 8 lines'),
        buildToolTrailLine('Bash', 'cat nope', true, 'Error: No such file'),
        buildToolTrailLine('Bash', 'echo hi', false, 'hi')
      ])
    ],
    [
      'a call with no result row',
      trailMsg([
        buildToolTrailLine('Bash', 'true', false, ''),
        buildToolTrailLine('Read', 'a.py', false, 'Read 1 line')
      ])
    ],
    [
      // Edit details carry a full path and are capped in LINES, never columns,
      // so this is the ordinary case on a normal-width terminal, not a corner.
      'a detail long enough to wrap',
      trailMsg([
        buildToolTrailLine(
          'Edit',
          'src/components/transcript/toolTrail.tsx',
          false,
          'Updated src/components/transcript/toolTrail.tsx with 3 additions and 1 removal'
        )
      ])
    ],
    [
      // Four clauses with multi-digit tallies is the widest a brief can get
      // now that `other` stands alone: ~84 columns, so it wraps at cols=60 and
      // fits at cols=100. The cols=60 arm is the one exercising brief wrap.
      'a brief long enough to wrap at 60 columns',
      trailMsg([
        ...Array.from({ length: 12 }, (_, i) => buildToolTrailLine('Grep', `p${i}`, false, 'Found 2 lines')),
        ...Array.from({ length: 13 }, (_, i) => buildToolTrailLine('Read', `f${i}.py`, false, 'Read 8 lines')),
        ...Array.from({ length: 24 }, (_, i) => buildToolTrailLine('Bash', `ls d${i}`, false, 'a.py')),
        ...Array.from({ length: 18 }, (_, i) => buildToolTrailLine('Bash', `echo ${i}`, false, 'hi'))
      ])
    ],
    ['a reasoning trail', { kind: 'trail', role: 'system', text: '', thinking: 'I should check the parser first.' }],
    [
      'reasoning plus tools',
      {
        kind: 'trail',
        role: 'system',
        text: '',
        thinking: 'First line.\nSecond line.\nThird line.',
        tools: [buildToolTrailLine('Read', 'a.py', false, 'Read 8 lines')]
      }
    ],
    [
      'reasoning long enough to wrap',
      {
        kind: 'trail',
        role: 'system',
        text: '',
        thinking:
          'The parser reads the header first, then walks each block until it hits a boundary, ' +
          'and only then does it decide whether the trailing bytes belong to the previous frame.'
      }
    ],
    [
      // The reasoning body sits under a `  └─ ` rail — content column 5, not
      // 3. One unbreakable token so Ink's word wrap and the estimator's
      // character wrap coincide and the gutter is the only variable, sized to
      // land between the two candidate widths at cols=100: it wraps at 91 and
      // does not at 93. A body that merely "is long" cannot tell them apart.
      'reasoning that wraps only at the rail column',
      { kind: 'trail', role: 'system', text: '', thinking: 'x'.repeat(92) }
    ]
  ]

  // Prose rows carry the trail through MessageLine, which adds chrome the
  // estimator has to model too: the details wrapper's margin, the `└─
  // Response` separator, and the reasoning panel's own header row.
  describe('through MessageLine', () => {
    const proseRows = (msg: Msg, cols: number) => {
      const rows = stripAnsi(
        renderToString(React.createElement(MessageLine, { cols, msg, t: DEFAULT_THEME }), cols)
      ).split('\n')

      while (rows.length && rows[rows.length - 1]!.trim() === '') {
        rows.pop()
      }

      return rows.length
    }

    const proseCases: [string, Msg][] = [
      ['bare assistant prose', { role: 'assistant', text: 'ok' }],
      ['prose with reasoning', { role: 'assistant', text: 'ok', thinking: 'plan' }],
      ['prose with a tool brief', { role: 'assistant', text: 'ok', tools: [READ_LINE] }],
      ['prose with both', { role: 'assistant', text: 'ok', thinking: 'plan', tools: [READ_LINE] }],
      // No text means no `└─ Response` separator, but the details wrapper's
      // margin is still paid — the two must not be conflated.
      ['details with no prose', { role: 'assistant', text: '', tools: [READ_LINE] }],
      // Details on a non-assistant row: no separator either, same margin.
      ['a system row carrying details', { role: 'system', text: 'note', tools: [READ_LINE] }]
    ]

    for (const [name, msg] of proseCases) {
      it(`agrees on ${name}`, () => {
        expect(estimatedMsgHeight(msg, 80, { compact: false, details: true })).toBe(proseRows(msg, 80))
      })
    }

    // The structured-diff branch brings its own wrapper with ToolTrail as a
    // direct child, so it must NOT be charged the details wrapper's margin.
    // (Its absolute estimate is off for older reasons — it still counts
    // msg.text for a markdown fallback the structured path never renders — so
    // pin the delta rather than the number.)
    it('does not charge the details margin to a structured diff', () => {
      const diff: Msg = {
        diffData: { filePath: 'a.py', hunks: [], kind: 'update' },
        kind: 'diff',
        role: 'assistant',
        text: 'patch',
        tools: [READ_LINE]
      }

      const withDetails = estimatedMsgHeight(diff, 80, { compact: false, details: true })
      const withoutDetails = estimatedMsgHeight(diff, 80, { compact: false, details: false })

      // Turning details on costs the one brief row, plus the 2 the estimator
      // charges for a `Response` separator this branch never paints (older
      // divergence, left alone). The point is that it is 3 and not 4 — the
      // details wrapper's marginBottom belongs to prose rows only.
      expect(withDetails - withoutDetails).toBe(3)
    })
  })

  // Narrow widths are where wrapping bites; 100 is the everyday case.
  for (const cols of [60, 100]) {
    for (const [name, msg] of cases) {
      it(`agrees on ${name} (collapsed, cols=${cols})`, () => {
        expect(estimatedMsgHeight(msg, cols, { compact: false, details: true })).toBe(
          paintedRows(msg, 'collapsed', cols)
        )
      })

      it(`agrees on ${name} (expanded, cols=${cols})`, () => {
        expect(estimatedMsgHeight(msg, cols, { compact: false, details: true, toolsExpanded: true })).toBe(
          paintedRows(msg, 'expanded', cols)
        )
      })
    }
  }
})
