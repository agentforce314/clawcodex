import { memo, useState } from 'react'

import { ChevronDownIcon, ChevronRightIcon, ListIcon } from '../ui/icons.tsx'
import {
  countTodos,
  type TodoProgressItem,
  type TodoProgressStatus,
} from '../state/todo-progress.ts'
import css from './TodoPanel.module.css'

/**
 * The three checklist glyphs, drawn rather than typed: a ring with a check
 * (done), an open arc that spins (in flight), a dashed ring (queued). The
 * reference client's task panel uses exactly this vocabulary, and it reads at
 * a glance in a way ✓/◐/○ text glyphs do not.
 */
function Glyph({ status }: { status: TodoProgressStatus }) {
  if (status === 'completed') {
    return (
      <svg aria-hidden="true" className={css.glyphDone} fill="none" height="14" viewBox="0 0 14 14" width="14">
        <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1.4" />
        <path d="M4.4 7.2l1.8 1.8 3.4-3.9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.4" />
      </svg>
    )
  }

  if (status === 'in_progress') {
    return (
      <svg aria-hidden="true" className={css.glyphActive} fill="none" height="14" viewBox="0 0 14 14" width="14">
        {/* Circumference ≈ 37.7; the gap makes the arc read as motion. */}
        <circle cx="7" cy="7" r="6" stroke="currentColor" strokeDasharray="26 11.7" strokeLinecap="round" strokeWidth="1.6" />
      </svg>
    )
  }

  return (
    <svg aria-hidden="true" className={css.glyphPending} fill="none" height="14" viewBox="0 0 14 14" width="14">
      <circle cx="7" cy="7" r="6" stroke="currentColor" strokeDasharray="2.2 2.9" strokeWidth="1.4" />
    </svg>
  )
}

function countsLabel(todos: readonly TodoProgressItem[]): string {
  const counts = countTodos(todos)
  const parts: string[] = []

  if (counts.completed > 0) parts.push(`${counts.completed} completed`)
  if (counts.inProgress > 0) parts.push(`${counts.inProgress} in progress`)
  if (counts.pending > 0) parts.push(`${counts.pending} pending`)

  return parts.join(' · ')
}

export interface TodoPanelProps {
  todos: TodoProgressItem[]
}

/**
 * Where the work stands NOW: the session's current checklist as a card docked
 * above the composer, fed by TodoWrite and the task registry alike.
 *
 * The transcript's tool rows say what each call did, in order; this panel is
 * the running total, always in reach without scrolling back. It appears once
 * a checklist exists and collapses to its header line on demand.
 */
function TodoPanelImpl({ todos }: TodoPanelProps) {
  // null = the reader has not chosen; the panel then decides for itself:
  // open while work is in flight, folded to its header once every item is
  // done — a finished checklist is a receipt, not something to keep reading.
  const [choice, setChoice] = useState<boolean | null>(null)

  if (todos.length === 0) return null

  const allDone = todos.every(todo => todo.status === 'completed')
  const collapsed = choice ?? allDone

  return (
    <section aria-label="To-dos" className={css.panel}>
      <button
        aria-expanded={!collapsed}
        className={css.header}
        onClick={() => {
          setChoice(!collapsed)
        }}
        type="button"
      >
        <span className={css.headerIcon}>
          <ListIcon size={14} />
        </span>
        <span className={css.title}>To-dos</span>
        <span className={css.counts}>{countsLabel(todos)}</span>
        <span className={css.chevron}>
          {collapsed ? <ChevronRightIcon size={14} /> : <ChevronDownIcon size={14} />}
        </span>
      </button>
      {!collapsed && (
        <ul className={css.list}>
          {todos.map((todo, index) => (
            <li className={css.item} data-status={todo.status} key={todo.id ?? `${index}-${todo.content}`}>
              <span className={css.glyph}>
                <Glyph status={todo.status} />
              </span>
              <span className={css.text}>{todo.content}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export const TodoPanel = memo(TodoPanelImpl)
