import { describe, expect, it } from 'vitest'

import { renderToolName, renderToolResult } from './tool-vocabulary.ts'

describe('renderToolName', () => {
  it('maps the ClawCodex vocabulary onto the renderer names', () => {
    expect(renderToolName('Read')).toBe('read_file')
    expect(renderToolName('Bash')).toBe('terminal')
    expect(renderToolName('Grep')).toBe('search_files')
  })

  it('passes unknown tools through unchanged', () => {
    expect(renderToolName('Task')).toBe('Task')
    expect(renderToolName('mcp__linear__create_issue')).toBe('mcp__linear__create_issue')
  })
})

describe('renderToolResult', () => {
  it('summarises a numbered read like the live gateway does', () => {
    const result = renderToolResult('Read', '1\tconst a = 1\n2\tconst b = 2')

    expect(result.content).toBe('1\tconst a = 1\n2\tconst b = 2')
    expect(result.context).toBe('Read 2 lines')
  })

  it('leaves an un-numbered read without a line summary', () => {
    expect(renderToolResult('Read', 'binary blob').context).toBeUndefined()
  })

  it('counts matches for a resumed search', () => {
    const result = renderToolResult('Grep', 'a.ts:1:x\nb.ts:2:y\n')

    expect(result.match_count).toBe(2)
    expect(result.output).toContain('a.ts:1:x')
  })

  it('counts files for a resumed glob', () => {
    expect(renderToolResult('Glob', 'a.ts\nb.ts\nc.ts').file_count).toBe(3)
  })

  it('keeps terminal output on the output field', () => {
    expect(renderToolResult('Bash', 'ok')).toEqual({ output: 'ok' })
  })

  it('returns nothing for empty output', () => {
    expect(renderToolResult('Bash', '')).toEqual({})
  })
})
