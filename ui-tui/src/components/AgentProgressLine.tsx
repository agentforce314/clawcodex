/**
 * Live progress line for a running subagent (the original's AgentProgressLine),
 * driven by `agent_progress` messages the agent-server streams from the Agent
 * tool's run_agent on_message hook. Shows the task, its current activity, and a
 * running tool/token count — in the subagent blue-purple.
 */
import { Box, Text } from '../ink.js'
import React from 'react'
import { theme } from '../theme.js'

export interface AgentLine {
  agentId: string
  name: string
  description: string
  activity: string
  toolUseCount: number
  tokens: number
}

function fmtK(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export function AgentProgressLine({ line }: { line: AgentLine }): React.ReactElement {
  const label = line.name ? `${line.description} (@${line.name})` : line.description || 'Task'
  const stats: string[] = []
  if (line.toolUseCount > 0) stats.push(`${line.toolUseCount} tool${line.toolUseCount === 1 ? '' : 's'}`)
  if (line.tokens > 0) stats.push(`${fmtK(line.tokens)} tokens`)
  return (
    <Box>
      <Box width={2}>
        <Text color={theme.suggestion}>⏺</Text>
      </Box>
      <Box flexGrow={1}>
        <Text>
          <Text bold>{label}</Text>
          {line.activity ? <Text color={theme.dim}>{`  ${line.activity}`}</Text> : null}
          {stats.length ? <Text color={theme.dim}>{`  · ${stats.join(' · ')}`}</Text> : null}
        </Text>
      </Box>
    </Box>
  )
}
