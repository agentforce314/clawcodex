import { describe, expect, it, vi } from 'vitest'

import { GatewayClient } from '../gatewayClient.js'

import { planApprovalOptions } from '../components/prompts.js'

describe('planApprovalOptions — ExitPlanModePermissionRequest option arms', () => {
  it('offers auto-accept edits when bypass is unavailable', () => {
    const opts = planApprovalOptions(false)

    expect(opts.map(o => o.choice)).toEqual(['accept-edits', 'default', 'deny'])
    expect(opts[0]!.label).toBe('Yes, auto-accept edits')
    expect(opts[1]!.label).toBe('Yes, manually approve edits')
    expect(opts[2]!.label).toBe('No, keep planning')
  })

  it('elevates to bypass permissions when the session launched with bypass', () => {
    const opts = planApprovalOptions(true)

    expect(opts.map(o => o.choice)).toEqual(['bypass', 'default', 'deny'])
    expect(opts[0]!.label).toBe('Yes, and bypass permissions')
  })
})

// Wire-level: the gatewayClient must route ExitPlanMode asks to the plan
// dialog (plan.approval), keep every other tool on the generic box, and map
// planApproval.respond choices to chosen_updates setMode / deny+feedback.
describe('gatewayClient plan-approval routing', () => {
  const mkClient = () => {
    const gw = new GatewayClient() as any
    const events: any[] = []

    gw.subscribed = true
    gw.emit = (kind: string, ev: any) => {
      if (kind === 'event') {
        events.push(ev)
      }
    }

    const sent: any[] = []

    gw.send = (obj: unknown) => sent.push(obj)

    return { events, gw, sent }
  }

  it('routes ExitPlanMode can_use_tool to plan.approval with the plan payload', () => {
    const { events, gw } = mkClient()

    gw.handleServerControl({
      request: {
        bypass_available: true,
        input: {},
        plan: '# The Plan',
        plan_file_path: '/plans/x.md',
        subtype: 'can_use_tool',
        suggestions: [],
        tool_name: 'ExitPlanMode'
      },
      request_id: 'r1'
    })

    const ev = events.find(e => e.type === 'plan.approval')

    expect(ev).toBeTruthy()
    expect(ev.payload).toEqual({ bypass_available: true, plan: '# The Plan', plan_file_path: '/plans/x.md' })
    // No generic approval box for the plan dialog.
    expect(events.find(e => e.type === 'approval.request')).toBeUndefined()
  })

  it('keeps other tools on the generic approval box', () => {
    const { events, gw } = mkClient()

    gw.handleServerControl({
      request: { input: { command: 'rm -rf x' }, subtype: 'can_use_tool', suggestions: [], tool_name: 'Bash' },
      request_id: 'r2'
    })

    expect(events.find(e => e.type === 'approval.request')).toBeTruthy()
    expect(events.find(e => e.type === 'plan.approval')).toBeUndefined()
  })

  it.each([
    ['accept-edits', 'acceptEdits'],
    ['bypass', 'bypassPermissions'],
    ['default', 'default']
  ])('planApproval.respond %s → allow + setMode %s (session)', async (choice, mode) => {
    const { gw, sent } = mkClient()

    gw.handleServerControl({
      request: { input: {}, plan: 'p', subtype: 'can_use_tool', suggestions: [], tool_name: 'ExitPlanMode' },
      request_id: 'r3'
    })

    await gw.request('planApproval.respond', { choice })

    const resp = sent.find(m => m.type === 'control_response')

    expect(resp.response.request_id).toBe('r3')
    expect(resp.response.response.behavior).toBe('allow')
    expect(resp.response.response.chosen_updates).toEqual([{ destination: 'session', mode, type: 'setMode' }])
  })

  it('planApproval.respond deny carries the keep-planning feedback', async () => {
    const { gw, sent } = mkClient()

    gw.handleServerControl({
      request: { input: {}, plan: 'p', subtype: 'can_use_tool', suggestions: [], tool_name: 'ExitPlanMode' },
      request_id: 'r4'
    })

    await gw.request('planApproval.respond', { choice: 'deny', feedback: 'also update the README' })

    const resp = sent.find(m => m.type === 'control_response')

    expect(resp.response.response).toEqual({ behavior: 'deny', message: 'also update the README' })
  })

  it('system/status with permission_mode publishes permission.mode', () => {
    const { events, gw } = mkClient()

    gw.dispatch({ permission_mode: 'acceptEdits', subtype: 'status', type: 'system' })

    const ev = events.find(e => e.type === 'permission.mode')

    expect(ev?.payload).toEqual({ mode: 'acceptEdits' })
  })
})

describe('/plan slash — CC semantics', () => {
  const mkClient = () => {
    const gw = new GatewayClient() as any

    gw.subscribed = true
    gw.emit = vi.fn()
    gw.send = vi.fn()

    return gw
  }

  it('enables plan mode when not in it', async () => {
    const gw = mkClient()
    const calls: any[] = []

    gw.controlQuery = (subtype: string, payload: any) => {
      calls.push([subtype, payload])

      if (subtype === 'plan') {
        return Promise.resolve({ mode: 'default', ok: true, plan: null })
      }

      return Promise.resolve({ mode: 'plan', ok: true })
    }

    const r = await gw.dispatchSlash('plan')

    expect(calls[0]![0]).toBe('plan')
    expect(calls[1]).toEqual(['set_permission_mode', { mode: 'plan' }])
    expect(r.output).toBe('Enabled plan mode')
  })

  it('submits the description as a prompt when given an argument', async () => {
    const gw = mkClient()

    gw.controlQuery = (subtype: string) =>
      Promise.resolve(subtype === 'plan' ? { mode: 'default', ok: true } : { mode: 'plan', ok: true })

    const r = await gw.dispatchSlash('plan', 'refactor the auth flow')

    expect(r).toEqual({ message: 'refactor the auth flow', notice: 'Enabled plan mode', type: 'send' })
  })

  it('shows the current plan when already in plan mode', async () => {
    const gw = mkClient()

    gw.controlQuery = () =>
      Promise.resolve({ mode: 'plan', ok: true, plan: '# P', plan_file_path: '/plans/a.md' })

    const r = await gw.dispatchSlash('plan')

    expect(r.output).toContain('Current Plan')
    expect(r.output).toContain('/plans/a.md')
    expect(r.output).toContain('# P')
  })

  it('reports an empty plan in plan mode', async () => {
    const gw = mkClient()

    gw.controlQuery = () => Promise.resolve({ mode: 'plan', ok: true, plan: null })

    const r = await gw.dispatchSlash('plan')

    expect(r.output).toBe('Already in plan mode. No plan written yet.')
  })
})
