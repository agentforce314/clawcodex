import { memo, useState, type ReactNode } from 'react'

import {
  AgentIcon,
  FilePenIcon,
  FileTextIcon,
  GlobeIcon,
  HelpIcon,
  LayersIcon,
  ListIcon,
  SearchIcon,
  TerminalIcon,
  WrenchIcon,
} from '../ui/icons.tsx'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import { DiffBlock } from '../ui/primitives/DiffBlock.tsx'
import { DisclosureRow } from '../ui/primitives/DisclosureRow.tsx'
import { IoCard } from '../ui/primitives/IoCard.tsx'
import { OutputBlock } from '../ui/primitives/OutputBlock.tsx'
import { ReadBlock } from '../ui/primitives/ReadBlock.tsx'
import { TerminalBlock } from '../ui/primitives/TerminalBlock.tsx'
import { WebBlock } from '../ui/primitives/WebBlock.tsx'
import { openFile } from '../sidebar-right/store.ts'
import { openSubagent } from '../state/actions.ts'
import { formatRunDuration, type SubagentEntry } from '../state/subagents.ts'
import type { ToolNode } from '../state/transcript.ts'
import {
  describeTool,
  formatArgs,
  genericBodyText,
  prettyMaybeJson,
  readTaskEntries,
  toolFileLine,
  toolFilePath,
  readTodos,
  synthesizeDiff,
  type ToolIconName,
  type TodoEntry,
} from './tool-view.ts'
import css from './ToolRow.module.css'

const ICON_COMPONENTS: Record<ToolIconName, (props: { size?: number }) => ReactNode> = {
  agent: AgentIcon,
  edit: FilePenIcon,
  file: FileTextIcon,
  globe: GlobeIcon,
  help: HelpIcon,
  layers: LayersIcon,
  list: ListIcon,
  search: SearchIcon,
  terminal: TerminalIcon,
  tool: WrenchIcon,
}

/**
 * A delegation's body: what the agent was told, then what it said back.
 *
 * The report is markdown — it is the agent's answer, written for a reader
 * the same way the assistant's prose is — and the prompt sits above it,
 * folded into a plain block, because it is the only record of the task. The
 * way into the run itself (its own tool rows) is the button at the end.
 */
function AgentBody({ entry, node }: { entry?: SubagentEntry; node: ToolNode }) {
  const prompt = typeof node.args.prompt === 'string' ? node.args.prompt : ''
  const report = entry?.report ?? genericBodyText(node)

  return (
    <div className={css.agentBody}>
      {prompt !== '' && <OutputBlock className={css.agentPrompt} label="prompt" text={prompt} />}
      {node.error !== undefined ? (
        <OutputBlock className={css.agentPrompt} label="error" text={node.error} tone="error" />
      ) : (
        report !== '' && (
          <div className={css.agentReport}>
            <Markdown text={report} />
          </div>
        )
      )}
      {entry !== undefined && (
        <button
          className={css.agentOpen}
          onClick={() => {
            openSubagent(entry.key)
          }}
          type="button"
        >
          Open this subagent
        </button>
      )}
    </div>
  )
}

/** `3 tools · 42s` once settled; what the agent is doing while it runs. */
function agentTrailing(entry: SubagentEntry | undefined): string {
  if (entry === undefined) return ''

  if (entry.status === 'running') {
    const parts = [entry.activity, entry.toolCount === undefined ? undefined : `${entry.toolCount} tools`]

    return parts.filter((part): part is string => part !== undefined && part !== '').join(' · ')
  }

  const parts = [
    entry.toolCount === undefined ? undefined : `${entry.toolCount} ${entry.toolCount === 1 ? 'tool' : 'tools'}`,
    entry.durationMs === undefined ? undefined : formatRunDuration(entry.durationMs),
  ]

  return parts.filter((part): part is string => part !== undefined).join(' · ')
}

function TodoBody({ todos }: { todos: TodoEntry[] }) {
  return (
    <ul className={css.todoList}>
      {todos.map((todo, index) => (
        <li className={css.todoItem} data-status={todo.status} key={index}>
          <span
            className={[
              css.todoGlyph,
              todo.status === 'completed' ? css.todoDone : '',
              todo.status === 'in_progress' ? css.todoActive : '',
            ]
              .filter(Boolean)
              .join(' ')}
          >
            {todo.status === 'completed' ? '✓' : todo.status === 'in_progress' ? '◐' : '○'}
          </span>
          <span className={css.todoText}>{todo.content}</span>
        </li>
      ))}
    </ul>
  )
}

export interface ToolRowProps {
  /** For an Agent row: the catalog entry its call became, live figures included. */
  agent?: SubagentEntry
  node: ToolNode
  workspace?: string
}

