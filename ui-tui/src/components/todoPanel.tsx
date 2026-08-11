import { Box, Text } from '@clawcodex/ink'
import { memo, useState } from 'react'

import { todoGlyph } from '../lib/todo.js'
import type { Theme } from '../theme.js'
import type { TodoItem } from '../types.js'

// Original TaskListV2 icon colors: ✔ success-green, ◼ claude-orange,
// ◻ default text (cancelled dims via the row style).
const iconColor = (t: Theme, status: TodoItem['status']) =>
  status === 'completed' ? t.color.ok : status === 'in_progress' ? t.color.accent : t.color.text

// Cap the visible list like the original HUD; the summary row carries the rest.
const MAX_VISIBLE_TODOS = 10

// The original's MessageResponse lead — two spaces, └, two spaces
// (MessageResponse.tsx:22). While busy, the whole list hangs off the busy
// line through this connector: └ beside the first row, every later row
// aligned under it by the flex-row split.
const ATTACHED_LEAD = '  └  '

export const TodoPanel = memo(function TodoPanel({
  collapsed,
  defaultCollapsed = false,
  marginBottom = 0,
  onToggle,
  t,
  todos,
  variant = 'standalone'
}: {
  collapsed?: boolean
  defaultCollapsed?: boolean
  marginBottom?: number
  onToggle?: () => void
  t: Theme
  todos: TodoItem[]
  /** `standalone` (default): the original's isStandalone render — count
   *  header + rows. Used idle and in the transcript archive. `attached`:
   *  the busy-turn render (Spinner.tsx:275) — no header, rows hanging off
   *  the busy line via the `  └  ` connector. */
  variant?: 'attached' | 'standalone'
}) {
  // Fallback local state for archived todos in transcript where there's no
  // external controller. Live TodoPanel passes collapsed+onToggle from the
  // turn store so clicks still work there.
  const [localCollapsed, setLocalCollapsed] = useState(defaultCollapsed)
  const isControlled = typeof collapsed === 'boolean'
  const effectiveCollapsed = isControlled ? collapsed : localCollapsed

  const handleToggle = () => {
    if (onToggle) {
      onToggle()

      return
    }

    if (!isControlled) {
      setLocalCollapsed(v => !v)
    }
  }

  if (!todos.length) {
    return null
  }

  const done = todos.filter(todo => todo.status === 'completed').length
  const inProgress = todos.filter(todo => todo.status === 'in_progress').length
  const open = todos.length - done - inProgress

  // Original standalone header: "N tasks (X done, Y in progress, Z open)".
  const headerCounts = [
    `${done} done`,
    ...(inProgress > 0 ? [`${inProgress} in progress`] : []),
    `${open} open`
  ].join(', ')

  const visible = todos.slice(0, MAX_VISIBLE_TODOS)
  const hidden = todos.slice(MAX_VISIBLE_TODOS)

  const hiddenSummary =
    hidden.length > 0
      ? ` … +${hidden.filter(todo => todo.status === 'in_progress').length} in progress, ${hidden.filter(todo => todo.status === 'pending').length} pending, ${hidden.filter(todo => todo.status === 'completed').length} completed`
      : ''

  const rows = (
    <>
      {visible.map(todo => {
        const isDone = todo.status === 'completed'
        const isActive = todo.status === 'in_progress'
        const isCancelled = todo.status === 'cancelled'

        return (
          <Text color={t.color.text} key={todo.id}>
            <Text color={iconColor(t, todo.status)}>{todoGlyph(todo.status)} </Text>
            <Text bold={isActive} dimColor={isDone || isCancelled} strikethrough={isDone || isCancelled}>
              {todo.content}
            </Text>
          </Text>
        )
      })}
      {hiddenSummary ? (
        <Text color={t.color.muted} dim>
          {hiddenSummary}
        </Text>
      ) : null}
    </>
  )

  if (variant === 'attached') {
    return (
      <Box flexDirection="row" marginBottom={marginBottom}>
        <Box flexShrink={0} width={ATTACHED_LEAD.length}>
          <Text color={t.color.muted} dim>
            {ATTACHED_LEAD}
          </Text>
        </Box>
        <Box flexDirection="column" flexGrow={1}>
          {rows}
        </Box>
      </Box>
    )
  }

  return (
    <Box flexDirection="column" marginBottom={marginBottom}>
      <Box onClick={handleToggle}>
        <Text color={t.color.muted}>
          <Text color={t.color.accent}>{effectiveCollapsed ? '▸ ' : '▾ '}</Text>
          <Text bold>{todos.length}</Text> {todos.length === 1 ? 'task' : 'tasks'}{' '}
          <Text color={t.color.statusFg} dim>
            ({headerCounts})
          </Text>
        </Text>
      </Box>

      {!effectiveCollapsed && (
        <Box flexDirection="column" marginLeft={2}>
          {rows}
        </Box>
      )}
    </Box>
  )
})
