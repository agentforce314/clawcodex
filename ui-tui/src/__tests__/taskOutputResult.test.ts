import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { describe, expect, it, vi } from 'vitest'

const harness = vi.hoisted(() => ({ proc: null as null | EventEmitter }))

vi.mock('node:child_process', () => ({ spawn: () => harness.proc }))

import { formatToolResult, GatewayClient } from '../gatewayClient.js'

// ── fixtures ────────────────────────────────────────────────────────────────

/** The tagged content the backend now puts on the wire (tasks_v2.py
 *  `_task_output_map_result_to_api`). Built here rather than imported so a
 *  silent format change on either side fails one of these tests. */
const tagged = (parts: Record<string, string | undefined>, output?: string): string =>
  [
    ...Object.entries(parts)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `<${k}>${v}</${k}>`),
    ...(output === undefined ? [] : [`<output>\n${output}\n</output>`])
  ].join('\n\n')

const bashTask = (output: string, status = 'completed') =>
  tagged(
    {
      retrieval_status: 'success',
      task_id: 'baa0sty3d',
      task_type: 'bash_background',
      status,
      description: 'Build the sandbox image with CA support',
      exit_code: '0'
    },
    output
  )

const BUILD_LOG = [
  'EXIT=1',
  '#7 [internal] load build context',
  '#7 transferring context: 1.95kB done',
  '#7 DONE 0.0s',
  '#8 [node-runtime 2/4] COPY fly/certs/ /usr/local/share/ca-certificates/',
  '#9 0.101 /bin/sh: 1: update-ca-certificates: not found',
  '#9 ERROR: process did not complete successfully: exit code: 127'
].join('\n')

// Exactly the payload from the reported bug — the pre-fix serialization. Still
// reachable, because the TUI and the Python backend update independently: a
// refreshed TUI talking to an older installed `clawcodex` still gets this.
const LEGACY_JSON = JSON.stringify({
  retrieval_status: 'success',
  task: {
    command: 'npm run sandbox:local:build > /tmp/sandbox-build.log 2>&1',
    description: 'Build the sandbox image with CA support',
    exit_code: 0,
    finished_at: 1786432700.10884,
    output: BUILD_LOG,
    pid: 75947,
    started_at: 1786432698.634229,
    status: 'completed',
    task_id: 'baa0sty3d',
    task_type: 'bash_background',
    truncated: false
  }
})

const fmt = (result: string, name = 'TaskOutput') => formatToolResult(name, result)

/** Byte-for-byte what the backend emits, duplicated verbatim from
 *  tests/test_task_output_serialization.py::test_matches_the_tui_golden. The
 *  two runtimes cannot import from each other, so pinning the same literal on
 *  both sides is what catches the serializer and this renderer drifting. */
const BACKEND_GOLDEN = [
  '<retrieval_status>success</retrieval_status>',
  '',
  '<task_id>baa0sty3d</task_id>',
  '',
  '<task_type>bash_background</task_type>',
  '',
  '<status>completed</status>',
  '',
  '<description>Build the sandbox image with CA support</description>',
  '',
  '<exit_code>0</exit_code>',
  '',
  '<output>',
  'EXIT=1',
  '#7 [internal] load build context',
  '#9 ERROR: process did not complete successfully: exit code: 127',
  '</output>'
].join('\n')

// ── the reported bug ────────────────────────────────────────────────────────

describe('TaskOutput no longer dumps its result blob', () => {
  const expected = [
    'EXIT=1',
    '#7 [internal] load build context',
    '#7 transferring context: 1.95kB done',
    '… +4 lines (ctrl+o to expand)'
  ].join('\n')

  it('renders a background shell task the way Bash renders', () => {
    expect(fmt(bashTask(BUILD_LOG))).toBe(expected)
  })

  it('renders the same from an older backend still sending JSON', () => {
    expect(fmt(LEGACY_JSON)).toBe(expected)
  })

  it('renders the exact bytes the backend emits', () => {
    expect(fmt(BACKEND_GOLDEN)).toBe(
      'EXIT=1\n#7 [internal] load build context\n#9 ERROR: process did not complete successfully: exit code: 127'
    )
  })

  it.each([
    ['tagged', bashTask(BUILD_LOG)],
    ['legacy JSON', LEGACY_JSON]
  ])('shows no wire bookkeeping and no escaped newlines (%s)', (_label, content) => {
    const out = fmt(content)

    // The literal two-character `\n` sequence is what made the blob
    // unreadable; a real newline is fine and expected.
    expect(out).not.toContain(String.raw`\n`)
    expect(out).not.toContain('retrieval_status')
    expect(out).not.toContain('started_at')
    expect(out).not.toContain('pid')
    expect(out).not.toContain('{')
    expect(out.split('\n')).toHaveLength(4)
  })
})

