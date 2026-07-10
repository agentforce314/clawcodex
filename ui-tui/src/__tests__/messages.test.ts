import { PassThrough } from 'stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import { MessageLine, transcriptRowBand } from '../components/messageLine.js'
import { toTranscriptMessages } from '../domain/messages.js'
import { upsert } from '../lib/messages.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

describe('toTranscriptMessages', () => {
  it('preserves assistant tool-call rows so resume does not drop prior turns', () => {
    const rows = [
      { role: 'user', text: 'first prompt' },
      { role: 'tool', context: 'repo', name: 'search_files', text: 'ignored raw result' },
      { role: 'assistant', text: 'first answer' },
      { role: 'user', text: 'second prompt' }
    ]

    expect(toTranscriptMessages(rows).map(msg => [msg.role, msg.text])).toEqual([
      ['user', 'first prompt'],
      ['assistant', 'first answer'],
      ['user', 'second prompt']
    ])
    expect(toTranscriptMessages(rows)[1]?.tools?.[0]).toContain('Search Files')
  })
})

describe('MessageLine', () => {
  it('preserves a separator after compound user prompt glyphs in transcript rows', () => {
    const stdout = new PassThrough()
    const stdin = new PassThrough()
    const stderr = new PassThrough()
    let output = ''

    Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
    Object.assign(stdin, { isTTY: false })
    Object.assign(stderr, { isTTY: false })
    stdout.on('data', chunk => {
      output += chunk.toString()
    })

    const t = {
      ...DEFAULT_THEME,
      brand: { ...DEFAULT_THEME.brand, prompt: 'Ψ >' }
    }

    const instance = renderSync(
      React.createElement(MessageLine, {
        cols: 80,
        msg: { role: 'user', text: 'Okay' },
        t
      }),
      {
        patchConsole: false,
        stderr: stderr as NodeJS.WriteStream,
        stdin: stdin as NodeJS.ReadStream,
        stdout: stdout as NodeJS.WriteStream
      }
    )

    instance.unmount()
    instance.cleanup()

    const renderedLine = stripAnsi(output)
      .split('\n')
      .find(line => line.includes('Okay'))

    // The transcript pointer is the fixed `❯` (roles.ts, original CC
    // figures.pointer) — the compound brand prompt 'Ψ >' only widens the
    // gutter (composerPromptWidth = 4), so the glyph must be padded with the
    // full 3-column separator before the body, not collapsed to one space.
    expect(renderedLine).toContain('❯   Okay')
  })

  // Original-CC transcript emphasis: past user inputs sit on a
  // userMessageBackground band (UserPromptMessage.tsx:76); slash echoes get
  // the same band + user pointer (UserCommandMessage.tsx:62); assistant rows
  // stay bandless.
  const renderRaw = (msg: Record<string, unknown>) => {
    const stdout = new PassThrough()
    const stdin = new PassThrough()
    const stderr = new PassThrough()
    let output = ''

    Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
    Object.assign(stdin, { isTTY: false })
    Object.assign(stderr, { isTTY: false })
    stdout.on('data', chunk => {
      output += chunk.toString()
    })

    const instance = renderSync(
      React.createElement(MessageLine, { cols: 80, msg: msg as never, t: DEFAULT_THEME }),
      {
        patchConsole: false,
        stderr: stderr as NodeJS.WriteStream,
        stdin: stdin as NodeJS.ReadStream,
        stdout: stdout as NodeJS.WriteStream
      }
    )

    instance.unmount()
    instance.cleanup()

    return output
  }

  it('paints the userMessageBackground band behind user rows and slash echoes only', () => {
    const band = DEFAULT_THEME.color.userMessageBackground

    expect(transcriptRowBand({ role: 'user', text: 'find the bug' } as never, DEFAULT_THEME)).toBe(band)
    expect(transcriptRowBand({ kind: 'slash', role: 'system', text: '/cost' } as never, DEFAULT_THEME)).toBe(band)
    expect(transcriptRowBand({ role: 'assistant', text: 'prose' } as never, DEFAULT_THEME)).toBeUndefined()
    expect(transcriptRowBand({ role: 'system', text: 'note' } as never, DEFAULT_THEME)).toBeUndefined()
    expect(transcriptRowBand({ role: 'tool', text: 'result' } as never, DEFAULT_THEME)).toBeUndefined()
  })

  it('renders slash echoes with the user pointer, not the system dot', () => {
    const raw = stripAnsi(renderRaw({ kind: 'slash', role: 'system', text: '/cost' }))

    expect(raw).toContain('/cost')
    expect(raw).toContain('❯')
    expect(raw).not.toContain('·')
  })
})

describe('upsert', () => {
  it('appends when last role differs', () => {
    expect(upsert([{ role: 'user', text: 'hi' }], 'assistant', 'hello')).toHaveLength(2)
  })

  it('replaces when last role matches', () => {
    expect(upsert([{ role: 'assistant', text: 'partial' }], 'assistant', 'full')[0]!.text).toBe('full')
  })

  it('appends to empty', () => {
    expect(upsert([], 'user', 'first')).toEqual([{ role: 'user', text: 'first' }])
  })

  it('does not mutate', () => {
    const prev = [{ role: 'user' as const, text: 'hi' }]
    upsert(prev, 'assistant', 'yo')
    expect(prev).toHaveLength(1)
  })
})
