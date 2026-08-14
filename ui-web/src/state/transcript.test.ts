import { beforeEach, describe, expect, it } from 'vitest'

import type { GatewayEvent } from '../gateway/protocol.ts'
import {
  appendUserMessage,
  applyEvent,
  clearApproval,
  emptyTranscript,
  hydrateStoredMessages,
  markTurnStarted,
  resetNodeIds,
  rewindLastTurn,
  type AssistantNode,
  type ReasoningNode,
  type ToolNode,
  type TranscriptState,
} from './transcript.ts'

function event(type: string, payload?: unknown): GatewayEvent {
  return { payload, type } as GatewayEvent
}

function fold(events: GatewayEvent[], from = emptyTranscript()): TranscriptState {
  return events.reduce(applyEvent, from)
}

const assistants = (state: TranscriptState): AssistantNode[] =>
  state.nodes.filter((n): n is AssistantNode => n.kind === 'assistant')
const reasonings = (state: TranscriptState): ReasoningNode[] =>
  state.nodes.filter((n): n is ReasoningNode => n.kind === 'reasoning')
const tools = (state: TranscriptState): ToolNode[] =>
  state.nodes.filter((n): n is ToolNode => n.kind === 'tool')

beforeEach(() => {
  resetNodeIds()
})

describe('streaming prose', () => {
  it('accumulates deltas into one open bubble', () => {
    const state = fold([
      event('message.start'),
      event('message.delta', { text: 'Hello' }),
      event('message.delta', { text: ', world' }),
    ])

    expect(assistants(state)).toHaveLength(1)
    expect(assistants(state)[0]?.text).toBe('Hello, world')
    expect(assistants(state)[0]?.sealed).toBe(false)
    expect(state.running).toBe(true)
  })

  it('seals the bubble and clears the turn on complete', () => {
    const state = fold([
      event('message.start'),
      event('message.delta', { text: 'Done' }),
      event('message.complete', { status: 'ok', text: 'Done' }),
    ])

    expect(assistants(state)).toHaveLength(1)
    expect(assistants(state)[0]?.sealed).toBe(true)
    expect(state.running).toBe(false)
    expect(state.turnStartedAt).toBeUndefined()
  })

  it('drops the replayed copy that rides message.complete', () => {
    // The backend sends the fully-rendered text again on `result`; printing it
    // beside the streamed copy is the classic double-render.
    const state = fold([
      event('message.start'),
      event('message.delta', { text: 'Answer' }),
      event('message.complete', { status: 'ok', text: 'Answer' }),
    ])

    expect(assistants(state).map(n => n.text)).toEqual(['Answer'])
  })

  it('materialises a bubble when the turn streamed nothing', () => {
    const state = fold([
      event('message.start'),
      event('message.complete', { status: 'ok', text: 'Whole answer' }),
    ])

    expect(assistants(state).map(n => n.text)).toEqual(['Whole answer'])
    expect(assistants(state)[0]?.sealed).toBe(true)
  })

  it('treats message.interim as a seal, not new content', () => {
    const state = fold([
      event('message.start'),
      event('message.delta', { text: 'Part one' }),
      event('message.interim', { text: 'Part one' }),
      event('message.delta', { text: 'Part two' }),
    ])

    expect(assistants(state).map(n => n.text)).toEqual(['Part one', 'Part two'])
    expect(assistants(state)[0]?.sealed).toBe(true)
  })

  it('uses interim text when nothing streamed', () => {
    const state = fold([event('message.start'), event('message.interim', { text: 'Only copy' })])

    expect(assistants(state).map(n => n.text)).toEqual(['Only copy'])
  })
})

describe('reasoning', () => {
  it('collects thinking into its own block, separate from prose', () => {
    const state = fold([
      event('message.start'),
      event('reasoning.delta', { text: 'Let me ' }),
      event('reasoning.delta', { text: 'think.' }),
      event('message.delta', { text: 'Here it is.' }),
    ])

    expect(reasonings(state).map(n => n.text)).toEqual(['Let me think.'])
    expect(reasonings(state)[0]?.sealed).toBe(true)
    expect(assistants(state).map(n => n.text)).toEqual(['Here it is.'])
  })

  it('accepts thinking.delta as an alias', () => {
    const state = fold([event('thinking.delta', { text: 'hmm' })])

    expect(reasonings(state).map(n => n.text)).toEqual(['hmm'])
  })
})

