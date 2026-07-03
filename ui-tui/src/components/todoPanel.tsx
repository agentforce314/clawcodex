import { Box, Text } from '@clawcodex/ink'
import { memo, useState } from 'react'

import { countPendingTodos } from '../lib/liveProgress.js'
import { todoGlyph } from '../lib/todo.js'
import type { Theme } from '../theme.js'
import type { TodoItem } from '../types.js'

// Original TaskListV2 icon colors: ✔ success-green, ◼ claude-orange,
// ◻ default text (cancelled dims via the row style).
const iconColor = (t: Theme, status: TodoItem['status']) =>
  status === 'completed' ? t.color.ok : status === 'in_progress' ? t.color.accent : t.color.text

// Cap the visible list like the original HUD; the summary row carries the rest.
const MAX_VISIBLE_TODOS = 10

export const TodoPanel = memo(function TodoPanel({
  collapsed,
  defaultCollapsed = false,
  incomplete = false,
  onToggle,
  t,
  todos
}: {
  collapsed?: boolean
  defaultCollapsed?: boolean
  incomplete?: boolean
  onToggle?: () => void
  t: Theme
  todos: TodoItem[]
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
  const pending = countPendingTodos(todos)

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

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box onClick={handleToggle}>
        <Text color={t.color.muted}>
          <Text color={t.color.accent}>{effectiveCollapsed ? '▸ ' : '▾ '}</Text>
          <Text bold>{todos.length}</Text> {todos.length === 1 ? 'task' : 'tasks'}{' '}
          <Text color={t.color.statusFg} dim>
            ({headerCounts})
          </Text>
          {incomplete && pending > 0 && (
            <Text color={t.color.muted} dim>
              {' '}
              · incomplete · {pending} still {pending === 1 ? 'pending' : 'pending/in_progress'}
            </Text>
          )}
        </Text>
      </Box>

      {!effectiveCollapsed && (
        <Box flexDirection="column" marginLeft={2}>
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
        </Box>
      )}
    </Box>
  )
})