/**
 * One tool call in the flow: a 24px title row that discloses the call's own
 * card — a terminal transcript, a diff, a numbered file window, a web card
 * with followable links, or the generic IN/OUT card.
 *
 * A running call shows its row (with the sweep) and no body: there is nothing
 * to disclose yet, and reserving space for a card that has not arrived makes
 * the flow jump twice instead of once.
 */
function ToolRowImpl({ agent, node, workspace }: ToolRowProps) {
  const [expanded, setExpanded] = useState(false)
  const view = describeTool(node, workspace)
  const Icon = ICON_COMPONENTS[view.icon]
  const running = node.state === 'running'
  const failed = node.state === 'error'
  const trailing = view.body === 'agent' ? agentTrailing(agent) : ''

  // The checklist card serves two families: TodoWrite carries its list in the
  // ARGUMENTS, the task registry's TaskList in its result JSON.
  const todos =
    view.body === 'todo'
      ? node.name === 'TaskList'
        ? readTaskEntries(node)
        : readTodos(node.args)
      : []
  const generic = genericBodyText(node)
  const diff = view.body === 'diff' ? synthesizeDiff(node) : undefined

  let body: ReactNode

  if (running) {
    body = undefined
  } else if (view.body === 'agent') {
    // Settled either way: a failed delegation keeps its shape, with the
    // failure where the report would be.
    body = <AgentBody entry={agent} node={node} />
  } else if (failed) {
    // A failed terminal keeps its terminal shape — the error text is what the
    // command printed. Everything else shows the exchange, arguments first:
    // for an unknown tool they are the only record of what was asked.
    const command = typeof node.args.command === 'string' ? node.args.command : ''

    if (node.name === 'terminal' && command !== '') {
      body = (
        <TerminalBlock
          className={[css.body, css.terminalBody].join(' ')}
          command={command}
          output={node.error ?? 'Tool execution failed'}
          state="error"
        />
      )
    } else {
      const input = formatArgs(node.args)

      body = (
        <IoCard
          className={css.body}
          input={input === '' ? undefined : input}
          output={node.error ?? 'Tool execution failed'}
          tone="error"
        />
      )
    }
  } else if (view.body === 'terminal') {
    body = (
      <TerminalBlock
        className={[css.body, css.terminalBody].join(' ')}
        command={typeof node.args.command === 'string' ? node.args.command : view.summary}
        output={node.result?.output ?? ''}
        state="done"
      />
    )
  } else if (view.body === 'diff' && diff !== undefined) {
    body = <DiffBlock className={css.body} diff={diff} />
  } else if (view.body === 'read' && node.result?.content !== undefined) {
    body = <ReadBlock className={css.body} content={node.result.content} label={view.path} />
  } else if (view.body === 'todo' && todos.length > 0) {
    body = <TodoBody todos={todos} />
  } else if (view.body === 'web' && generic !== '') {
    body = (
      <WebBlock
        className={css.body}
        text={generic}
        url={typeof node.args.url === 'string' ? node.args.url : undefined}
      />
    )
  } else if (view.body === 'io') {
    const input = formatArgs(node.args)

    if (input !== '' || generic !== '') {
      body = (
        <IoCard
          className={css.body}
          input={input === '' ? undefined : input}
          output={prettyMaybeJson(generic)}
        />
      )
    }
  } else if (generic !== '') {
    body = <OutputBlock className={css.body} label="output" text={generic} />
  }

  // A file tool's path replaces the summary: it is the one thing worth reading
  // on that row, and it deserves the monospace treatment a summary does not.
  // It is also the way into the file — clicking opens it in the sidebar, at the
  // line a `read` started from, so the row is a reference and not just a label.
  const filePath = toolFilePath(node, workspace)
  const summary =
    view.path !== undefined && view.path !== '' ? (
      filePath === '' ? (
        <span className={css.path} title={view.path}>
          {view.path}
        </span>
      ) : (
        <button
          className={[css.path, css.pathLink].join(' ')}
          onClick={() => {
            openFile(filePath, toolFileLine(node))
          }}
          title={`Open ${filePath}`}
          type="button"
        >
          {view.path}
        </button>
      )
    ) : (
      view.summary
    )

  return (
    <div className={css.root}>
      <DisclosureRow
        body={body}
        expanded={expanded}
        icon={<Icon size={14} />}
        onToggle={
          body === undefined
            ? undefined
            : () => {
                setExpanded(value => !value)
              }
        }
        state={running ? 'running' : failed ? 'error' : 'done'}
        summary={summary}
        summaryTone={failed ? 'error' : 'default'}
        title={view.title}
        trailing={trailing === '' ? undefined : <span className={css.trailing}>{trailing}</span>}
      />
    </div>
  )
}

/** Memoized on the node: a settled row must not re-render on every delta. */
export const ToolRow = memo(ToolRowImpl)
