/**
 * Tool-permission prompt — faithful to the original Claude Code permission
 * dialog (components/permissions/PermissionDialog.tsx + per-tool requests):
 * a top-rule-only box in the permission blue-purple, a bold tool title + dim
 * subtitle, a TOOL-SPECIFIC preview (the diff for Edit/Write, the highlighted
 * command for Bash, the URL for Fetch, …), then the question + options.
 *
 * The wire protocol is allow/deny (no rule persistence), so we show two options
 * — App.tsx maps 1/y → allow, 2/n/esc → deny. Session-wide "don't ask again"
 * is `/mode acceptEdits`.
 */
import { highlight } from 'cli-highlight'
import { Box, Text } from 'ink'
import { readFileSync } from 'node:fs'
import React from 'react'
import { buildToolDiff } from '../diff.js'
import { theme } from '../theme.js'
import { DiffView } from './DiffView.js'

interface Props {
  toolName: string
  input: Record<string, unknown>
}

function readFileSafe(p: unknown): string | undefined {
  if (typeof p !== 'string' || !p) return undefined
  try {
    return readFileSync(p, 'utf8')
  } catch {
    return undefined
  }
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v)
}

/** Tool category title (bold, permission-colored) + dim subtitle. */
function titleFor(toolName: string, input: Record<string, unknown>): { title: string; subtitle: string } {
  const fp = str(input['file_path']) || str(input['path'])
  switch (toolName) {
    case 'Bash':
      return { title: 'Bash command', subtitle: str(input['description']) }
    case 'Edit':
    case 'MultiEdit':
      return { title: 'Edit file', subtitle: fp }
    case 'Write':
      return { title: 'Write file', subtitle: fp }
    case 'WebFetch':
      return { title: 'Fetch', subtitle: str(input['url']) }
    case 'WebSearch':
      return { title: 'Web search', subtitle: str(input['query']) }
    case 'Read':
      return { title: 'Read file', subtitle: fp }
    case 'Grep':
      return { title: 'Search', subtitle: str(input['pattern']) }
    case 'Glob':
      return { title: 'Find files', subtitle: str(input['pattern']) }
    default:
      return { title: toolName, subtitle: '' }
  }
}

/** Tool-specific preview body (rendered between title and the question). */
function Preview({ toolName, input }: Props): React.ReactElement | null {
  if (toolName === 'Edit' || toolName === 'MultiEdit' || toolName === 'Write') {
    // Permission runs BEFORE the edit is applied, so the on-disk file is the
    // pre-edit content — a reliable diff with real line numbers.
    const diff = buildToolDiff(toolName, input, readFileSafe(input['file_path']))
    if (diff) return <DiffView diff={diff} />
  }
  if (toolName === 'Bash') {
    const cmd = str(input['command'])
    if (!cmd) return null
    let hl = cmd
    try {
      hl = highlight(cmd, { language: 'bash', ignoreIllegals: true })
    } catch {
      /* keep raw */
    }
    const rows = hl.split('\n')
    return (
      <Box marginTop={1} flexDirection="column">
        {rows.map((row, i) => (
          <Text key={i}>{row}</Text>
        ))}
      </Box>
    )
  }
  // Generic: a compact one-line preview of the most meaningful field.
  const main =
    str(input['file_path']) ||
    str(input['path']) ||
    str(input['url']) ||
    str(input['query']) ||
    str(input['pattern']) ||
    str(input['command'])
  if (!main) return null
  return (
    <Box marginTop={1}>
      <Text color={theme.dim}>{main.length > 200 ? `${main.slice(0, 199)}…` : main}</Text>
    </Box>
  )
}

export function PermissionDialog({ toolName, input }: Props): React.ReactElement {
  const { title, subtitle } = titleFor(toolName, input)
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
          {title}
        </Text>
        {subtitle ? <Text color={theme.dim}>{`  ${subtitle}`}</Text> : null}
      </Text>

      <Preview toolName={toolName} input={input} />

      <Box marginTop={1} flexDirection="column">
        <Text color={theme.dim}>Do you want to proceed?</Text>
        <Text>
          <Text color={theme.suggestion} bold>
            {'❯ '}
          </Text>
          <Text bold>1. Yes</Text>
          <Text color={theme.dim}>{'   (y)'}</Text>
        </Text>
        <Text color={theme.dim}>{'  2. Yes, and don’t ask again this session   (a)'}</Text>
        <Text color={theme.dim}>{'  3. No, and tell the agent what to do differently   (n / esc)'}</Text>
      </Box>
    </Box>
  )
}
