/**
 * Tool-permission prompt — a bordered Claude-Code-style dialog. The wire
 * protocol's control_response is allow/deny, so we offer (y) allow / (n) deny
 * (esc = deny). The args preview shows what the tool wants to do.
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
  const preview =
    keys.length > 0
      ? keys
          .map((k) => {
            const v = (input as Record<string, unknown>)[k]
            const s = typeof v === 'string' ? v : JSON.stringify(v)
            return `${k}: ${s.length > 120 ? `${s.slice(0, 119)}…` : s}`
          })
          .join('\n')
      : null

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.warn} paddingX={1}>
      <Text color={theme.warn} bold>
        ⏵ Permission required
      </Text>
      <Text>
        Allow <Text bold color={theme.tool}>{toolName}</Text> to run?
      </Text>
      {preview ? (
        <Box flexDirection="column" marginTop={1}>
          {preview.split('\n').map((ln, i) => (
            <Text key={i} color={theme.dim}>
              {ln}
            </Text>
          ))}
        </Box>
      ) : null}
      <Box marginTop={1}>
        <Text color={theme.success} bold>
          (y)
        </Text>
        <Text>{' allow   '}</Text>
        <Text color={theme.error} bold>
          (n)
        </Text>
        <Text>{' deny   '}</Text>
        <Text color={theme.dim}>esc to deny</Text>
      </Box>
    </Box>
  )
}
