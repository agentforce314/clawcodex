import { describe, expect, it } from 'vitest'

import {
  describeStatus,
  formatRunDuration,
  isAgentTool,
  subagentCatalog,
  subagentCounts,
} from './subagents.ts'
import type { SubagentLive, ToolNode, TranscriptNode } from './transcript.ts'

let counter = 0

function agentCall(overrides: Partial<ToolNode> = {}): ToolNode {
  counter += 1

  return {
    args: { description: `Task ${counter}`, prompt: `Do task ${counter}\nin detail`, subagent_type: 'Explore' },
    id: `n${counter}`,
    kind: 'tool',
    name: 'Agent',
    startedAt: 1_000,
    state: 'running',
    toolId: `call_${counter}`,
    ...overrides,
  }
}

function live(overrides: Partial<SubagentLive> & { agentId: string }): SubagentLive {
  return { startedAt: 1_000, status: 'running', ...overrides }
}

describe('isAgentTool', () => {
  it('names both spellings of the delegation tool', () => {
    expect(isAgentTool('Agent')).toBe(true)
    expect(isAgentTool('Task')).toBe(true)
    expect(isAgentTool('terminal')).toBe(false)
  })
})

describe('subagentCatalog', () => {
  it('lists nothing for a transcript without delegations', () => {
    const nodes: TranscriptNode[] = [
      { at: 0, id: 'u', kind: 'user', text: 'hi' },
      { ...agentCall(), name: 'terminal' },
    ]

    expect(subagentCatalog(nodes, {}, 5_000)).toEqual([])
  })

  it('describes a settled call from its own result', () => {
    const node = agentCall({
      endedAt: 4_000,
      result: {
        agent: { agent_id: 'a1', duration_ms: 2_500, model: 'flash', status: 'completed', tokens: 1_200, tool_count: 3 },
        output: '# Report\n\nAll good.',
      },
      state: 'done',
    })

    const [entry] = subagentCatalog([node], {}, 9_000)

    expect(entry).toMatchObject({
      agentId: 'a1',
      durationMs: 2_500,
      key: `tool:${node.toolId}`,
      label: node.args.description,
      model: 'flash',
      report: '# Report\n\nAll good.',
      status: 'completed',
      subagentType: 'Explore',
      tokens: 1_200,
      toolCount: 3,
      toolId: node.toolId,
    })
    expect(entry?.prompt).toBe(node.args.prompt)
  })

  it('joins a running call to its progress by the call id', () => {
    const node = agentCall()
    const agents = {
      a9: live({ activity: 'Reading README.md', agentId: 'a9', toolCount: 2, toolUseId: node.toolId }),
    }

    const [entry] = subagentCatalog([node], agents, 3_000)

    expect(entry).toMatchObject({
      activity: 'Reading README.md',
      agentId: 'a9',
      durationMs: 2_000,
      status: 'running',
      toolCount: 2,
    })
  })

  it('falls back to the description when the progress names no call', () => {
    const first = agentCall({ args: { description: 'Audit auth', prompt: 'x' } })
    const second = agentCall({ args: { description: 'Audit data', prompt: 'y' } })
    const agents = {
      p1: live({ agentId: 'p1', description: 'Audit data' }),
      p2: live({ agentId: 'p2', description: 'Audit auth' }),
    }

    const entries = subagentCatalog([first, second], agents, 2_000)

    expect(entries.map(entry => entry.agentId)).toEqual(['p2', 'p1'])
  })

  it('never claims one run for two calls', () => {
    const first = agentCall({ args: { description: 'Same', prompt: 'x' } })
    const second = agentCall({ args: { description: 'Same', prompt: 'y' } })
    const agents = { only: live({ agentId: 'only', description: 'Same' }) }

    const entries = subagentCatalog([first, second], agents, 2_000)

    expect(entries[0]?.agentId).toBe('only')
    expect(entries[1]?.agentId).toBeUndefined()
  })

  it('reads a terminal frame as the run ending, even before the row settles', () => {
    const node = agentCall()
    const agents = {
      a1: live({ agentId: 'a1', endedAt: 4_000, status: 'failed', toolUseId: node.toolId }),
    }

    expect(subagentCatalog([node], agents, 9_000)[0]).toMatchObject({ durationMs: 3_000, status: 'failed' })
  })

  it('reports a failed call as failed, with the error as its report', () => {
    const node = agentCall({ endedAt: 2_000, error: 'Error: spawn refused', state: 'error' })

    expect(subagentCatalog([node], {}, 9_000)[0]).toMatchObject({
      report: 'Error: spawn refused',
      status: 'failed',
    })
  })

  it('marks a background launch whose outcome never arrived', () => {
    const node = agentCall({
      endedAt: 1_100,
      result: { agent: { agent_id: 'bg', status: 'async_launched' }, output: 'Async agent launched' },
      state: 'done',
    })

    expect(subagentCatalog([node], {}, 9_000)[0]).toMatchObject({ agentId: 'bg', status: 'background' })
  })

  it('lets a later frame settle a background launch', () => {
    const node = agentCall({
      endedAt: 1_100,
      result: { agent: { agent_id: 'bg', status: 'async_launched' } },
      state: 'done',
    })
    const agents = { bg: live({ agentId: 'bg', endedAt: 8_000, status: 'completed', toolCount: 7 }) }

    expect(subagentCatalog([node], agents, 9_000)[0]).toMatchObject({ status: 'completed', toolCount: 7 })
  })

  it('keeps a run that has frames but no row', () => {
    const agents = { lone: live({ agentId: 'lone', description: 'Orphan work', name: 'orphan' }) }

    expect(subagentCatalog([], agents, 5_000)).toEqual([
      expect.objectContaining({ agentId: 'lone', key: 'agent:lone', label: 'Orphan work', status: 'running' }),
    ])
  })

  it('counts the running ones', () => {
    const entries = subagentCatalog(
      [agentCall(), agentCall({ endedAt: 2_000, result: { agent: { agent_id: 'd', status: 'completed' } }, state: 'done' })],
      {},
      3_000,
    )

    expect(subagentCounts(entries)).toEqual({ running: 1, total: 2 })
  })
})

describe('formatRunDuration', () => {
  it('reads seconds, minutes and hours at their own precision', () => {
    expect(formatRunDuration(42_000)).toBe('42s')
    expect(formatRunDuration(198_000)).toBe('3m 18s')
    expect(formatRunDuration(3_720_000)).toBe('1h 02m')
  })
})

describe('describeStatus', () => {
  it('has a word for every state', () => {
    expect(describeStatus('running')).toBe('running')
    expect(describeStatus('completed')).toBe('done')
    expect(describeStatus('background')).toBe('in background')
  })
})
