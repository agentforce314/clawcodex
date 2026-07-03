import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The clawcodex GatewayClient is an adapter that spawns `clawcodex
// agent-server --stdio` and maps its stdout NDJSON to clawcodex GatewayEvents.
// We fake the child process so the test can feed protocol lines on stdout and
// observe the emitted events. (The previous suite here tested an older
// WebSocket attach-mode client that the NDJSON rewrite in #572 removed.)
const harness = vi.hoisted(() => ({ proc: null as null | EventEmitter, spawnCalls: [] as unknown[][] }))

vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => {
    harness.spawnCalls.push(args)

    return harness.proc
  }
}))

import { approvalCommandText, GatewayClient } from '../gatewayClient.js'

class FakeProc extends EventEmitter {
  kill = vi.fn()
  stderr = new PassThrough()
  stdin = new PassThrough()
  stdout = new PassThrough()

  /** Feed one NDJSON protocol message to the client as a stdout line. */
  line(obj: unknown): void {
    this.stdout.write(JSON.stringify(obj) + '\n')
  }
}

const toolUse = (id: string, name: string, input: unknown) => ({
  message: { content: [{ id, input, name, type: 'tool_use' }] },
  type: 'assistant'
})

const toolResult = (id: string, content: unknown, isError = false) => ({
  message: { content: [{ content, is_error: isError, tool_use_id: id, type: 'tool_result' }] },
  type: 'user'
})

const INIT = {
  cwd: '/ws',
  model: 'claude-test',
  protocol_version: '0.1.0',
  session_id: 's1',
  subtype: 'init',
  tools: [{ name: 'Read' }, { name: 'Bash' }],
  type: 'system'
}