// ── per-task-type branches (TaskOutputTool.tsx renderToolResultMessage) ──────

describe('background shell tasks', () => {
  it('shows the whole output when only one line would overflow', () => {
    expect(fmt(bashTask('a\nb\nc\nd'))).toBe('a\nb\nc\nd')
  })

  it('says so when the task produced nothing', () => {
    expect(fmt(bashTask(''))).toBe('(No output)')
  })

  it('says so for an empty tag body rather than echoing the closing tag', () => {
    // extractTag returns null for an empty body too, so the truncated-preview
    // fallback must not claim this one.
    const empty = [
      '<retrieval_status>success</retrieval_status>',
      '',
      '<task_id>t1</task_id>',
      '',
      '<task_type>bash_background</task_type>',
      '',
      '<output></output>'
    ].join('\n')

    expect(fmt(empty)).toBe('(No output)')
  })

  it('says so when the task produced nothing and the tag is absent', () => {
    // `<output>` is omitted entirely for blank output, so the "no output at
    // all" and "output was whitespace" paths must agree.
    expect(fmt(bashTask('   \n  '))).toBe('(No output)')
  })

  it('renders a still-running task from its partial output', () => {
    expect(fmt(bashTask('step 1 done', 'running'))).toBe('step 1 done')
  })

  it('strips only the delimiter newlines, never the task\'s own layout', () => {
    // The backend writes `<output>\n…\n</output>`; those two newlines are
    // framing. A blank first line or an indented first line is content.
    expect(fmt(bashTask('\n    indented'))).toBe('\n    indented')
  })
})

describe('subagent tasks', () => {
  const agent = (retrieval: string, status: string) =>
    tagged(
      { retrieval_status: retrieval, task_id: 'a1', task_type: 'local_agent', status, description: 'Find bugs' },
      'a long multi-paragraph answer\nthat must not be re-printed here'
    )

  it('points at the expansion instead of re-printing the answer', () => {
    expect(fmt(agent('success', 'completed'))).toBe('Read output (ctrl+o to expand)')
  })

  it.each(['timeout', 'not_ready'])('reports %s as still running', retrieval => {
    expect(fmt(agent(retrieval, 'running'))).toBe('Task is still running…')
  })

  it('reports a running task as still running whatever the retrieval status', () => {
    expect(fmt(agent('partial', 'running'))).toBe('Task is still running…')
  })

  it('falls back to "not ready" for a terminal task with no output to read', () => {
    expect(fmt(agent('partial', 'failed'))).toBe('Task not ready')
  })
})

describe('todo and unknown task types', () => {
  it('renders an identity line plus a bounded peek at the output', () => {
    const todo = tagged(
      { retrieval_status: 'success', task_id: '7', task_type: 'task_list', status: 'completed', description: 'Ship it' },
      'all green'
    )

    expect(fmt(todo)).toBe('Ship it [completed]\nall green')
  })

  it('caps the peek at 500 characters', () => {
    const todo = tagged(
      { retrieval_status: 'success', task_id: '7', task_type: 'task_list', status: 'completed', description: 'Ship it' },
      'x'.repeat(2000)
    )

    expect(fmt(todo)).toBe(`Ship it [completed]\n${'x'.repeat(500)}`)
  })

  it('renders the identity line alone when there is no output', () => {
    const todo = tagged({
      retrieval_status: 'success',
      task_id: '7',
      task_type: 'task_list',
      status: 'pending',
      description: 'Ship it'
    })

    expect(fmt(todo)).toBe('Ship it [pending]')
  })
})

