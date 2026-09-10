import { useEffect, useMemo, useState } from 'react'

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

function formatClock(seconds: number): string {
  if (seconds < 60) return `${seconds}s`

  const minutes = Math.floor(seconds / 60)

  return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`
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

  // The status line is the only thing marking a turn that has produced nothing
  // yet; once prose or a tool row is streaming, those carry the activity.
  const showStatus = running && nodes[nodes.length - 1]?.kind !== 'tool'

  return (
    <div className={css.root}>
      <div className={css.column}>
        {nodes.map((node, index) => (
          <div className={css.item} key={node.id}>
            {node.kind === 'user' && <UserMessage node={node} onEdit={onEditPrompt} />}
            {node.kind === 'assistant' && (
              <AssistantMessage
                node={node}
                // The newest reply only, and never mid-turn: the agent refuses
                // to rewind while a turn is running, so offering it would be
                // offering something that cannot happen.
                onRetry={!running && index === nodes.length - 1 ? onRetry : undefined}
              />
            )}
            {node.kind === 'reasoning' && <ReasoningRow node={node} />}
            {node.kind === 'tool' && (
              <ToolRow agent={agentEntries.get(node.toolId)} node={node} workspace={workspace} />
            )}
            {node.kind === 'notice' && <NoticeMessage node={node} />}
          </div>
        ))}
        {showStatus && (
          <div className={css.turnStatus}>
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
