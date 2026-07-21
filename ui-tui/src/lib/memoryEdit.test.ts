import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  editorHint,
  ensureMemoryFile,
  memoryEditorArgv,
  openMemoryFileInEditor,
  relativeMemoryPath
} from './memoryEdit.js'

describe('ensureMemoryFile', () => {
  let home: string

  beforeEach(() => {
    home = mkdtempSync(join(tmpdir(), 'mem-home-'))
  })

  it('creates the config home and file for the user memory target', () => {
    const path = join(home, '.clawcodex', 'CLAWCODEX.md')

    ensureMemoryFile(path, home)

    expect(readFileSync(path, 'utf8')).toBe('')
  })

  it('preserves existing content (exclusive create swallows EEXIST)', () => {
    const path = join(home, 'CLAWCODEX.md')

    writeFileSync(path, 'precious')
    ensureMemoryFile(path, home)

    expect(readFileSync(path, 'utf8')).toBe('precious')
  })

  it('propagates non-EEXIST errors (missing dir outside the config home)', () => {
    expect(() => ensureMemoryFile(join(home, 'nope', 'CLAWCODEX.md'), home)).toThrow()
  })
})

describe('editorHint', () => {
  it('names $VISUAL above $EDITOR', () => {
    expect(editorHint({ EDITOR: 'vim', VISUAL: 'helix' })).toBe(
      '> Using $VISUAL="helix". To change editor, set $EDITOR or $VISUAL environment variable.'
    )
  })

  it('names $EDITOR when $VISUAL is unset', () => {
    expect(editorHint({ EDITOR: 'vim' })).toBe(
      '> Using $EDITOR="vim". To change editor, set $EDITOR or $VISUAL environment variable.'
    )
  })

  it('falls back to the set-an-editor hint', () => {
    expect(editorHint({})).toBe(
      '> To use a different editor, set the $EDITOR or $VISUAL environment variable.'
    )
  })
})

describe('relativeMemoryPath', () => {
  it('prefers the shorter of ~ and ./ spellings', () => {
    expect(relativeMemoryPath('/home/u/w/CLAWCODEX.md', '/home/u/w', '/home/u')).toBe('./CLAWCODEX.md')
    expect(relativeMemoryPath('/home/u/.clawcodex/CLAWCODEX.md', '/home/u/deep/nested/dir', '/home/u')).toBe(
      '~/.clawcodex/CLAWCODEX.md'
    )
  })

  it('falls back to the absolute path outside home and cwd', () => {
    expect(relativeMemoryPath('/etc/CLAWCODEX.md', '/home/u/w', '/home/u')).toBe('/etc/CLAWCODEX.md')
  })
})

describe('memoryEditorArgv', () => {
  it('appends the wait flag to a bare GUI editor', () => {
    expect(memoryEditorArgv('/f.md', { EDITOR: 'code', PATH: '' })).toEqual(['code', '-w', '/f.md'])
    expect(memoryEditorArgv('/f.md', { EDITOR: '/usr/local/bin/subl', PATH: '' })).toEqual([
      '/usr/local/bin/subl',
      '--wait',
      '/f.md'
    ])
  })

  it('never second-guesses user-supplied arguments', () => {
    expect(memoryEditorArgv('/f.md', { EDITOR: 'code --wait', PATH: '' })).toEqual(['code', '--wait', '/f.md'])
  })

  it('leaves terminal editors untouched', () => {
    expect(memoryEditorArgv('/f.md', { EDITOR: 'vim', PATH: '' })).toEqual(['vim', '/f.md'])
  })
})

describe('openMemoryFileInEditor', () => {
  let home: string
  let cwd: string

  beforeEach(() => {
    home = mkdtempSync(join(tmpdir(), 'mem-home-'))
    cwd = join(home, 'w')
    mkdirSync(cwd)
  })

  const deps = (over: Record<string, unknown> = {}) => {
    const calls: string[] = []

    return {
      calls,
      cwd,
      env: { EDITOR: 'vim' } as NodeJS.ProcessEnv,
      home,
      notifyEdited: vi.fn(() => void calls.push('notify')),
      spawn: vi.fn((cmd: string, args: string[]) => {
        calls.push(`spawn:${cmd} ${args.join(' ')}`)

        return {}
      }),
      suspend: vi.fn(async (run: () => Promise<void>) => {
        calls.push('suspend:enter')
        await run()
        calls.push('suspend:exit')
      }),
      sys: vi.fn((text: string) => void calls.push(`sys:${text}`)),
      ...over
    }
  }

  it('creates the file, spawns inside the suspend, notifies, and reports', async () => {
    const d = deps()
    const path = join(cwd, 'CLAWCODEX.md')

    await openMemoryFileInEditor(path, d)

    expect(readFileSync(path, 'utf8')).toBe('')
    expect(d.calls).toEqual([
      'suspend:enter',
      `spawn:vim ${path}`,
      'suspend:exit',
      'notify',
      `sys:Opened memory file at ./CLAWCODEX.md\n\n${editorHint({ EDITOR: 'vim' })}`
    ])
  })

  it('reports a launch failure and skips the edited notification', async () => {
    const d = deps({ spawn: vi.fn(() => ({ error: new Error('ENOENT') })) })

    await openMemoryFileInEditor(join(cwd, 'CLAWCODEX.md'), d)

    expect(d.notifyEdited).not.toHaveBeenCalled()
    expect(d.sys).toHaveBeenCalledWith('Error opening memory file: Error: ENOENT')
  })

  it('reports ensure-create failures without spawning', async () => {
    const d = deps()

    await openMemoryFileInEditor(join(cwd, 'missing-dir', 'CLAWCODEX.md'), d)

    expect(d.spawn).not.toHaveBeenCalled()
    expect(d.sys.mock.calls[0]?.[0]).toMatch(/^Error opening memory file: /)
  })
})
