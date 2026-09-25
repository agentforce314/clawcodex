/**
 * The agents overlay ("Spawn tree") must say WHO each agent is, not only what
 * it was asked to do. Rows used to render just the task description, so a
 * team of named specialists (nl-sketcher, fl-formalizer, …) read as a list of
 * anonymous tasks:
 *
 *   1 ● Pin environment encode statement ·104t
 *   2 ✓ Map informal proof obligations ·22t
 *
 * Now the spawn name — or, for an unnamed spawn, the agent definition — leads
 * the row. Default types (general-purpose, worker, fork) say nothing about the
 * agent and stay hidden, as the reference's userFacingName hides them:
 *
 *   1 ● fl-formalizer · Pin environment encode statement ·104t
 *   2 ✓ Explore · Map informal proof obligations ·22t
 *   3 ✓ Extract eligible theorem precisely ·8t
 */
import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  process.env.FORCE_COLOR = '0'
  process.env.NO_COLOR = '1'
})

import { clearSpawnHistory } from '../app/spawnHistoryStore.js'
import { $sessionAgents, patchTurnState, resetTurnState } from '../app/turnStore.js'
import { AgentsOverlay } from '../components/agentsOverlay.js'
import type { GatewayClient } from '../gatewayClient.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { SubagentProgress } from '../types.js'

const renderToString = (element: React.ReactElement): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 100, rows: 40 })
  // The overlay reads keys (useInput): a non-TTY stdin makes Ink throw "Raw
  // mode is not supported" after the first frame.
  Object.assign(stdin, { isTTY: true, ref: () => {}, setRawMode: () => {}, unref: () => {} })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  return stripAnsi(output)
}

const item = (
  overrides: Partial<SubagentProgress> & Pick<SubagentProgress, 'goal' | 'id' | 'index'>
): SubagentProgress => ({
  depth: 0,
  notes: [],
  parentId: null,
  status: 'completed',
  taskCount: 1,
  thinking: [],
  toolCount: 1,
  tools: [],
  ...overrides
})

const gw = { request: vi.fn(() => Promise.resolve({})) } as unknown as GatewayClient

beforeEach(() => {
  resetTurnState()
  clearSpawnHistory()
  $sessionAgents.set({})
})

describe('agents overlay rows name the agent', () => {
  it('leads each row with the spawn name, else the agent type, then the task', () => {
    patchTurnState({
      subagents: [
        item({
          agentType: 'math-fl-formalizer',
          goal: 'Pin environment encode statement',
          id: 'a1',
          index: 0,
          name: 'fl-formalizer',
          status: 'running'
        }),
        item({ agentType: 'Explore', goal: 'Map informal proof obligations', id: 'a2', index: 1 }),
        item({ agentType: 'general-purpose', goal: 'Extract eligible theorem precisely', id: 'a3', index: 2 }),
        item({ goal: 'A row from an older gateway', id: 'a4', index: 3 })
      ]
    })

    const out = renderToString(<AgentsOverlay gw={gw} onClose={() => {}} t={DEFAULT_THEME} />)

    // Anchored on the status glyph: a bare substring would also match the
    // type ("math-fl-formalizer · …") if name and type swapped precedence.
    expect(out).toMatch(/● fl-formalizer · Pin environment encode statement/)
    expect(out).toMatch(/✓ Explore · Map informal proof obligations/)
    // A default type says nothing — the bare task, with no dangling separator.
    expect(out).toMatch(/✓ Extract eligible theorem precisely/)
    expect(out).not.toContain('general-purpose ·')
    expect(out).toMatch(/✓ A row from an older gateway/)
    // A clean mount — no Ink error screen after the first frame.
    expect(out).not.toContain('ERROR')
  })
})

describe('agents overlay keeps agents that outlive their turn', () => {
  it('lists a still-running teammate from an earlier turn beside this turn’s subagents', () => {
    // A later turn: its own subagent in turn state, while the teammate spawned
    // in an earlier turn survives only in the session roster.
    patchTurnState({
      subagents: [item({ agentType: 'Explore', goal: 'Find cutoff eligible literature', id: 'a3', index: 0 })]
    })
    $sessionAgents.set({
      a1: item({ goal: 'Read the notes', id: 'a1', index: 0 }),
      t1: item({
        agentType: 'math-fl-formalizer',
        goal: 'Pin environment encode statement',
        id: 't1',
        index: 0,
        name: 'fl-formalizer',
        status: 'running'
      })
    })

    const out = renderToString(<AgentsOverlay gw={gw} onClose={() => {}} t={DEFAULT_THEME} />)

    expect(out).toMatch(/✓ Explore · Find cutoff eligible literature/)
    expect(out).toMatch(/● fl-formalizer · Pin environment encode statement/)
    // A finished agent from an earlier turn belongs to that turn's archive.
    expect(out).not.toContain('Read the notes')
  })
})
