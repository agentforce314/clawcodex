import { describe, expect, it } from 'vitest'

import type { ToolNode } from '../state/transcript.ts'
import {
  describeTool,
  formatArgs,
  genericBodyText,
  prettyMaybeJson,
  readTaskEntries,
  shortPath,
  synthesizeDiff,
  todoSummary,
} from './tool-view.ts'

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

  it('falls back to an IN/OUT row for an unknown tool', () => {
    const view = describeTool(tool({ context: 'Explore the repo', name: 'Task', state: 'running' }))

    expect(view).toMatchObject({ body: 'io', icon: 'tool', title: 'Task' })
    expect(view.summary).toBe('Explore the repo')
  })

  it('summarises todos as progress plus the live item', () => {
    const view = describeTool(
      tool({
        args: {
          todos: [
            { activeForm: 'Mapping the code', content: 'Map the code', status: 'completed' },
            { activeForm: 'Porting the polish', content: 'Port the polish', status: 'in_progress' },
            { content: 'Run QA', status: 'pending' },
          ],
        },
        name: 'todo',
        result: { message: 'Todos have been modified successfully.' },
      }),
    )

    expect(view.body).toBe('todo')
    expect(view.summary).toBe('1/3 done · Porting the polish')
  })

  it('gives web tools a web card', () => {
    expect(describeTool(tool({ args: { query: 'x' }, name: 'web_search' })).body).toBe('web')
    expect(describeTool(tool({ args: { url: 'https://a.io' }, name: 'web_extract' })).body).toBe(
      'web',
    )
  })

  it('synthesizes a diff for a rehydrated edit with no inline_diff', () => {
    const view = describeTool(
      tool({
        args: { file_path: '/repo/a.ts', new_string: 'const b = 2', old_string: 'const a = 1' },
        name: 'edit_file',
        result: { message: 'The file /repo/a.ts has been updated successfully.' },
      }),
      '/repo',
    )

    expect(view.body).toBe('diff')
    expect(view.summary).toBe('')
  })
})

describe('describeTool task registry rows', () => {
  it('summarises TaskCreate by its subject, never its JSON', () => {
    const view = describeTool(
      tool({
        args: { subject: 'Map DeepSeek R-series lineup' },
        name: 'TaskCreate',
        result: {
          context: '{"task": {"id": "a0a7148e59a2", "subject": "Map DeepSeek R-series lineup"}}',
          output: '{"task": {"id": "a0a7148e59a2", "subject": "Map DeepSeek R-series lineup"}}',
        },
      }),
    )

    expect(view).toMatchObject({ icon: 'list', title: 'Task' })
    expect(view.summary).toBe('Map DeepSeek R-series lineup')
  })

  it('summarises TaskUpdate as subject → status word', () => {
    const view = describeTool(
      tool({
        args: { status: 'completed', taskId: 'a0a7148e59a2' },
        context: 'Map DeepSeek R-series lineup',
        name: 'TaskUpdate',
        result: { output: '{"success": true, "taskId": "a0a7148e59a2"}' },
      }),
    )

    expect(view.summary).toBe('Map DeepSeek R-series lineup → completed')
  })

  it('recovers the new status from the result when the args lack it', () => {
    const view = describeTool(
      tool({
        args: { taskId: 'a0a7148e59a2' },
        name: 'TaskUpdate',
        result: {
          output: '{"success": true, "statusChange": {"from": "pending", "to": "in_progress"}}',
        },
      }),
    )

    expect(view.summary).toBe('a0a7148e59a2 → started')
  })

  it('reads a TaskList result as a checklist', () => {
    const node = tool({
      name: 'TaskList',
      result: {
        output:
          '{"tasks": [{"id": "a", "subject": "First", "status": "completed"}, {"id": "b", "subject": "Second", "status": "pending"}]}',
      },
    })
    const view = describeTool(node)

    expect(view).toMatchObject({ body: 'todo', title: 'Tasks' })
    expect(view.summary).toBe('1/2 done')
    expect(readTaskEntries(node)).toEqual([
      { active: 'First', content: 'First', status: 'completed' },
      { active: 'Second', content: 'Second', status: 'pending' },
    ])
  })

  it('never quotes JSON in a generic summary', () => {
    const view = describeTool(
      tool({
        name: 'mcp__linear__create_issue',
        result: {
          context: '{"success": true, "id": "LIN-42"}',
          output: '{"success": true, "id": "LIN-42"}',
        },
      }),
    )

    expect(view.summary).toBe('')
  })
})

