import { describe, expect, it } from 'vitest'

import type { ToolNode } from '../state/transcript.ts'
import { describeTool, genericBodyText, shortPath } from './tool-view.ts'

function tool(overrides: Partial<ToolNode> = {}): ToolNode {
  return {
    args: {},
    id: 'n1',
    kind: 'tool',
    name: 'terminal',
    startedAt: 0,
    state: 'done',
    toolId: 't1',
    ...overrides,
  }
}

describe('shortPath', () => {
  it('strips the workspace prefix', () => {
    expect(shortPath('/repo/src/app.ts', '/repo')).toBe('src/app.ts')
  })

  it('keeps the tail when the path is outside the workspace', () => {
    expect(shortPath('/a/b/c/d/e.ts', '/repo')).toBe('…/c/d/e.ts')
  })

  it('leaves a short path alone', () => {
    expect(shortPath('src/app.ts')).toBe('src/app.ts')
  })
})

describe('describeTool', () => {
  it('shows a shell command as the row summary and a terminal card', () => {
    const view = describeTool(tool({ args: { command: 'ls -la' } }))

    expect(view).toMatchObject({ body: 'terminal', icon: 'terminal', title: 'Bash' })
    expect(view.summary).toBe('ls -la')
  })

  it('gives a read its path and a line-numbered card', () => {
    const view = describeTool(
      tool({
        args: { file_path: '/repo/src/app.ts' },
        name: 'read_file',
        result: { content: '1\tconst a = 1' },
      }),
      '/repo',
    )

    expect(view).toMatchObject({ body: 'read', icon: 'file', path: 'src/app.ts', title: 'Read' })
  })

  it('gives an edit a diff card when a diff came back', () => {
    const view = describeTool(
      tool({
        args: { path: '/repo/a.ts' },
        name: 'edit_file',
        result: { inline_diff: '--- a\n+++ a\n@@ -1 +1 @@\n-x\n+y' },
      }),
      '/repo',
    )

    expect(view.body).toBe('diff')
    // The diff IS the result; a summary beside it would repeat it.
    expect(view.summary).toBe('')
  })

  it('counts results for list and search families', () => {
    expect(describeTool(tool({ name: 'list_files', result: { file_count: 3 } })).summary).toBe(
      '3 files',
    )
    expect(describeTool(tool({ name: 'search_files', result: { match_count: 1 } })).summary).toBe(
      '1 match',
    )
  })

  it('reports a web search with its duration', () => {
    const view = describeTool(
      tool({ name: 'web_search', result: { duration_s: 1.4, result_count: 5 }, state: 'done' }),
    )

    // A finished search summarises its own result, not the query.
    expect(view.title).toBe('Web search')
  })

  it('prefers the failure reason over any output', () => {
    const view = describeTool(
      tool({ error: 'Error: path outside workspace\nmore detail', name: 'write_file' }),
    )

    expect(view.summary).toBe('Error: path outside workspace')
    expect(view.body).toBe('output')
  })

  it('falls back to a generic row for an unknown tool', () => {
    const view = describeTool(tool({ context: 'Explore the repo', name: 'Task', state: 'running' }))

    expect(view).toMatchObject({ body: 'output', icon: 'tool', title: 'Task' })
    expect(view.summary).toBe('Explore the repo')
  })
})

describe('genericBodyText', () => {
  it('prefers the error, then output, then content', () => {
    expect(genericBodyText(tool({ error: 'boom' }))).toBe('boom')
    expect(genericBodyText(tool({ result: { content: 'c', output: 'o' } }))).toBe('o')
    expect(genericBodyText(tool({ result: { content: 'c' } }))).toBe('c')
    expect(genericBodyText(tool())).toBe('')
  })
})