describe('tool lifecycle', () => {
  it('opens a running row and resolves it by tool id', () => {
    const state = fold([
      event('message.start'),
      event('tool.start', { args: { command: 'ls' }, name: 'terminal', tool_id: 't1' }),
      event('tool.complete', { name: 'terminal', result: { output: 'a\nb' }, tool_id: 't1' }),
    ])

    expect(tools(state)).toHaveLength(1)
    expect(tools(state)[0]?.state).toBe('done')
    expect(tools(state)[0]?.result?.output).toBe('a\nb')
  })

  it('marks a failed call and keeps its reason', () => {
    const state = fold([
      event('tool.start', { name: 'write_file', tool_id: 't1' }),
      event('tool.complete', { error: 'Error: denied', tool_id: 't1' }),
    ])

    expect(tools(state)[0]?.state).toBe('error')
    expect(tools(state)[0]?.error).toBe('Error: denied')
  })

  it('seals an open bubble so prose after a tool is its own block', () => {
    const state = fold([
      event('message.delta', { text: 'Let me look.' }),
      event('tool.start', { name: 'read_file', tool_id: 't1' }),
      event('tool.complete', { result: { content: 'x' }, tool_id: 't1' }),
      event('message.delta', { text: 'Found it.' }),
    ])

    expect(state.nodes.map(n => n.kind)).toEqual(['assistant', 'tool', 'assistant'])
    expect(assistants(state).map(n => n.text)).toEqual(['Let me look.', 'Found it.'])
  })

  it('never leaves a row spinning after the turn ends', () => {
    const state = fold([
      event('message.start'),
      event('tool.start', { name: 'terminal', tool_id: 't1' }),
      event('message.complete', { status: 'ok' }),
    ])

    expect(tools(state)[0]?.state).toBe('error')
    expect(tools(state)[0]?.error).toBe('Interrupted')
  })

  it('shows a completion whose start was missed', () => {
    // A reload mid-turn: the socket reconnects after tool.start was pushed.
    const state = fold([
      event('tool.complete', { name: 'terminal', result: { output: 'ok' }, tool_id: 'orphan' }),
    ])

    expect(tools(state)).toHaveLength(1)
    expect(tools(state)[0]?.state).toBe('done')
  })
})

describe('turn state', () => {
  it('does not let session.info clear a running turn', () => {
    // The backend stamps running:false on EVERY session.info it publishes.
    const started = markTurnStarted(emptyTranscript())
    const state = applyEvent(started, event('session.info', { model: 'm', running: false }))

    expect(state.running).toBe(true)
    expect(state.info.model).toBe('m')
  })

  it('merges session info rather than replacing it', () => {
    const state = fold([
      event('session.info', { model: 'm', provider: 'p' }),
      event('session.info', { approval_mode: 'manual' }),
    ])

    expect(state.info).toMatchObject({ approval_mode: 'manual', model: 'm', provider: 'p' })
  })

  it('accumulates usage across turns', () => {
    const state = fold([
      event('message.complete', {
        status: 'ok',
        usage: { calls: 1, input: 10, output: 2, total: 12 },
      }),
      event('message.complete', {
        status: 'ok',
        usage: { calls: 1, input: 5, output: 3, total: 8 },
      }),
    ])

    expect(state.usage).toEqual({ calls: 2, input: 15, output: 5, total: 20 })
  })

  it('records a turn error as a notice', () => {
    const state = fold([event('message.complete', { error: 'overloaded', status: 'error' })])

    expect(state.nodes.at(-1)).toMatchObject({ body: 'overloaded', kind: 'notice', tone: 'error' })
    expect(state.running).toBe(false)
  })

  it('drops an empty trailing bubble instead of painting one', () => {
    const state = fold([
      event('message.delta', { text: '' }),
      event('tool.start', { name: 'terminal', tool_id: 't1' }),
    ])

    expect(state.nodes.map(n => n.kind)).toEqual(['tool'])
  })
})

describe('approvals', () => {
  it('parks the request and clears it once answered', () => {
    const asked = applyEvent(
      emptyTranscript(),
      event('approval.request', { command: 'rm -rf x', description: 'Use Bash' }),
    )

    expect(asked.approval?.command).toBe('rm -rf x')
    expect(clearApproval(asked).approval).toBeUndefined()
  })
})

describe('local actions', () => {
  it('appends the user bubble and seals what came before', () => {
    const state = appendUserMessage(
      fold([event('message.delta', { text: 'prior' })]),
      'next question',
    )

    expect(state.nodes.map(n => n.kind)).toEqual(['assistant', 'user'])
    expect(assistants(state)[0]?.sealed).toBe(true)
  })
})

