/**
 * Renders one finished transcript entry, Claude-Code style:
 *   › user text                 (cyan)
 *   ⏺ assistant markdown        (⏺ marker column + markdown body)
 *   ⏺ Bash(ls -la)              (tool call: name bold + dim args)
 *     ⎿ output… (+N lines)      (tool result: indented, dim, line-capped)
 *   · system   ✓ result   ✗ error
 */
import { Box, Text } from 'ink'
import React from 'react'
import { Markdown } from '../markdown.js'
import { theme } from '../theme.js'
import { DiffView } from './DiffView.js'
import { toolDiff } from '../diff.js'
import type { TranscriptEntry } from '../sdkMessageAdapter.js'

const RESULT_MAX_LINES = 8

function ToolResult({ text }: { text: string }): React.ReactElement {
  const lines = text.replace(/\s+$/, '').split('\n')
  const shown = lines.slice(0, RESULT_MAX_LINES)
  const extra = lines.length - shown.length
  return (
    <Box flexDirection="column">
      {shown.map((ln, i) => (
        <Box key={i}>
          <Text color={theme.dim}>{i === 0 ? '  ⎿ ' : '    '}</Text>
          <Text color={theme.toolResult}>{ln || ' '}</Text>
        </Box>
      ))}
      {extra > 0 ? <Text color={theme.dim}>{`    … +${extra} more line${extra === 1 ? '' : 's'}`}</Text> : null}
    </Box>
  )
}

export function Message({ entry }: { entry: TranscriptEntry }): React.ReactElement | null {
  switch (entry.kind) {
    case 'user':
      return (
        <Box>
          <Box width={2}>
            <Text color={theme.dim} bold>
              ›
            </Text>
          </Box>
          <Box flexGrow={1}>
            <Text backgroundColor={theme.userBg}> {entry.text} </Text>
          </Box>
        </Box>
      )
    case 'assistant':
      return (
        <Box>
          <Box width={2}>
            <Text color={theme.assistant}>⏺</Text>
          </Box>
          <Box flexDirection="column" flexGrow={1}>
            <Markdown text={entry.text} />
          </Box>
        </Box>
      )
    case 'tool': {
      const diff = entry.toolName ? toolDiff(entry.toolName, entry.input ?? {}) : null
      return (
        <Box flexDirection="column">
          <Text>
            <Text color={theme.success}>⏺ </Text>
            <Text bold>{entry.toolName}</Text>
            <Text color={theme.dim}>{`(${entry.argsText})`}</Text>
          </Text>
          {diff ? <DiffView lines={diff} /> : null}
        </Box>
      )
    }
    case 'toolResult':
      return <ToolResult text={entry.text} />
    case 'result':
      return <Text color={theme.success}>{`✓ ${entry.text}`}</Text>
    case 'error':
      return <Text color={theme.error}>{`✗ ${entry.text}`}</Text>
    case 'system':
    default:
      return <Text color={theme.system}>{`· ${entry.text}`}</Text>
  }
}
