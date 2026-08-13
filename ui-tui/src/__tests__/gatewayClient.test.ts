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

  it('passes the session totals rider through on result (cost + session_turns)', async () => {
    proc.line({
      cost: { total_cost_usd: 0.0048 },
      result: 'done',
      session_turns: 3,
      subtype: 'success',
      type: 'result'
    })
    await vi.waitFor(() => expect(last('message.complete')).toBeTruthy())
    expect(last('message.complete').payload).toMatchObject({
      cost: { total_cost_usd: 0.0048 },
      session_turns: 3,
      text: 'done'
    })
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

  // WebSearch renders as the original's one-liner (WebSearchTool/UI.tsx);
  // the agent-server forwards searchCount/durationSeconds on tool_use_result
  // and the raw blob stays reachable behind ctrl+o via result_raw.
  it('collapses a WebSearch result to "Did N searches in Xs" from the envelope', async () => {
    const blob = 'Web search results for query: "q"\n\n**A** -- snippet (https://a.example)\n\nLinks: [{"title": "A", "url": "https://a.example"}]'

    proc.line(toolUse('t1', 'WebSearch', { query: 'q' }))
    await vi.waitFor(() => expect(last('tool.start')).toBeTruthy())
    proc.line({
      ...toolResult('t1', blob),
      tool_use_result: { durationSeconds: 2.4, searchCount: 1, type: 'web_search' }
    })
    await vi.waitFor(() => expect(last('tool.complete')).toBeTruthy())

    const p = last('tool.complete').payload
    expect(p.result_text).toBe('Did 1 search in 2s')
    expect(p.result_raw).toContain('Links:')
  })

  it('falls back to a durationless WebSearch summary without the envelope', async () => {
    const blob = 'Web search results for query: "q"\n\nLinks: [{"title": "A", "url": "https://a.example"}]'
    const p = await runTool('t1', 'WebSearch', { query: 'q' }, blob)
    expect(p.result_text).toBe('Did 1 search')
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

  it('forwards a review_summary system frame as a review.summary event', async () => {
    proc.line(INIT)
    await vi.waitFor(() => expect(last('gateway.ready')).toBeTruthy())
    proc.line({
      message: '💾 Self-improvement review: Memory updated',
      session_id: 's1',
      subtype: 'review_summary',
      type: 'system'
    })
    await vi.waitFor(() => expect(last('review.summary')).toBeTruthy())
    expect(last('review.summary').payload).toEqual({ text: '💾 Self-improvement review: Memory updated' })
  })

  it('maps arg-ful /memory to the memory_manage control and prints its text', async () => {
    const p = gw.request('slash.exec', { command: 'memory status' })
    await replyToControl('memory_manage', { ok: true, text: 'Memory (MEMORY.md): 2 entries' })
    await expect(p).resolves.toEqual({ output: 'Memory (MEMORY.md): 2 entries', type: 'exec' })
  })

  it('publishes a session.stats event from the session.clear reply rider', async () => {
    const p = gw.request('session.clear', {})
    await replyToControl('clear', { cost: { total_cost_usd: 0.5 }, count: 0, ok: true, session_turns: 0 })
    await expect(p).resolves.toEqual({ ok: true })
    expect(last('session.stats').payload).toMatchObject({ cost: { total_cost_usd: 0.5 }, session_turns: 0 })
  })

  it('stays silent on a session.clear reply without the rider (old backend)', async () => {
    const p = gw.request('session.clear', {})
    await replyToControl('clear', { count: 0, ok: true })
    await expect(p).resolves.toEqual({ ok: true })
    expect(last('session.stats')).toBeUndefined()
  })

  // `/mode` is gone — `/permissions` replaced it as a LOCAL slash command
  // (app/slash/commands/session.ts), so dispatchSlash no longer has a case for
  // either name. What remains on the gateway is the RPC that command uses.

  it('reflects the server rejection through config.set permission_mode', async () => {
    // The write path must not report success when the server refuses (Full
    // Access is gated on selectability), and must hand back the REASON so
    // /permissions can print it rather than a generic failure.
    const p = gw.request('config.set', { key: 'permission_mode', value: 'bypassPermissions' })
    await replyToControl('set_permission_mode', { error: 'not available', ok: false })
    await expect(p).resolves.toMatchObject({ error: 'not available', ok: false })
  })

  it('returns the applied mode from config.set permission_mode', async () => {
    // /permissions badges `mode` from the reply, not the arg it sent.
    const p = gw.request('config.set', { key: 'permission_mode', value: 'acceptEdits' })
    await replyToControl('set_permission_mode', { mode: 'acceptEdits', ok: true, persisted: true })
    await expect(p).resolves.toMatchObject({ mode: 'acceptEdits', ok: true, persisted: true })
  })

  it('forwards the persist flag on config.set permission_mode', async () => {
    // persist is what makes a deliberate down-shift to "Ask for approval"
    // survive relaunch; it must reach the control request, not be dropped.
    void gw.request('config.set', { key: 'permission_mode', persist: true, value: 'default' })
    await vi.waitFor(() => {
      const f = stdinFrames().find(x => x.request?.subtype === 'set_permission_mode')

      expect(f?.request?.persist).toBe(true)
    })
  })

  it('routes config.set logoColor to the set_logo_color control and echoes the value', async () => {
    // /logo persistence: the round-trip matters — a not-ready backend must
    // surface as ok:false (the command prints "this session only"), never a
    // silent false success.
    const p = gw.request('config.set', { key: 'logoColor', value: 'forest' })
    await replyToControl('set_logo_color', { logo_color: 'forest', ok: true })
    await expect(p).resolves.toEqual({ ok: true, value: 'forest' })
  })

  it('reflects a set_logo_color rejection as ok:false', async () => {
    const p = gw.request('config.set', { key: 'logoColor', value: 'lava' })
    await replyToControl('set_logo_color', { error: 'invalid palette', ok: false })
    await expect(p).resolves.toEqual({ ok: false })
  })

  // ── config.set model (the /model picker + typed /model) ───────────────────

  it('parses the picker model grammar and answers with the switched value', async () => {
    // The picker emits "<model> --provider <slug> [--global|--tui-session]";
    // the gateway owns parsing it (hermes contract). The flags must never
    // reach the backend as part of the model id.
    const p = gw.request('config.set', { key: 'model', value: 'deepseek-v4-pro --provider deepseek --global' })
    await replyToControl('set_model', { model: 'deepseek-v4-pro', ok: true })
    await expect(p).resolves.toEqual({ value: 'deepseek-v4-pro' })

    const req = seen.find(f => f.request?.subtype === 'set_model')!.request
    expect(req.model).toBe('deepseek-v4-pro')
    expect(req.provider).toBe('deepseek')
  })

  it('sends a bare typed /model value without a provider param', async () => {
    const p = gw.request('config.set', { key: 'model', value: 'x-model --tui-session' })
    await replyToControl('set_model', { model: 'x-model', ok: true })
    await expect(p).resolves.toEqual({ value: 'x-model' })

    const req = seen.find(f => f.request?.subtype === 'set_model')!.request
    expect(req.model).toBe('x-model')
    expect('provider' in req).toBe(false)
  })

  it('passes the backend model-switch warning through to the caller', async () => {
    const p = gw.request('config.set', { key: 'model', value: 'mystery-model' })
    await replyToControl('set_model', {
      model: 'mystery-model',
      ok: true,
      warning: "'mystery-model' is not in deepseek's model list — the API may reject it"
    })
    await expect(p).resolves.toEqual({
      value: 'mystery-model',
      warning: "'mystery-model' is not in deepseek's model list — the API may reject it"
    })
  })

  it('falls back to the requested model when an older backend acks without echoing it', async () => {
    const p = gw.request('config.set', { key: 'model', value: 'x-model' })
    await replyToControl('set_model', { ok: true })
    // No `provider` key at all: an absent provider means "unchanged", and
    // synthesizing one here would let a caller overwrite a correct label.
    await expect(p).resolves.toEqual({ value: 'x-model' })
  })

  it('passes the provider the backend echoes through to the caller', async () => {
    const p = gw.request('config.set', { key: 'model', value: 'deepseek-v4-pro' })
    await replyToControl('set_model', { model: 'deepseek-v4-pro', ok: true, provider: 'deepseek' })
    await expect(p).resolves.toEqual({ provider: 'deepseek', value: 'deepseek-v4-pro' })
  })

  // ── step 3: the effort ladder for the model being selected ───────────────

  it('reports the effort ladder for the model the picker asks about', async () => {
    const p = gw.request('model.effort_options', { model: 'claude-opus-5', provider: 'anthropic' })
    await replyToControl('effort_options', {
      current: 'xhigh',
      levels: ['low', 'medium', 'high', 'xhigh', 'max'],
      ok: true,
      supported: true
    })

    await expect(p).resolves.toEqual({
      current: 'xhigh',
      levels: ['low', 'medium', 'high', 'xhigh', 'max'],
      supported: true
    })

    const req = seen.find(f => f.request?.subtype === 'effort_options')!.request
    expect(req.model).toBe('claude-opus-5')
    expect(req.provider).toBe('anthropic')
  })

  it('treats an errored effort lookup as "no ladder" rather than failing the switch', async () => {
    // The model is already chosen by the time this runs, so a refusal must
    // degrade to the two-step flow, not reject and strand the picker.
    const p = gw.request('model.effort_options', { model: 'm', provider: 'p' })
    await replyToControl('effort_options', { error: 'boom', ok: false })

    await expect(p).resolves.toEqual({ levels: [], supported: false })
  })

  it('drops non-string levels from a malformed reply', async () => {
    const p = gw.request('model.effort_options', { model: 'm', provider: 'p' })
    await replyToControl('effort_options', { levels: ['low', 3, null, 'max'], ok: true, supported: true })

    await expect(p).resolves.toMatchObject({ levels: ['low', 'max'] })
  })

  it('rejects with the backend error when the model switch is refused', async () => {
    const p = gw.request('config.set', { key: 'model', value: 'm --provider other' })
    await replyToControl('set_model', {
      error: "model 'm' expects provider 'other' but this session is on 'deepseek'",
      ok: false
    })
    await expect(p).rejects.toThrow("model 'm' expects provider 'other' but this session is on 'deepseek'")
  })

  // ── cross-provider selection (the picker's step 1 + step 2) ───────────────
  // `set_model` refuses to point the live provider at a foreign model id, so a
  // picker selection from another provider has to go through `set_provider`
  // first. A replier that can answer the SECOND set_model frame is required —
  // replyToControl always matches the first, which is already resolved.

  const makeReplier = () => {
    const answered = new Set<string>()

    return async (subtype: string, response: unknown) => {
      let req: any
      await vi.waitFor(() => {
        seen.push(...stdinFrames())
        req = seen.find(
          f => f.type === 'control_request' && f.request?.subtype === subtype && !answered.has(f.request_id)
        )
        expect(req).toBeTruthy()
      })
      answered.add(req.request_id)
      proc.line({ response: { request_id: req.request_id, response }, type: 'control_response' })
    }
  }

  it('switches provider then re-applies the model on provider_mismatch', async () => {
    const reply = makeReplier()
    const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai --tui-session' })

    await reply('set_model', {
      error: "model 'gpt-5.4' expects provider 'openai' but this session is on 'anthropic'",
      ok: false,
      provider: 'anthropic',
      provider_mismatch: true
    })
    await reply('set_provider', { model: 'gpt-5.4', ok: true, provider: 'openai' })
    // No `provider` in the retry's reply — an older backend. The switch still
    // has to report where the session landed, or the caller leaves the stats
    // line reading `anthropic · gpt-5.4`.
    await reply('set_model', { model: 'gpt-5.4', ok: true })

    await expect(p).resolves.toEqual({ provider: 'openai', value: 'gpt-5.4' })

    const switched = seen.find(f => f.request?.subtype === 'set_provider')!.request
    expect(switched.provider).toBe('openai')
    // Two set_model frames: the probe that got refused, then the retry.
    expect(seen.filter(f => f.request?.subtype === 'set_model')).toHaveLength(2)
  })

  it('prefers the provider the retry itself reports over the requested one', async () => {
    const reply = makeReplier()
    const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai' })

    await reply('set_model', { ok: false, provider: 'anthropic', provider_mismatch: true })
    await reply('set_provider', { model: 'gpt-5.4', ok: true, provider: 'openai' })
    // The backend canonicalizes the slug it was sent; its answer wins so the
    // label matches what the session is actually on.
    await reply('set_model', { model: 'gpt-5.4', ok: true, provider: 'openai-compat' })

    await expect(p).resolves.toEqual({ provider: 'openai-compat', value: 'gpt-5.4' })
  })

  it('surfaces a failed provider switch instead of the model error', async () => {
    const reply = makeReplier()
    const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai' })

    await reply('set_model', { error: 'wrong provider', ok: false, provider_mismatch: true })
    await reply('set_provider', { error: "provider 'openai' is not configured (no API key)", ok: false })

    await expect(p).rejects.toThrow("provider 'openai' is not configured (no API key)")
  })

  it('does not retry forever when the switch does not take', async () => {
    const reply = makeReplier()
    const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai' })

    await reply('set_model', { error: 'wrong provider', ok: false, provider_mismatch: true })
    await reply('set_provider', { ok: true, provider: 'openai' })
    await reply('set_model', { error: 'still wrong provider', ok: false, provider_mismatch: true })

    await expect(p).rejects.toThrow('still wrong provider')
    expect(seen.filter(f => f.request?.subtype === 'set_provider')).toHaveLength(1)
  })

  // ── model.options / save_key / disconnect ─────────────────────────────────

  it('lists every provider from the catalog control', async () => {
    const p = gw.request('model.options', {})
    await replyToControl('list_model_providers', {
      model: 'claude-opus-5',
      ok: true,
      provider: 'anthropic',
      providers: [
        { authenticated: true, is_current: true, models: ['claude-opus-5'], name: 'Anthropic Claude', slug: 'anthropic' },
        { authenticated: false, key_env: 'TOGETHER_API_KEY', models: [], name: 'Together AI', slug: 'together' }
      ]
    })

    const r: any = await p
    expect(r.providers).toHaveLength(2)
    expect(r.providers.map((x: any) => x.slug)).toEqual(['anthropic', 'together'])
    expect(r.model).toBe('claude-opus-5')
  })

  it('surfaces a catalog refusal instead of inventing a fake provider row', async () => {
    // The `init_error` short-circuit answers every control with {ok:false} —
    // and it fires exactly when no provider is configured. Falling back to the
    // get_settings synthesis there would render one row literally named
    // `clawcodex` (the `?? 'clawcodex'` default), reproducing the original
    // one-row symptom on top of a provider that does not exist.
    const p = gw.request('model.options', {})
    await replyToControl('list_model_providers', {
      error: "API key for provider 'anthropic' is not configured. Run `clawcodex login` to set it up.",
      ok: false
    })

    await expect(p).rejects.toThrow('Run `clawcodex login`')
    expect(seen.find(f => f.request?.subtype === 'get_settings')).toBeUndefined()
  })

  it('falls back to the active provider only when the backend never answers', async () => {
    // A null reply means an RPC timeout or a backend too old to know the
    // control — the one case where synthesizing from get_settings is right.
    vi.useFakeTimers()

    try {
      const p = gw.request('model.options', {})
      await vi.waitFor(() => {
        seen.push(...stdinFrames())
        expect(seen.find(f => f.request?.subtype === 'list_model_providers')).toBeTruthy()
      })
      await vi.advanceTimersByTimeAsync(5_100) // past RPC_TIMEOUT_MS
      await vi.waitFor(() => {
        seen.push(...stdinFrames())
        expect(seen.find(f => f.request?.subtype === 'get_settings')).toBeTruthy()
      })
      const req = seen.find(f => f.request?.subtype === 'get_settings')!
      proc.line({
        response: {
          request_id: req.request_id,
          response: { available_models: ['a', 'b'], model: 'a', provider: 'deepseek' }
        },
        type: 'control_response'
      })

      const r: any = await p
      expect(r.providers).toHaveLength(1)
      expect(r.providers[0]).toMatchObject({ is_current: true, slug: 'deepseek', total_models: 2 })
    } finally {
      vi.useRealTimers()
    }
  })

  it('rolls back the provider when the model cannot be selected after switching', async () => {
    // set_provider has already committed backend-side (registry rebuilt,
    // model reset to the new provider's default, pairing persisted), so a
    // bare "model switch failed" would read as "nothing happened".
    const reply = makeReplier()
    const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai' })

    await reply('set_model', { ok: false, provider: 'anthropic', provider_mismatch: true })
    await reply('set_provider', { ok: true, provider: 'openai' })
    await reply('set_model', { error: 'model switch failed: bad id', ok: false })
    await reply('set_provider', { ok: true, provider: 'anthropic' })

    await expect(p).rejects.toThrow("rolled back to 'anthropic'")

    const switches = seen.filter(f => f.request?.subtype === 'set_provider')
    expect(switches).toHaveLength(2)
    expect(switches[1]!.request.provider).toBe('anthropic')
  })

  it('does not roll back when the retry only timed out', async () => {
    // A silent backend may well have applied the model; rolling back there
    // would discard a switch that actually worked.
    vi.useFakeTimers()
    try {
      const reply = makeReplier()
      const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai' })
      // Attach the rejection handler BEFORE advancing timers: the rejection
      // fires inside advanceTimersByTimeAsync, and an assertion added after
      // that leaves a window Node reports as an unhandled rejection.
      const rejects = expect(p).rejects.toThrow('may be on')

      await reply('set_model', { ok: false, provider: 'anthropic', provider_mismatch: true })
      await reply('set_provider', { ok: true, provider: 'openai' })
      await vi.advanceTimersByTimeAsync(5_100) // the retried set_model never answers

      await rejects
      expect(seen.filter(f => f.request?.subtype === 'set_provider')).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('says the session moved when the rollback itself fails', async () => {
    const reply = makeReplier()
    const p = gw.request('config.set', { key: 'model', value: 'gpt-5.4 --provider openai' })

    await reply('set_model', { ok: false, provider: 'anthropic', provider_mismatch: true })
    await reply('set_provider', { ok: true, provider: 'openai' })
    await reply('set_model', { error: 'model switch failed: bad id', ok: false })
    await reply('set_provider', { error: 'anthropic is not configured', ok: false })

    await expect(p).rejects.toThrow("session is now on 'openai'")
  })

  it('saves an api key through the save_provider_key control', async () => {
    const p = gw.request('model.save_key', { api_key: 'sk-tog-1', slug: 'together' })
    await replyToControl('save_provider_key', {
      ok: true,
      provider: { authenticated: true, models: ['m1'], name: 'Together AI', slug: 'together' }
    })

    const r: any = await p
    expect(r.provider).toMatchObject({ authenticated: true, slug: 'together' })

    const req = seen.find(f => f.request?.subtype === 'save_provider_key')!.request
    expect(req).toMatchObject({ api_key: 'sk-tog-1', slug: 'together' })
  })

  it('surfaces a refused disconnect rather than reporting success', async () => {
    const p = gw.request('model.disconnect', { slug: 'anthropic' })
    await replyToControl('disconnect_provider', {
      disconnected: false,
      error: "'anthropic' is the active provider — switch to another provider before disconnecting it",
      ok: false
    })

    await expect(p).rejects.toThrow('is the active provider')
  })

  it('surfaces a partial disconnect whose key survives in the shell', async () => {
    const p = gw.request('model.disconnect', { slug: 'together' })
    await replyToControl('disconnect_provider', {
      disconnected: false,
      error: "'together' still authenticates — its key is set in the process environment, which only your shell can unset",
      ok: true,
      still_authenticated: true
    })

    await expect(p).rejects.toThrow('only your shell can unset')
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
    // Not a workflow → the client falls back to the skill resolver before
    // giving up; only when that also misses does the unwired line show.
    await replyToControl('skill_command', { error: "unknown skill 'frobnicate'", ok: false })
    await expect(p).resolves.toEqual({ output: "/frobnicate isn't wired into the clawcodex backend yet.", type: 'exec' })
  })

  it('dispatches /loop through the backend skill resolver as a skill payload', async () => {
    const p = gw.request('slash.exec', { command: 'loop 5m check the deploy' })
    await replyToControl('skill_command', {
      name: 'loop',
      ok: true,
      prompt: '# /loop — fixed recurring interval\nRequested interval: 5m'
    })
    await expect(p).resolves.toEqual({
      message: '# /loop — fixed recurring interval\nRequested interval: 5m',
      name: 'loop',
      type: 'skill'
    })
    const req = seen.find(f => f.request?.subtype === 'skill_command')
    expect(req.request).toMatchObject({ args: '5m check the deploy', name: 'loop' })
  })

  it('falls back from workflows to skills for typed skill commands', async () => {
    const p = gw.request('slash.exec', { command: 'my-skill do things' })
    await replyToControl('workflow_command', { error: "unknown workflow command 'my-skill'", ok: false })
    await replyToControl('skill_command', { name: 'my-skill', ok: true, prompt: 'skill body here' })
    await expect(p).resolves.toEqual({ message: 'skill body here', name: 'my-skill', type: 'skill' })
  })

  it('lists /loop in the slash-completion menu', async () => {
    const p = gw.request<{ items: Array<{ text: string }> }>('complete.slash', { text: '/lo' })
    await replyToControl('list_workflow_commands', { commands: [], ok: true })
    const r = await p
    expect(r.items.map(i => i.text)).toContain('/loop')
  })

  it('maps cron_status system envelopes to a cron transcript line and a cron.state snapshot', async () => {
    proc.line({
      message: '⏰ Scheduled task ab12cd34 fired (every 5 minutes).',
      scheduled: { jobs: [{ cron: '*/5 * * * *', id: 'ab12cd34', next_fire_at: 1_900_000_000 }], wakeup: null },
      session_id: 'sess',
      subtype: 'cron_status',
      type: 'system'
    })
    await vi.waitFor(() => expect(last('cron.state')).toBeTruthy())
    const line = last('status.update')
    expect(line?.payload).toEqual({ kind: 'cron', text: '⏰ Scheduled task ab12cd34 fired (every 5 minutes).' })
    expect(last('cron.state')?.payload?.scheduled?.jobs?.[0]?.id).toBe('ab12cd34')
  })

  it('publishes a message-less cron_status as a snapshot-only cron.state event', async () => {
    proc.line({
      message: '',
      scheduled: { jobs: [], wakeup: { fire_at: 1_900_000_123, is_fallback: false, reason: 'watching CI' } },
      session_id: 'sess',
      subtype: 'cron_status',
      type: 'system'
    })
    await vi.waitFor(() => expect(last('cron.state')).toBeTruthy())
    expect(events.some(e => e.type === 'status.update')).toBe(false)
    expect(last('cron.state')?.payload?.scheduled?.wakeup?.reason).toBe('watching CI')
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

  it('lists /exit in the slash-completion menu (user-reported: /exit executed but never showed as a command)', async () => {
    const p = gw.request<{ items: Array<{ text: string }> }>('complete.slash', { text: '/ex' })
    await replyToControl('list_workflow_commands', { commands: [], ok: true })
    const r = await p
    expect(r.items.map(i => i.text)).toContain('/exit')
  })

  it('lists /skills in the slash-completion menu (user-reported: /skills missing)', async () => {
    const p = gw.request<{ items: Array<{ text: string }> }>('complete.slash', { text: '/sk' })
    await replyToControl('list_workflow_commands', { commands: [], ok: true })
    const r = await p
    expect(r.items.map(i => i.text)).toContain('/skills')
  })

  it('carries argument hints on completion items (user-reported: no value suggestions)', async () => {
    const p = gw.request<{ items: Array<{ hint?: string; text: string }> }>('complete.slash', { text: '/ef' })
    await replyToControl('list_workflow_commands', { commands: [], ok: true })
    const r = await p
    const effort = r.items.find(i => i.text === '/effort')
    // Must track the backend ladder (VALID_EFFORT_VALUES): xhigh/max are the
    // levels Claude Opus 5 wants, and `minimal` is a GPT-5 level the backend
    // rejects — advertising it sent users at a guaranteed error.
    expect(effort?.hint).toBe('[low|medium|high|xhigh|max|auto|ultracode]')
  })

  it('passes workflow argument_hint through to completion items', async () => {
    const p = gw.request<{ items: Array<{ hint?: string; text: string }> }>('complete.slash', { text: '/de' })
    await replyToControl('list_workflow_commands', {
      commands: [{ argument_hint: '<question>', description: 'Deep research', name: 'deep-research' }],
      ok: true
    })
    const r = await p
    expect(r.items.find(i => i.text === '/deep-research')?.hint).toBe('<question>')
  })

  it('exposes argument hints in the command catalog (ghost-text lookup source)', async () => {
    proc.line(INIT)
    const p = gw.request<{ hints: Record<string, string> }>('commands.catalog', {})
    await replyToControl('list_workflow_commands', {
      commands: [{ argument_hint: '<question>', description: 'Deep research', name: 'deep-research' }],
      ok: true
    })
    const r = await p
    expect(r.hints['/deep-research']).toBe('<question>')
    // Names shadowed by TUI-local commands carry no gateway hint — the local
    // registry's argumentHint is the truthful one (dispatch order).
    expect(r.hints['/compact']).toBeUndefined()
    expect(r.hints['/model']).toBeUndefined()
    expect(r.hints['/permissions']).toBeUndefined()
  })

  it('skills.manage list groups backend skills by category', async () => {
    const p = gw.request('skills.manage', { action: 'list' })
    await replyToControl('list_skills', {
      skills: [
        { category: 'bundled', description: 'Deep research', name: 'deep-research', path: '/b/dr' },
        { category: 'user', description: 'Ship it', name: 'ship', path: '/u/ship' },
        { category: 'user', description: 'QA a web app', name: 'qa', path: '/u/qa' }
      ],
      total: 3
    })
    await expect(p).resolves.toEqual({
      skills: { bundled: ['deep-research'], user: ['qa', 'ship'] },
      total: 3
    })
  })

  it('skills.manage inspect matches case-insensitively and rides the TTL cache (one list_skills per burst)', async () => {
    const p = gw.request('skills.manage', { action: 'list' })
    await replyToControl('list_skills', {
      skills: [{ category: 'user', description: 'QA a web app', name: 'qa', path: '/u/qa' }],
      total: 1
    })
    await p

    // Within the TTL the inspect is served from the cached list — no second
    // control round-trip (the hub inspects per selection).
    const r = await gw.request<{ info?: { name?: string; path?: string } }>('skills.manage', {
      action: 'inspect',
      query: 'QA'
    })

    expect(r.info).toMatchObject({ name: 'qa', path: '/u/qa' })
    expect(stdinFrames().filter(f => f.request?.subtype === 'list_skills')).toHaveLength(0)
  })

  it('skills.manage install/browse reject as unsupported instead of faking success', async () => {
    await expect(gw.request('skills.manage', { action: 'install', query: 'foo' })).rejects.toThrow(/not supported/)
    await expect(gw.request('skills.manage', { action: 'browse', page: 1 })).rejects.toThrow(/not supported/)
  })

  it('skills.reload busts the cache, re-scans, and reports the count', async () => {
    const p = gw.request<{ output?: string }>('skills.reload', {})
    await replyToControl('list_skills', { skills: [{ category: 'user', name: 'qa' }], total: 41 })
    const r = await p
    expect(r.output).toContain('41')
  })

  it('routes /skills <unknown-sub> to a usage hint, not the workflow fallback', async () => {
    await expect(gw.request('slash.exec', { command: 'skills frobnicate' })).resolves.toMatchObject({
      output: expect.stringContaining('usage: /skills'),
      type: 'exec'
    })
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
      model: 'claude-haiku-4-5', name: 'Explore', status: 'running',
      subagent_type: 'Explore', tokens: 120, tool_use_count: 2,
      type: 'agent_progress'
    })
    await vi.waitFor(() => expect(last('subagent.start')).toBeTruthy())
    expect(last('subagent.start').payload.subagent_id).toBe('a1')
    // The routed model must survive the bridge — the agents overlay falls
    // back to 'inherit' without it, which the per-provider subagent
    // defaults make actively wrong.
    expect(last('subagent.start').payload.model).toBe('claude-haiku-4-5')
    await vi.waitFor(() => expect(last('subagent.progress')).toBeTruthy())
    expect(last('subagent.progress').payload.text).toBe('reading src/')
    expect(last('subagent.progress').payload.model).toBe('claude-haiku-4-5')
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

  // ── AskUserQuestion ────────────────────────────────────────────────────────
  //
  // Deliberately NOT the permission lane: the questions ARE the gate, and a
  // PermissionAskReply has nowhere to carry structured answers. These pin the
  // wire contract in both directions.

  it('forks ask_user_question into a question.request instead of the approval box', async () => {
    const questions = [
      { question: 'Which posts?', header: 'Scope', options: [{ label: 'Two' }, { label: 'All' }] }
    ]

    proc.line({ request: { questions, subtype: 'ask_user_question' }, request_id: 'q1', type: 'control_request' })
    await vi.waitFor(() => expect(last('question.request')).toBeTruthy())

    expect(last('question.request').payload.questions).toEqual(questions)
    // The generic approval box must NOT also fire — that would stack a
    // redundant "Answer questions?" prompt on top of the questions.
    expect(last('approval.request')).toBeFalsy()
  })

  it('tolerates a malformed questions payload rather than throwing', async () => {
    proc.line({ request: { questions: 'nope', subtype: 'ask_user_question' }, request_id: 'q1', type: 'control_request' })
    await vi.waitFor(() => expect(last('question.request')).toBeTruthy())
    expect(last('question.request').payload.questions).toEqual([])
  })

  it('replies to question.respond with action:submit and the answers map', async () => {
    const sent: any[] = []

    ;(gw as any).send = (m: any) => sent.push(m)

    proc.line({
      request: { questions: [{ question: 'Which posts?' }], subtype: 'ask_user_question' },
      request_id: 'q7', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('question.request')).toBeTruthy())

    await gw.request('question.respond', { answers: { 'Which posts?': 'Two' } })

    const resp = sent.find(m => m.type === 'control_response')
    expect(resp.response.request_id).toBe('q7')
    expect(resp.response.response).toEqual({ action: 'submit', answers: { 'Which posts?': 'Two' } })
  })

  it('replies to a dismissed dialog with action:cancel, not an empty submit', async () => {
    // The backend maps cancel to a DECLINE; an empty submit is a different
    // thing (the review step lets you submit with nothing filled in), so
    // collapsing the two would tell the model the user answered with nothing.
    const sent: any[] = []

    ;(gw as any).send = (m: any) => sent.push(m)

    proc.line({
      request: { questions: [{ question: 'Which posts?' }], subtype: 'ask_user_question' },
      request_id: 'q8', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('question.request')).toBeTruthy())

    await gw.request('question.respond', { answers: null })

    const resp = sent.find(m => m.type === 'control_response')
    expect(resp.response.request_id).toBe('q8')
    expect(resp.response.response).toEqual({ action: 'cancel' })
  })

  it('reports failure when no question is pending, instead of a false success', async () => {
    // The round trip can already be over (server-side timeout, or a second
    // reply racing the first). Returning ok here made the app stamp
    // "questions answered" on a turn whose answers went nowhere.
    const r = await gw.request<{ ok?: boolean }>('question.respond', { answers: { Q: 'A' } })

    expect(r.ok).toBe(false)
  })

  it('sends nothing on a second question.respond (single-flight slot)', async () => {
    // turnController.idle() also declines a still-open dialog on teardown, so
    // a real answer and the safety-net decline can both fire. The slot is
    // cleared before the send, making the second a no-op rather than a
    // duplicate control_response that would resolve someone else's request.
    const sent: any[] = []

    ;(gw as any).send = (m: any) => sent.push(m)

    proc.line({
      request: { questions: [{ question: 'Q' }], subtype: 'ask_user_question' },
      request_id: 'q9', type: 'control_request'
    })
    await vi.waitFor(() => expect(last('question.request')).toBeTruthy())

    await gw.request('question.respond', { answers: { Q: 'A' } })
    await gw.request('question.respond', { answers: null })

    expect(sent.filter(m => m.type === 'control_response')).toHaveLength(1)
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

  // ── image attachment ──────────────────────────────────────────────────────
  // These three RPCs were called by the composer and /image from the start but
  // had no case here, so they fell to `default` and resolved `{}`. The composer
  // read that as "not an image" and pasted the path as literal text; Ctrl+V with
  // a screenshot did nothing at all.

  it('maps image.attach to the attach_image control and returns its metadata', async () => {
    const p = gw.request('image.attach', { path: '/tmp/shot.png' })
    await replyToControl('attach_image', {
      height: 914, name: 'shot.png', token_estimate: 976, width: 1568
    })
    await expect(p).resolves.toEqual({
      height: 914, name: 'shot.png', token_estimate: 976, width: 1568
    })
  })

  it('forwards the pasted path verbatim so the backend can unescape it', async () => {
    // Shell escapes and file:// percent-encoding are the backend's job; mangling
    // the text here would make a dragged screenshot miss on disk.
    const raw = 'file:///var/T/Screenshot%202026-04-21%20at%201.04.43%20PM.png'

    void gw.request('image.attach', { path: raw })
    await vi.waitFor(() => {
      seen.push(...stdinFrames())
      const req = seen.find(f => f.request?.subtype === 'attach_image')
      expect(req?.request?.path).toBe(raw)
    })
  })

  it('maps image.clipboard to the clipboard_image control', async () => {
    const p = gw.request('image.clipboard', {})
    await replyToControl('clipboard_image', {
      height: 220, name: 'clipboard image', token_estimate: 300, width: 760
    })
    await expect(p).resolves.toMatchObject({ name: 'clipboard image' })
  })

  it('resolves image.clipboard to {} when the clipboard holds no image', async () => {
    // The composer needs a falsy `name` here to fall back to a text paste.
    const p = gw.request('image.clipboard', {})
    await replyToControl('clipboard_image', {})
    await expect(p).resolves.toEqual({})
  })

  it('maps input.detect_drop to the detect_file_drop control', async () => {
    const p = gw.request('input.detect_drop', { text: '/tmp/data.csv' })
    await replyToControl('detect_file_drop', {
      is_image: false, matched: true, name: 'data.csv', text: '@/tmp/data.csv'
    })
    await expect(p).resolves.toMatchObject({
      is_image: false, matched: true, text: '@/tmp/data.csv'
    })
  })

  // The macOS Cmd+V route. Apple Terminal/iTerm handle Cmd+V themselves and
  // deliver the clipboard as a bracketed paste, so with an image on the
  // clipboard an EMPTY paste arrives and lands in the composer's
  // empty-bracketed branch → onClipboardPaste → this RPC. Unwired it resolved
  // {} and Cmd+V did nothing, silently (the call passes quiet=true).
  it('maps clipboard.paste to the clipboard_image control', async () => {
    const p = gw.request('clipboard.paste', { session_id: 's1' })
    await replyToControl('clipboard_image', {
      attached: true, count: 3, height: 220, name: 'clipboard image', width: 760
    })
    await expect(p).resolves.toMatchObject({ attached: true, count: 3 })
  })

  it('gives clipboard.paste a message when the clipboard has no image', async () => {
    // The caller shows this only on an explicit paste (quiet=false).
    const p = gw.request('clipboard.paste', {})
    await replyToControl('clipboard_image', {})
    await expect(p).resolves.toMatchObject({ message: 'No image found in clipboard' })
  })

  it('does not overwrite a real failure with the not-found message', async () => {
    const p = gw.request('clipboard.paste', {})
    await replyToControl('clipboard_image', { unavailable: true })
    const r = (await p) as { message?: string; unavailable?: boolean }
    expect(r.unavailable).toBe(true)
  })

  // `placeholder` says "this caller renders an `[Image #N]` chip", which makes the
  // chip authoritative — the backend DROPS a pending image whose chip is gone at
  // submit. It must therefore come from the CALLER, never be hardcoded per-RPC:
  // six sites reach these RPCs and only three insert a chip. Hardcoding `true`
  // made `/image`, the startup image, and typed-path submit silently lose their
  // images *after* their UI had confirmed success. Default false is fail-open.
  it('forwards placeholder:true from a chip-rendering caller', async () => {
    void gw.request('image.clipboard', { placeholder: true })
    await vi.waitFor(() => {
      seen.push(...stdinFrames())
      const req = seen.find(f => f.request?.subtype === 'clipboard_image')
      expect(req?.request?.placeholder).toBe(true)
    })
  })

  it('sends placeholder:false when the caller renders no chip', async () => {
    // `/image`, the startup image and typed-path submit all land here. If this
    // ever flips to true their images are dropped at submit.
    void gw.request('image.attach', { path: '/tmp/a.png' })
    await vi.waitFor(() => {
      seen.push(...stdinFrames())
      const req = seen.find(f => f.request?.subtype === 'attach_image')
      expect(req?.request?.placeholder).toBe(false)
    })
  })

  it('never invents placeholder:true from a truthy-ish value', async () => {
    // Strict === true, so a stray string/1 cannot make a chip authoritative.
    void gw.request('input.detect_drop', { placeholder: 'yes', text: '/tmp/a.png' })
    await vi.waitFor(() => {
      seen.push(...stdinFrames())
      const req = seen.find(f => f.request?.subtype === 'detect_file_drop')
      expect(req?.request?.placeholder).toBe(false)
    })
  })

  it('never resolves an image RPC to undefined', async () => {
    // The composer does `attached?.name`, but `input.detect_drop`'s caller reads
    // `dropped.matched` after a truthiness check — a bare undefined from a
    // backend that predates these controls must still be an object.
    const p = gw.request('image.clipboard', {})
    await replyToControl('clipboard_image', null)
    await expect(p).resolves.toEqual({})
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