describe('GatewayClient NDJSON adapter', () => {
  const prevWs = process.env.CLAWCODEX_WORKSPACE
  let events: any[]
  let gw: GatewayClient
  let proc: FakeProc

  beforeEach(() => {
    process.env.CLAWCODEX_WORKSPACE = '/ws'
    proc = new FakeProc()
    harness.proc = proc
    harness.spawnCalls = []
    events = []
    gw = new GatewayClient()
    gw.on('event', (e: any) => events.push(e))
    gw.start()
    gw.drain() // subscribe so publish() emits live instead of buffering
  })

  afterEach(() => {
    gw.kill()

    if (prevWs === undefined) {delete process.env.CLAWCODEX_WORKSPACE}
    else {process.env.CLAWCODEX_WORKSPACE = prevWs}
  })

  const types = () => events.map(e => e.type)
  const last = (t: string) => [...events].reverse().find(e => e.type === t)

  // Emit a tool_use then await its tool.start (so toolInputs is populated),
  // then emit the matching tool_result and await its tool.complete.
  const runTool = async (id: string, name: string, input: unknown, result: unknown) => {
    proc.line(toolUse(id, name, input))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line(toolResult(id, result))
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())

    return last('tool.complete').payload
  }

  it('spawns the agent-server and emits gateway.ready + session.info on init', async () => {
    expect(harness.spawnCalls).toHaveLength(1)
    proc.line(INIT)
    await vi.waitFor(() => expect(types()).toContain('gateway.ready'))
    expect(types()).toContain('session.info')
    await expect(gw.request('session.create', {})).resolves.toMatchObject({ session_id: 's1' })
  })

  it('labels file tools with a workspace-relative path', async () => {
    proc.line(toolUse('t1', 'Read', { file_path: '/ws/src/foo.ts' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload).toMatchObject({ context: 'src/foo.ts', name: 'Read', tool_id: 't1' })
  })

  it('falls back to the basename for paths outside the workspace', async () => {
    proc.line(toolUse('t1', 'Read', { file_path: '/etc/hosts' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.context).toBe('hosts')
  })

  it('labels Bash with its command (no path relativization)', async () => {
    proc.line(toolUse('t1', 'Bash', { command: 'ls -la' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.context).toBe('ls -la')
  })

  it('labels search tools with the pattern, not the search directory', async () => {
    proc.line(toolUse('t1', 'Grep', { path: '/ws/src', pattern: 'TODO' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    expect(last('tool.start').payload.context).toBe('TODO')
  })

  // Read's numbered output is `f"{i}\t{line}"` joined by "\n" — no leading pad,
  // no trailing newline (src/tool_system/tools/read.py).
  it('collapses a Read result to a line count', async () => {
    const p = await runTool('t1', 'Read', { file_path: '/ws/a.ts' }, '1\tconst a = 1\n2\tconst b = 2\n3\tconst c = 3')
    expect(p.result_text).toBe('Read 3 lines')
  })

  it('uses the singular for a one-line Read result', async () => {
    const p = await runTool('t1', 'Read', { file_path: '/ws/a.ts' }, '1\tonly line')
    expect(p.result_text).toBe('Read 1 line')
  })

  // Read's non-text acks aren't `N\t…` numbered output, so they must NOT be
  // collapsed (the empty-file case would otherwise become a false "Read 1 line"
  // and bury the warning).
  it('does not collapse the empty-file warning', async () => {
    const warning = '<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>'
    const p = await runTool('t1', 'Read', { file_path: '/ws/empty.ts' }, warning)
    expect(p.result_text).toBe(warning)
  })

  it('does not collapse the file_unchanged dedup stub', async () => {
    const stub = 'File unchanged since last read. The content from the earlier Read tool_result in this conversation is still current — refer to that instead of re-reading.'
    const p = await runTool('t1', 'Read', { file_path: '/ws/a.ts' }, stub)
    expect(p.result_text).toBe(stub)
  })

  it('passes short Bash results through and caps long ones (CC parity)', async () => {
    const p = await runTool('t1', 'Bash', { command: 'echo hi' }, 'hi\n')
    expect(p.result_text).toBe('hi')

    const long = await runTool('t2', 'Bash', { command: 'seq 9' }, '1\n2\n3\n4\n5\n6')
    expect(long.result_text).toBe('1\n2\n3\n… +3 lines (ctrl+o to expand)')
  })

  it('carries error on tool.complete for failed tools (drives the red ✗ path)', async () => {
    proc.line(toolUse('t1', 'Bash', { command: 'false' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line(toolResult('t1', 'exit 1', true))
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())

    const p = last('tool.complete').payload
    expect(p.error).toBe('Error: exit 1')
    expect(p.result_text).toBe('Error: exit 1')
  })

  it('keeps a failed Read result visible (Error-prefixed), not collapsed to a line count', async () => {
    proc.line(toolUse('t1', 'Read', { file_path: '/ws/missing.ts' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line(toolResult('t1', 'File does not exist.', true))
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())
    expect(last('tool.complete').payload.result_text).toBe('Error: File does not exist.')
    expect(last('tool.complete').payload.error).toBe('Error: File does not exist.')
  })

  it('attaches an inline diff for Edit results', async () => {
    const p = await runTool('t1', 'Edit', { file_path: '/ws/a.ts', new_string: 'b', old_string: 'a' }, 'ok')
    expect(p.name).toBe('Edit')
    expect(p.inline_diff).toContain('-a')
    expect(p.inline_diff).toContain('+b')
  })

  // ── workflow surfaces ──────────────────────────────────────────────────────

  /** Requests the client wrote to the agent-server's stdin, parsed. */
  const stdinFrames = (): any[] => {
    const raw = (proc.stdin as any).read()?.toString() ?? ''

    return raw
      .split('\n')
      .filter(Boolean)
      .map((l: string) => JSON.parse(l))
  }

  /** Wait for the client to send a control_request of `subtype`, then feed the
   *  matching control_response back on stdout. `seen` accumulates the stdin
   *  frames of the CURRENT test only (fresh proc per test) — reset per test so
   *  a stale frame from a prior client can never misroute a reply. */
  let seen: any[] = []
  beforeEach(() => {
    seen = []
  })

  const replyToControl = async (subtype: string, response: unknown) => {
    let req: any
    await vi.waitFor(() => {
      seen.push(...stdinFrames())
      req = seen.find(f => f.type === 'control_request' && f.request?.subtype === subtype)
      expect(req).toBeTruthy()
    })
    proc.line({ response: { request_id: req.request_id, response }, type: 'control_response' })
  }

  it('maps /workflows to the workflows control and prints its report', async () => {
    const p = gw.request('slash.exec', { command: 'workflows' })
    await replyToControl('workflows', { ok: true, text: 'deep-research  [running]  (run: wf_1)' })
    await expect(p).resolves.toEqual({ output: 'deep-research  [running]  (run: wf_1)', type: 'exec' })
  })

  it('confirms the applied mode from the server reply on /mode', async () => {
    const p = gw.request('slash.exec', { command: 'mode acceptEdits' })
    await replyToControl('set_permission_mode', { mode: 'acceptEdits', ok: true })
    await expect(p).resolves.toEqual({ output: 'Permission mode: acceptEdits.', type: 'exec' })
  })

  it('treats bare /mode as a no-op query, not an empty set', async () => {
    // No arg → must NOT send an empty mode the server would reject; report
    // unchanged instead (the pre-hardening behavior).
    const p = gw.request('slash.exec', { command: 'mode' })
    const r: any = await p
    expect(r).toEqual({ output: 'Permission mode: (unchanged).', type: 'exec' })
    // And it must not have hit the backend at all.
    expect(stdinFrames().some(f => f.request?.subtype === 'set_permission_mode')).toBe(false)
  })

  it('reflects the server rejection through config.set permission_mode', async () => {
    // The settings-panel write path must not report success when the server
    // refuses (bypassPermissions gated on availability).
    const p = gw.request('config.set', { key: 'permission_mode', value: 'bypassPermissions' })
    await replyToControl('set_permission_mode', { error: 'not available', ok: false })
    await expect(p).resolves.toEqual({ ok: false })
  })

  it('surfaces the server rejection when /mode bypassPermissions is unavailable', async () => {
    // The server gates bypassPermissions on availability (same guard as the
    // Shift+Tab cycle) — the client must show the refusal, not pretend the
    // mode changed.
    const p = gw.request('slash.exec', { command: 'mode bypassPermissions' })
    await replyToControl('set_permission_mode', {
      error: 'bypassPermissions is not available in this session',
      ok: false
    })
    const r: any = await p
    expect(r.type).toBe('exec')
    expect(r.output).toContain('not available')
    expect(r.output).not.toContain('Permission mode:')
  })

  it('dispatches an unknown slash as a backend workflow command (send)', async () => {
    const p = gw.request('slash.exec', { command: 'deep-research what is love' })
    await replyToControl('workflow_command', {
      notice: '⚡ launching workflow /deep-research',
      ok: true,
      prompt: 'Launch the dynamic workflow "deep-research" — args: what is love'
    })
    await expect(p).resolves.toEqual({
      message: 'Launch the dynamic workflow "deep-research" — args: what is love',
      notice: '⚡ launching workflow /deep-research',
      type: 'send'
    })
    const req = seen.find(f => f.request?.subtype === 'workflow_command')
    expect(req.request).toMatchObject({ args: 'what is love', name: 'deep-research' })
  })

  it('reports unknown commands as unwired when the backend does not own them', async () => {
    const p = gw.request('slash.exec', { command: 'frobnicate now' })
    await replyToControl('workflow_command', { error: "unknown workflow command 'frobnicate'", ok: false })
    await expect(p).resolves.toEqual({ output: "/frobnicate isn't wired into the clawcodex backend yet.", type: 'exec' })
  })

  it('merges backend workflow commands into slash completion', async () => {
    const p = gw.request<{ items: Array<{ text: string }> }>('complete.slash', { text: '/de' })
    await replyToControl('list_workflow_commands', {
      commands: [{ argument_hint: '<question>', description: 'Deep research', name: 'deep-research' }],
      ok: true
    })
    const r = await p
    expect(r.items.map(i => i.text)).toContain('/deep-research')
  })

  it('merges backend workflow commands into the command catalog after init', async () => {
    proc.line(INIT) // resolves readyPromise, which the catalog awaits
    const p = gw.request<{ canon: Record<string, string>; pairs: [string, string][] }>('commands.catalog', {})
    await replyToControl('list_workflow_commands', {
      commands: [{ description: 'Deep research', name: 'deep-research' }],
      ok: true
    })
    const r = await p
    expect(r.canon['/deep-research']).toBe('/deep-research')
    expect(r.pairs).toContainEqual(['/deep-research', 'Deep research'])
    // The static set is still present (workflow merge is additive).
    expect(r.canon['/workflows']).toBe('/workflows')
  })

  it('degrades the catalog to the static set when the workflow list is unavailable', async () => {
    proc.line(INIT)
    const p = gw.request<{ canon: Record<string, string>; pairs: [string, string][] }>('commands.catalog', {})
    await replyToControl('list_workflow_commands', { commands: [], ok: true })
    const r = await p
    expect(r.canon['/workflows']).toBe('/workflows')
    expect(r.pairs.some(([name]) => name === '/deep-research')).toBe(false)
  })

  it('renders a task_notification frame as a background.complete banner', async () => {
    proc.line({
      message: '✔ deep-research completed · 12 agents · 45.2k tok',
      session_id: 's1',
      subtype: 'task_notification',
      task_id: 'local_workflow_7',
      type: 'system'
    })
    await vi.waitFor(() => expect(last('background.complete')).toBeTruthy())
    expect(last('background.complete').payload).toEqual({
      task_id: 'local_workflow_7',
      text: '✔ deep-research completed · 12 agents · 45.2k tok'
    })
  })

  // ch13 round-4 — agent_progress → subagent.* (item 2)
  it('maps agent_progress to subagent.start + subagent.progress', async () => {
    proc.line({
      activity: 'reading src/', agent_id: 'a1', description: 'explore the repo',
      name: 'Explore', status: 'running', subagent_type: 'Explore',
      tokens: 120, tool_use_count: 2, type: 'agent_progress'
    })
    await vi.waitFor(() => expect(last('subagent.start')).toBeTruthy())
    expect(last('subagent.start').payload.subagent_id).toBe('a1')
    await vi.waitFor(() => expect(last('subagent.progress')).toBeTruthy())
    expect(last('subagent.progress').payload.text).toBe('reading src/')
  })

  it('emits subagent.start only once, then progress + complete', async () => {
    const base = { agent_id: 'a2', description: 'run tests', name: 'Test', subagent_type: 'general', type: 'agent_progress' }
    proc.line({ ...base, activity: 'running pytest', status: 'running' })
    await vi.waitFor(() => expect(last('subagent.progress')).toBeTruthy())
    proc.line({ ...base, activity: 'done', status: 'completed' })
    await vi.waitFor(() => expect(last('subagent.complete')).toBeTruthy())
    const starts = events.filter(e => e.type === 'subagent.start' && e.payload.subagent_id === 'a2')
    expect(starts.length).toBe(1)
    expect(last('subagent.complete').payload.status).toBe('completed')
  })

  // ch13 round-4 — permission "always allow" persistence (item 1)
  it('forwards a can_use_tool suggestion as a persistable approval option', async () => {
    proc.line({
      request: {
        input: { command: 'ls' }, subtype: 'can_use_tool', tool_name: 'Bash',
        suggestions: [{ type: 'addRules', destination: 'localSettings', behavior: 'allow', rules: [{ tool_name: 'Bash', rule_content: 'ls:*' }] }]
      },
      request_id: 'r1', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('approval.request')).toBeTruthy())
    const p = last('approval.request').payload
    expect(p.allow_permanent).toBe(true)
    // The box shows the ACTUAL command + carries the editable grant rule.
    expect(p.command).toBe('ls')
    expect(p.tool_name).toBe('Bash')
    expect(p.rule).toBe('ls:*')
    expect(p.rule_label).toBe('Bash(ls:*)')
  })

  it('sends chosen_updates when the user picks "always"; none for "once"', async () => {
    const sent: any[] = []

    ;(gw as any).send = (m: any) => sent.push(m)

    proc.line({
      request: {
        input: { command: 'ls' }, subtype: 'can_use_tool', tool_name: 'Bash',
        suggestions: [{ type: 'addRules', destination: 'localSettings', behavior: 'allow', rules: [{ tool_name: 'Bash', rule_content: 'ls:*' }] }]
      },
      request_id: 'r2', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('approval.request')).toBeTruthy())

    await gw.request('approval.respond', { choice: 'always' })
    const resp = sent.find(m => m.type === 'control_response')?.response?.response
    expect(resp.behavior).toBe('allow')
    expect(resp.chosen_updates).toHaveLength(1)
    expect(resp.chosen_updates[0].rules[0].rule_content).toBe('ls:*')
    expect(resp.chosen_updates[0].destination).toBe('localSettings')
  })

  it('persists the user-EDITED (widened) rule for "always"', async () => {
    // The box lets the user widen the suggested rule (git status:* → git:*);
    // the edited value is carried as `rule` and must become the persisted rule.
    const sent: any[] = []

    ;(gw as any).send = (m: any) => sent.push(m)
    proc.line({
      request: {
        input: { command: 'git status' }, subtype: 'can_use_tool', tool_name: 'Bash',
        suggestions: [{ type: 'addRules', destination: 'localSettings', behavior: 'allow', rules: [{ tool_name: 'Bash', rule_content: 'git status:*' }] }]
      },
      request_id: 'r3', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('approval.request')).toBeTruthy())
    await gw.request('approval.respond', { choice: 'always', rule: 'git:*' })
    const resp = sent.find(m => m.type === 'control_response')?.response?.response
    expect(resp.chosen_updates[0].rules[0].rule_content).toBe('git:*')
    expect(resp.chosen_updates[0].destination).toBe('localSettings')
  })

  it('offers "always" for a NON-Bash tool and passes its setMode suggestion through UNCHANGED', async () => {
    // Regression: Write/Edit send a session-scoped acceptEdits setMode (no
    // rules, no rule_content). The persist option must still be offered
    // (allow_permanent=true) and the suggestion must not be mangled into a
    // localSettings rule.
    const sent: any[] = []

    ;(gw as any).send = (m: any) => sent.push(m)
    const setModeSuggestion = { type: 'setMode', destination: 'session', mode: 'acceptEdits' }
    proc.line({
      request: {
        input: { file_path: '/a/b.ts' }, subtype: 'can_use_tool', tool_name: 'Write',
        session_label: 'allow all edits during this session',
        suggestions: [setModeSuggestion]
      },
      request_id: 'r4', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('approval.request')).toBeTruthy())
    // The box still offers a persistable option for non-Bash tools, with the
    // backend's authoritative per-tool wording (not "don't ask again for Write").
    expect(last('approval.request').payload.allow_permanent).toBe(true)
    expect(last('approval.request').payload.rule).toBeNull()
    expect(last('approval.request').payload.session_label).toBe('allow all edits during this session')

    await gw.request('approval.respond', { choice: 'always' })
    const resp = sent.find(m => m.type === 'control_response')?.response?.response
    // Suggestion passes through AS-IS: session scope kept, no rules injected.
    expect(resp.chosen_updates[0]).toEqual(setModeSuggestion)
    expect(resp.chosen_updates[0].destination).toBe('session')
    expect(resp.chosen_updates[0].rules).toBeUndefined()
  })

  it('compound-command suggestion (multiple rules): no editable rule, ALL rules sent on always', async () => {
    // R6 compound parity: a pipeline's suggestion bundles several rules in ONE
    // addRules update. The box must not offer per-rule editing (rule=null) and
    // accepting must persist the WHOLE bundle unchanged.
    const sent: any[] = []
    ;(gw as any).send = (m: any) => sent.push(m)
    const bundle = {
      type: 'addRules', destination: 'localSettings', behavior: 'allow',
      rules: [
        { tool_name: 'Bash', rule_content: 'grep:*' },
        { tool_name: 'Bash', rule_content: 'tr:*' },
        { tool_name: 'Bash', rule_content: 'sort -u' }
      ]
    }
    proc.line({
      request: {
        input: { command: "grep x f | tr a b | sort -u" }, subtype: 'can_use_tool', tool_name: 'Bash',
        suggestions: [bundle]
      },
      request_id: 'r5', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('approval.request')).toBeTruthy())
    const p = last('approval.request').payload
    expect(p.allow_permanent).toBe(true)
    expect(p.rule).toBeNull() // multi-rule → not editable
    expect(p.rule_label).toBe('Bash(grep:*), Bash(tr:*), Bash(sort -u)')

    await gw.request('approval.respond', { choice: 'always' })
    const resp = sent.find(m => m.type === 'control_response')?.response?.response
    expect(resp.chosen_updates[0]).toEqual(bundle) // whole bundle, untouched
  })
})

describe('approvalCommandText — the human-reviewable action, not a JSON dump', () => {
  it('shows the Bash command / file path / url, not the whole input blob', () => {
    expect(approvalCommandText({ command: 'git status --short' })).toBe('git status --short')
    expect(approvalCommandText({ description: 'x', file_path: '/a/b.ts' })).toBe('/a/b.ts')
    expect(approvalCommandText({ url: 'https://example.com' })).toBe('https://example.com')
  })

  it('falls back to compact JSON for inputs with no obvious action field', () => {
    expect(approvalCommandText({ foo: 1 })).toBe('{"foo":1}')
  })
})
