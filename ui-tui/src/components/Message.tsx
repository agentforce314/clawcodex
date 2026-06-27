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
import { Banner } from './Banner.js'
import { DiffView } from './DiffView.js'
import { TOOL_VERB } from '../toolMeta.js'
import type { TranscriptEntry } from '../sdkMessageAdapter.js'

const RESULT_MAX_LINES = 8

function ToolResult({ text, isError }: { text: string; isError?: boolean }): React.ReactElement {
  const lines = text.replace(/\s+$/, '').split('\n')
  // Errors render in red (the original's error tool-result variant) and aren't
  // collapsed — the message is what the user needs to see.
  if (isError) {
    const shown = lines.slice(0, RESULT_MAX_LINES)
    const extra = lines.length - shown.length
    return (
      <Box flexDirection="column">
        {shown.map((ln, i) => (
          <Box key={i}>
            <Text color={theme.error}>{i === 0 ? '  ⎿ ' : '    '}</Text>
            <Text color={theme.error}>{ln || ' '}</Text>
          </Box>
        ))}
        {extra > 0 ? <Text color={theme.error}>{`    … +${extra} more line${extra === 1 ? '' : 's'}`}</Text> : null}
      </Box>
    )
  }
  // Collapse a long line-numbered file dump (Read / cat -n) to a single line —
  // several Reads otherwise bury the transcript under hundreds of content lines.
  // The original keeps these collapsed (ctrl+o to expand).
  const numbered = lines.filter((l) => /^\s*\d+[\t |→]/.test(l)).length
  if (lines.length > 6 && numbered >= lines.length * 0.6) {
    return <Text color={theme.dim}>{`  ⎿ Read ${lines.length} lines`}</Text>
  }
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
    case 'banner':
      return entry.bannerData ? <Banner {...entry.bannerData} /> : null
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
      // Collapsed summary of several same-kind calls (e.g. "Read 4 files").
      if (entry.count && entry.count > 1) {
        const noun = TOOL_VERB[entry.toolName ?? '']?.noun || 'files'
        return (
          <Text>
            <Text color={theme.success}>⏺ </Text>
            <Text bold>{entry.toolName}</Text>
            <Text color={theme.dim}>{` ${entry.count} ${noun}`}</Text>
          </Text>
        )
      }
      const diff = entry.diff
      const isWeb = entry.toolName === 'WebFetch' || entry.toolName === 'WebSearch'
      // File-edit tools display as Update/Create/Write (the original's
      // userFacingName), not the raw tool id.
      const name = diff ? diff.displayName : entry.toolName
      return (
        <Box flexDirection="column">
          <Text>
            <Text color={theme.success}>⏺ </Text>
            <Text bold>{name}</Text>
            <Text color={theme.dim}>(</Text>
            <Text color={isWeb ? theme.link : theme.dim}>{entry.argsText}</Text>
            <Text color={theme.dim}>)</Text>
          </Text>
          {diff ? <DiffView diff={diff} /> : null}
        </Box>
      )
    }
    case 'toolResult':
      return <ToolResult text={entry.text} isError={entry.isError} />
    case 'result':
      return <Text color={theme.success}>{`✓ ${entry.text}`}</Text>
    case 'error':
      return <Text color={theme.error}>{`✗ ${entry.text}`}</Text>
    case 'system':
    default:
      return <Text color={theme.system}>{`· ${entry.text}`}</Text>
  }
}