describe('degenerate results', () => {
  it('reports an unknown / evicted task id', () => {
    expect(fmt('<retrieval_status>success</retrieval_status>')).toBe('No task output available')
    expect(fmt(JSON.stringify({ retrieval_status: 'success', task: null }))).toBe('No task output available')
  })

  it('does not mistake an empty task id for a missing task', () => {
    // extractTag returns null for an empty body as well as an absent tag, so
    // keying "is there a task?" on extracted content reports a real task as
    // missing. Reachable via the unknown-TaskStateBase branch.
    const empty = '<retrieval_status>success</retrieval_status>\n\n<task_id></task_id>\n\n<status>running</status>'

    expect(fmt(empty)).toBe('[running]')
  })

  it('passes through content it does not recognize', () => {
    expect(fmt('not json, no tags')).toBe('not json, no tags')
    expect(fmt('{"unrelated": true}')).toBe('{"unrelated": true}')
    expect(fmt('<persisted-output>\nOutput too large (204.8KB). Saved to: /t/x.txt\n</persisted-output>')).toBe(
      '<persisted-output>\nOutput too large (204.8KB). Saved to: /t/x.txt\n</persisted-output>'
    )
  })

  // The regression that shipped in the first cut of this change. A result over
  // the persistence threshold is replaced by a <persisted-output> wrapper
  // around a 2KB HEAD preview, which cuts the part list mid-<output>: the head
  // tags survive, the closing </output> does not. extractTag needs the pair,
  // so `output` came back undefined and a 55KB build log rendered as
  // "(No output)" — a false statement, and worse than the raw dump this whole
  // change removes. Byte-shaped exactly like build_large_tool_result_message
  // (tool_result_persistence.py:300-314); note the HYPHEN in the tag name,
  // which the original version of this test got wrong.
  it('renders the log when the persistence wrapper cut <output> open', () => {
    const preview = [
      '<retrieval_status>success</retrieval_status>',
      '',
      '<task_id>baa0sty3d</task_id>',
      '',
      '<task_type>bash_background</task_type>',
      '',
      '<status>failed</status>',
      '',
      '<output>',
      'EXIT=1',
      '#7 [internal] load build context',
      '#8 COPY fly/certs/',
      '#9 ERROR: update-ca-certificates: not found',
      '#10 truncated mid-li'
    ].join('\n')

    const wrapped = [
      '<persisted-output>',
      'Output too large (54.7KB). Full output saved to: /t/tool-results/toolu_1.txt',
      '',
      'Preview (first 2.0KB):',
      preview,
      '...',
      '</persisted-output>'
    ].join('\n')

    const out = fmt(wrapped)

    expect(out).not.toBe('(No output)')
    expect(out.split('\n')[0]).toBe('EXIT=1')
    expect(out).toContain('#7 [internal] load build context')
    // The wrapper's own trailing bytes are not log lines.
    expect(out).not.toContain('</persisted-output>')
  })

  it('does not claim "no output" when the preview ended before <output>', () => {
    // The other half of the same class: an oversized <description> can eat the
    // whole 2KB window, so there is no opening tag to recover from either.
    // Showing the wrapper is honest — it names the file holding the rest.
    const wrapped = [
      '<persisted-output>',
      'Output too large (54.7KB). Full output saved to: /t/tool-results/toolu_1.txt',
      '',
      'Preview (first 2.0KB):',
      '<retrieval_status>success</retrieval_status>',
      '',
      '<task_id>t3</task_id>',
      '',
      '<task_type>bash_background</task_type>',
      '',
      `<description>${'D'.repeat(1800)}`,
      '...',
      '</persisted-output>'
    ].join('\n')

    const out = fmt(wrapped)

    expect(out).not.toBe('(No output)')
    expect(out).toContain('/t/tool-results/toolu_1.txt')
  })

  it('leaves errors to the error path', () => {
    expect(formatToolResult('TaskOutput', 'No task found with ID: nope', true)).toBe(
      'Error: No task found with ID: nope'
    )
  })

  it.each(['AgentOutputTool', 'BashOutputTool'])('routes the %s alias to the same renderer', alias => {
    expect(fmt(bashTask('a\nb'), alias)).toBe('a\nb')
  })

  it('still summarizes when the tool name is unknown (mid-turn attach)', () => {
    expect(formatToolResult(undefined, bashTask('a\nb'))).toBe('a\nb')
    expect(
      formatToolResult(undefined, `<stuck_task_hint>stop polling</stuck_task_hint>\n\n${bashTask('a\nb')}`)
    ).toBe('a\nb')
  })

  it('does not claim an unnamed result that is not a TaskOutput', () => {
    expect(formatToolResult(undefined, '<retrieval_status_other>x</retrieval_status_other>')).toBe(
      '<retrieval_status_other>x</retrieval_status_other>'
    )
    expect(formatToolResult(undefined, LEGACY_JSON)).toBe(LEGACY_JSON)
  })
})

// ── end to end through the client ───────────────────────────────────────────

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

describe('tool.complete payload', () => {
  it('summarizes the row and keeps the full result behind ctrl+o', async () => {
    const proc = new FakeProc()

    harness.proc = proc

    const events: any[] = []
    const gw = new GatewayClient()

    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain()

    const last = (t: string) => [...events].reverse().find(e => e.type === t)
    const content = bashTask(BUILD_LOG)

    proc.line({
      message: { content: [{ id: 't1', input: { task_id: 'baa0sty3d' }, name: 'TaskOutput', type: 'tool_use' }] },
      type: 'assistant'
    })
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())

    proc.line({
      message: { content: [{ content, tool_use_id: 't1', type: 'tool_result' }] },
      type: 'user'
    })
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())
    gw.kill()

    const payload = last('tool.complete').payload

    expect(payload.result_text).toBe(
      'EXIT=1\n#7 [internal] load build context\n#7 transferring context: 1.95kB done\n… +4 lines (ctrl+o to expand)'
    )
    expect(payload.error).toBeUndefined()
    // The compact row dropped the tail, so the expandable original is retained.
    expect(payload.result_raw).toBe(content)
  })
})