describe('prettyMaybeJson', () => {
  it('indents a JSON payload', () => {
    expect(prettyMaybeJson('{"a": 1, "b": [2, 3]}')).toBe('{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}')
  })

  it('leaves prose alone', () => {
    expect(prettyMaybeJson('two unused exports found')).toBe('two unused exports found')
    expect(prettyMaybeJson('{not json')).toBe('{not json')
  })
})

describe('synthesizeDiff', () => {
  it('prefers the diff the gateway sent', () => {
    const diff = '--- a\n+++ a\n@@ -1 +1 @@\n-x\n+y'

    expect(synthesizeDiff(tool({ name: 'edit_file', result: { inline_diff: diff } }))).toBe(diff)
  })

  it('reconstructs an edit from old_string and new_string', () => {
    const node = tool({
      args: { file_path: '/repo/a.ts', new_string: 'two\nlines', old_string: 'one line' },
      name: 'edit_file',
    })

    expect(synthesizeDiff(node)).toBe('+++ /repo/a.ts\n-one line\n+two\n+lines')
  })

  it('reconstructs a MultiEdit as gap-separated hunks', () => {
    const node = tool({
      args: {
        edits: [
          { new_string: 'b', old_string: 'a' },
          { new_string: 'd', old_string: 'c' },
        ],
        file_path: '/repo/a.ts',
      },
      name: 'edit_file',
    })

    expect(synthesizeDiff(node)).toBe('+++ /repo/a.ts\n-a\n+b\n@@\n-c\n+d')
  })

  it('reconstructs a write as pure additions', () => {
    const node = tool({
      args: { content: 'alpha\nbeta\n', file_path: '/repo/notes.md' },
      name: 'write_file',
    })

    expect(synthesizeDiff(node)).toBe('+++ /repo/notes.md\n+alpha\n+beta')
  })

  it('declines when the arguments carry no mutation', () => {
    expect(synthesizeDiff(tool({ name: 'edit_file' }))).toBeUndefined()
    expect(synthesizeDiff(tool({ name: 'write_file' }))).toBeUndefined()
  })

  it('declines for a failed call: nothing was written', () => {
    const node = tool({
      args: { new_string: 'b', old_string: 'a' },
      error: 'Error: not found',
      name: 'edit_file',
    })

    expect(synthesizeDiff(node)).toBeUndefined()
  })
})

describe('formatArgs', () => {
  it('renders scalar arguments as key: value lines', () => {
    expect(formatArgs({ description: 'Audit the store', subagent_type: 'Explore' })).toBe(
      'description: Audit the store\nsubagent_type: Explore',
    )
  })

  it('indents multi-line and structured values under their key', () => {
    expect(formatArgs({ prompt: 'line one\nline two' })).toBe('prompt:\n  line one\n  line two')
    expect(formatArgs({ flags: [1, 2] })).toBe('flags:\n  [\n   1,\n   2\n  ]')
  })

  it('returns empty for no arguments', () => {
    expect(formatArgs({})).toBe('')
  })
})

describe('todoSummary', () => {
  it('is empty without todos', () => {
    expect(todoSummary({})).toBe('')
  })

  it('shows progress alone when nothing is in flight', () => {
    expect(
      todoSummary({ todos: [{ content: 'a', status: 'completed' }, { content: 'b', status: 'pending' }] }),
    ).toBe('1/2 done')
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
