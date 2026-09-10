import { useEffect, useState } from 'react'

import { fetchSubagentTranscript } from '../state/actions.ts'
import { describeStatus, formatRunDuration, type SubagentEntry } from '../state/subagents.ts'
import { formatTokens } from '../state/trajectory.ts'
import { hydrateStoredMessages, type TranscriptNode } from '../state/transcript.ts'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import { ChatView } from './ChatView.tsx'
import css from './SubagentView.module.css'

/** How often a running subagent's record is re-read; it appends as it goes. */
const POLL_MS = 3_000

interface RecordState {
  nodes: TranscriptNode[]
  /** `missing` is a genuine miss: no file for this run, or no id to ask with. */
  state: 'idle' | 'loading' | 'missing' | 'ready'
}

export interface SubagentViewProps {
  entry: SubagentEntry
  workspace?: string
}

/**
 * One subagent's run, shown in the conversation column in place of the
 * session it belongs to: the prompt it was given, then everything it did —
 * from its own record when there is one, else the report it handed back.
 *
 * The record is the sidechain transcript the Agent tool writes as the run
 * goes, read through `subagent.transcript` and rehydrated with the same fold
 * as a resumed session, so a child's tool rows are the parent's tool rows. A
 * run that is still going is re-read on a slow tick; once it settles the
 * final read is the whole story.
 *
 * Read-only by construction. A one-shot delegation takes no follow-ups — the
 * way to steer it is the parent's composer, one click back.
 */
export function SubagentView({ entry, workspace }: SubagentViewProps) {
  const [record, setRecord] = useState<RecordState>({ nodes: [], state: 'idle' })
  const agentId = entry.agentId ?? ''
  const running = entry.status === 'running'

  useEffect(() => {
    if (agentId === '') {
      setRecord({ nodes: [], state: 'missing' })

      return
    }

    let live = true
    let first = true

    const load = () => {
      if (first) {
        first = false
        setRecord(current => ({ ...current, state: current.state === 'ready' ? 'ready' : 'loading' }))
      }

      void fetchSubagentTranscript(agentId).then(result => {
        if (!live) return

        const messages = result.messages ?? []

        if (!result.found || messages.length === 0) {
          // Keep rows already shown: a poll that misses mid-run is a hiccup,
          // not evidence the record vanished.
          setRecord(current => (current.state === 'ready' ? current : { nodes: [], state: 'missing' }))

          return
        }

        setRecord({ nodes: hydrateStoredMessages(messages), state: 'ready' })
      })
    }

    load()

    const timer = running ? setInterval(load, POLL_MS) : undefined

    return () => {
      live = false

      if (timer !== undefined) clearInterval(timer)
    }
    // Re-read when the run settles: the file is complete only then.
  }, [agentId, running])

  const facts = [
    describeStatus(entry.status),
    entry.toolCount === undefined
      ? undefined
      : `${entry.toolCount} ${entry.toolCount === 1 ? 'tool call' : 'tool calls'}`,
    entry.tokens === undefined ? undefined : `${formatTokens(entry.tokens)} tok`,
    entry.durationMs === undefined ? undefined : formatRunDuration(entry.durationMs),
    entry.model,
  ].filter((part): part is string => part !== undefined && part !== '')

  const showReport = record.state !== 'ready' && !running && entry.report !== undefined

  return (
    <div className={css.root}>
      <div className={css.column}>
        {entry.prompt !== undefined && (
          <div className={css.promptRow}>
            {/* The prompt the parent wrote for this run, in the user's seat:
                to the subagent, that IS the user. */}
            <div className={css.prompt}>{entry.prompt}</div>
          </div>
        )}
        {record.state === 'ready' && (
          <ChatView nodes={record.nodes} running={running} workspace={workspace} />
        )}
        {record.state === 'loading' && record.nodes.length === 0 && (
          <div className={css.status}>Loading the run…</div>
        )}
        {running && record.state !== 'ready' && record.state !== 'loading' && (
          <div className={css.status}>
            Working
            {entry.activity !== undefined && ` · ${entry.activity}`}
          </div>
        )}
        {showReport && (
          <div className={css.report}>
            <Markdown text={entry.report ?? ''} />
          </div>
        )}
        {record.state === 'missing' && !running && entry.report === undefined && (
          <div className={css.status}>No record of this run was kept.</div>
        )}
        {facts.length > 0 && <div className={css.facts}>{facts.join(' · ')}</div>}
      </div>
      <div className={css.foot}>
        <div className={css.readOnly}>
          <span className={css.readOnlyTitle}>This subagent is read-only.</span>
          <span className={css.readOnlyBody}>
            A delegated run takes no follow-ups; to steer it, write to the session it belongs to.
          </span>
        </div>
      </div>
    </div>
  )
}
