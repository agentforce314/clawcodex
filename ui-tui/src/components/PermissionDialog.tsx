/**
 * Tool-permission prompt — matches the original Claude Code permission dialog
 * (components/permissions/PermissionDialog.tsx): a top-rule-only frame in the
 * permission blue-purple, the tool name + an args preview, then a numbered
 * option list with "Yes" highlighted. The wire protocol is allow/deny, so the
 * keys are 1/y = allow, 2/n = deny, esc = interrupt.
 */
import { Box, Text } from 'ink'
import React from 'react'
import { theme } from '../theme.js'

interface Props {
  toolName: string
  input: Record<string, unknown>
}

export function PermissionDialog({ toolName, input }: Props): React.ReactElement {
  const keys = Object.keys(input ?? {})
  const preview = keys.map((k) => {
    const v = (input as Record<string, unknown>)[k]
    const s = typeof v === 'string' ? v : JSON.stringify(v)
    return `${k}: ${s.length > 120 ? `${s.slice(0, 119)}…` : s}`
  })

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.suggestion}
      borderBottom={false}
      borderLeft={false}
      borderRight={false}
      paddingX={1}
      marginTop={1}
    >
      <Text>
        <Text color={theme.suggestion} bold>
          {toolName}
        </Text>
        <Text color={theme.dim}>{' wants to run'}</Text>
      </Text>
      {preview.length ? (
        <Box flexDirection="column" marginTop={1}>
          {preview.map((ln, i) => (
            <Text key={i} color={theme.dim}>
              {`  ${ln}`}
            </Text>
          ))}
        </Box>
      ) : null}
      <Box marginTop={1} flexDirection="column">
        <Text color={theme.dim}>Do you want to proceed?</Text>
        <Text>
          <Text color={theme.suggestion} bold>
            {'❯ '}
          </Text>
          <Text bold>1. Yes</Text>
          <Text color={theme.dim}>{'   (y)'}</Text>
        </Text>
        <Text color={theme.dim}>{'  2. No, and tell the agent what to do differently   (n / esc)'}</Text>
      </Box>
    </Box>
  )
}
