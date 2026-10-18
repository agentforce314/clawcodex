import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'

import { $reading } from '../state/preferences.ts'
import { chatParts, chatTurns, type WorkNode } from './work-groups.ts'
import { describeTool } from './tool-view.ts'

import { subagentCatalog, type SubagentEntry } from '../state/subagents.ts'
import type { SubagentLive, TranscriptNode } from '../state/transcript.ts'
import { AssistantMessage, NoticeMessage, UserMessage } from './MessageItem.tsx'
import { ReasoningRow } from './ReasoningRow.tsx'
import { ToolRow } from './ToolRow.tsx'
import css from './ChatView.module.css'

const NO_AGENTS: Record<string, SubagentLive> = {}

export interface ChatViewProps {
  /** Live subagent state, so an Agent row can say what its run is doing. */
  agents?: Record<string, SubagentLive>
  nodes: TranscriptNode[]
  onEditPrompt?: (text: string) => void
  onRetry?: () => void
  running: boolean
  turnStartedAt?: number
  workspace?: string
}

/** Elapsed seconds since `startedAt`, ticking once a second while it runs. */
function useElapsed(startedAt: number | undefined): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (startedAt === undefined) return

    const timer = setInterval(() => {
      setNow(Date.now())
    }, 1000)

    return () => {
      clearInterval(timer)
    }
  }, [startedAt])

  if (startedAt === undefined) return 0

  return Math.max(0, Math.round((now - startedAt) / 1000))
}

/**
 * The running turn's clock: seconds, then minutes and seconds past a minute,
 * then hours as well past an hour, the smaller units zero-padded so the label
 * keeps its width as it ticks.
 */
export function formatClock(seconds: number): string {
  if (seconds < 60) return `${String(seconds)}s`

  const minutes = Math.floor(seconds / 60) % 60
  const rest = String(seconds % 60).padStart(2, '0')

  if (seconds < 3600) return `${String(minutes)}m ${rest}s`

  return `${String(Math.floor(seconds / 3600))}h ${String(minutes).padStart(2, '0')}m ${rest}s`
}

/**
 * The transcript flow.
 *
 * Deliberately not a scroller: the conversation root owns one scrollport for
 * the flow *and* the sticky composer, so a wheel gesture over the input card
 * still moves the conversation.
 */
export function ChatView({
  agents = NO_AGENTS,
  nodes,
  onEditPrompt,
  onRetry,
  running,
  turnStartedAt,
  workspace,
}: ChatViewProps) {
  const elapsed = useElapsed(running ? turnStartedAt : undefined)

  // One fold for every Agent row on screen, keyed by the call it came from.
  const agentEntries = useMemo(() => {
    const byTool = new Map<string, SubagentEntry>()

    for (const entry of subagentCatalog(nodes, agents)) {
      if (entry.toolId !== undefined) byTool.set(entry.toolId, entry)
    }

    return byTool
  }, [agents, nodes])

  const preferences = useStore($reading)
  const turns = useMemo(() => chatTurns(nodes), [nodes])
  const [shown, setShown] = useState(20)
  const root = useRef<HTMLDivElement>(null)
  const scrollAnchor = useRef<{ element: Element; top: number } | null>(null)
  const hidden = Math.max(0, turns.length - shown)

  // Preserve the first visible turn when older history is inserted above it.
  useLayoutEffect(() => {
    const anchor = scrollAnchor.current
    if (anchor === null) return
    const scroller = root.current?.closest('[data-chat-scroll]')
    if (scroller) scroller.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top
    scrollAnchor.current = null
  }, [shown])

  const renderNode = (node: TranscriptNode) => <div className={css.item} key={node.id}>
    {node.kind === 'user' && <UserMessage node={node} onEdit={onEditPrompt} />}
    {node.kind === 'assistant' && <AssistantMessage node={node}
      onRetry={!running && node === nodes[nodes.length - 1] ? onRetry : undefined} />}
    {node.kind === 'reasoning' && <ReasoningRow node={node} />}
    {node.kind === 'tool' && <ToolRow agent={agentEntries.get(node.toolId)} node={node} workspace={workspace} />}
    {node.kind === 'notice' && <NoticeMessage node={node} />}
  </div>

  return (
    <div className={css.root} ref={root}>
      <div className={css.column}>
        {hidden > 0 && <button className={css.history} type="button" onClick={() => {
          const element = root.current?.querySelector('[data-chat-turn]')
          if (element) scrollAnchor.current = { element, top: element.getBoundingClientRect().top }
          setShown(value => value + 20)
        }}>Show earlier messages ({hidden} turns)</button>}
        {turns.slice(hidden).map((turn, index) => {
          const latest = hidden + index === turns.length - 1
          const current = latest && (running || preferences.collapseTiming === 'next-input')
          const grouped = preferences.workDetails !== 'verbose' && !(current && preferences.workDetails === 'detailed')
          return <div className={css.turn} key={turn.id} data-chat-turn={turn.id}>
            {grouped ? chatParts(turn.nodes).map(part => part.kind === 'message'
              ? renderNode(part.node)
              : <WorkGroup key={`${part.id}:${preferences.workDetails}:${current}`} nodes={part.nodes}
                  compact={preferences.workDetails === 'compact'} workspace={workspace}>
                  {part.nodes.map(renderNode)}
                </WorkGroup>) : turn.nodes.map(renderNode)}
          </div>
        })}
        {running && (
          <div className={css.turnStatus} role="status">
            Working
            {turnStartedAt !== undefined && elapsed > 0 && (
              <span className={css.turnStatusClock}>{formatClock(elapsed)}</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function WorkGroup({ nodes, compact, workspace, children }: {
  nodes: WorkNode[]; compact: boolean; workspace?: string; children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const tools = nodes.filter(node => node.kind === 'tool')
  const failures = tools.filter(node => node.state === 'error').length
  const live = nodes.findLast(node => node.kind === 'tool' ? node.state === 'running' : !node.sealed)
  const detail = live?.kind === 'tool' ? describeTool(live, workspace).summary
    : live?.kind === 'reasoning' ? live.text.split('\n').find(line => line.trim() !== '') : undefined
  return <section className={css.workGroup} data-running={live ? '' : undefined}>
    <button className={css.workHeader} type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span aria-hidden="true">{open ? '▾' : '▸'}</span>
      <span>{live ? 'Working' : 'Work details'} · {tools.length} {tools.length === 1 ? 'tool' : 'tools'}
        {nodes.length > tools.length ? ' · thoughts' : ''}</span>
      {failures > 0 && <span className={css.failure}>{failures} failed</span>}
      {!compact && detail && <span className={css.workDetail}>{detail}</span>}
    </button>
    {open && <div className={css.workBody}>{children}</div>}
  </section>
}
