import type { TranscriptNode, ToolNode, ReasoningNode } from '../state/transcript.ts'

export type WorkNode = ToolNode | ReasoningNode
export interface ChatTurn { id: string; nodes: TranscriptNode[] }
export type ChatPart = { kind: 'work'; id: string; nodes: WorkNode[] } | { kind: 'message'; node: TranscriptNode }

/** A user input starts a turn. Leading notices remain visible in their own turn. */
export function chatTurns(nodes: readonly TranscriptNode[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  for (const node of nodes) {
    if (node.kind === 'user' || turns.length === 0) turns.push({ id: node.id, nodes: [] })
    turns[turns.length - 1]!.nodes.push(node)
  }
  return turns
}

/** Only adjacent process rows group: messages and failures keep their chronological position. */
export function chatParts(nodes: readonly TranscriptNode[]): ChatPart[] {
  const parts: ChatPart[] = []
  for (const node of nodes) {
    if (node.kind === 'tool' || node.kind === 'reasoning') {
      const last = parts[parts.length - 1]
      if (last?.kind === 'work') last.nodes.push(node)
      else parts.push({ kind: 'work', id: node.id, nodes: [node] })
    } else parts.push({ kind: 'message', node })
  }
  return parts
}