describe('rehydration', () => {
  it('rebuilds prose, thinking and tool rows from a stored transcript', () => {
    const nodes = hydrateStoredMessages([
      { content: 'do the thing', role: 'user' },
      {
        content: [
          { text: 'thinking about it', thinking: 'thinking about it', type: 'thinking' },
          { text: 'On it.', type: 'text' },
          { id: 'tu1', input: { command: 'ls' }, name: 'Bash', type: 'tool_use' },
        ],
        role: 'assistant',
      },
      { content: [{ content: 'a\nb', tool_use_id: 'tu1', type: 'tool_result' }], role: 'user' },
    ])

    expect(nodes.map(n => n.kind)).toEqual(['user', 'reasoning', 'assistant', 'tool'])
    const tool = nodes[3] as ToolNode
    // Raw ClawCodex names must be adapted, or the row loses its terminal card.
    expect(tool.name).toBe('terminal')
    expect(tool.state).toBe('done')
    expect(tool.result?.output).toBe('a\nb')
  })

  it('routes a stored Read result into the field its card reads', () => {
    const nodes = hydrateStoredMessages([
      {
        content: [{ id: 'tu1', input: { file_path: '/a.ts' }, name: 'Read', type: 'tool_use' }],
        role: 'assistant',
      },
      { content: [{ content: '1\tline', tool_use_id: 'tu1', type: 'tool_result' }], role: 'user' },
    ])

    const tool = nodes[0] as ToolNode
    expect(tool.name).toBe('read_file')
    expect(tool.result?.content).toBe('1\tline')
  })

  it('marks a tool whose result was never saved rather than spinning', () => {
    const nodes = hydrateStoredMessages([
      {
        content: [{ id: 'tu1', name: 'Bash', type: 'tool_use' }],
        role: 'assistant',
      },
    ])

    expect((nodes[0] as ToolNode).state).toBe('error')
  })

  it('skips plumbing rows the backend marked hidden', () => {
    const nodes = hydrateStoredMessages([
      { content: '<system-reminder>noise', display_kind: 'hidden', role: 'user' },
      { content: 'real question', role: 'user' },
    ])

    expect(nodes).toHaveLength(1)
    expect(nodes[0]).toMatchObject({ kind: 'user', text: 'real question' })
  })

  it('marks a failed stored tool call', () => {
    const nodes = hydrateStoredMessages([
      { content: [{ id: 'tu1', name: 'Write', type: 'tool_use' }], role: 'assistant' },
      {
        content: [
          { content: 'Error: denied', is_error: true, tool_use_id: 'tu1', type: 'tool_result' },
        ],
        role: 'user',
      },
    ])

    expect(nodes[0]).toMatchObject({ error: 'Error: denied', name: 'write_file', state: 'error' })
  })
})

describe('rewindLastTurn', () => {
  const turn = (prompt: string, reply: string): GatewayEvent[] => [
    event('message.start'),
    event('message.delta', { text: reply }),
    event('message.complete'),
  ]

  function conversation(): TranscriptState {
    let state = appendUserMessage(emptyTranscript(), 'first question')
    state = fold(turn('first question', 'first answer'), state)
    state = appendUserMessage(state, 'second question')

    return fold(turn('second question', 'second answer'), state)
  }

  it('cuts back to just before the last prompt and hands it back', () => {
    // The agent's `rewind` drops whole prompt-turns; cutting anywhere else
    // would leave the two sides disagreeing about the conversation.
    const result = rewindLastTurn(conversation())

    expect(result?.prompt).toBe('second question')
    expect(result?.state.nodes.map(n => n.kind)).toEqual(['user', 'assistant'])
  })

  it('takes the whole turn with it — reasoning and tool rows included', () => {
    let state = appendUserMessage(emptyTranscript(), 'do a thing')
    state = fold(
      [
        event('reasoning.delta', { text: 'thinking' }),
        event('tool.start', { name: 'terminal', tool_id: 't1' }),
        event('tool.complete', { tool_id: 't1' }),
        event('message.delta', { text: 'done' }),
        event('message.complete'),
      ],
      state,
    )

    expect(rewindLastTurn(state)?.state.nodes).toEqual([])
  })

  it('leaves the state it was given untouched', () => {
    // Nothing is trimmed locally until the backend confirms the rewind.
    const before = conversation()
    const count = before.nodes.length

    rewindLastTurn(before)

    expect(before.nodes).toHaveLength(count)
  })

  it('keeps everything else on the state', () => {
    const before = { ...conversation(), running: false, usage: { input: 5, output: 7 } }

    expect(rewindLastTurn(before)?.state.usage).toEqual({ input: 5, output: 7 })
  })

  it('returns null when there is no prompt to rewind to', () => {
    // A transcript of nothing but notices has no turn to re-run, and saying so
    // is better than trimming something arbitrary.
    expect(rewindLastTurn(emptyTranscript())).toBeNull()
    expect(rewindLastTurn(fold([event('error', { message: 'boom' })]))).toBeNull()
  })
})
