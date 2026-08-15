import { memo, useState } from 'react'

import { BrainIcon } from '../ui/icons.tsx'
import { DisclosureRow } from '../ui/primitives/DisclosureRow.tsx'
import type { ReasoningNode } from '../state/transcript.ts'
import css from './ReasoningRow.module.css'

export interface ReasoningRowProps {
  node: ReasoningNode
}

/**
 * The model's reasoning.
 *
 * LIVE, it is a small window onto the tail of the thought: the last few
 * lines, wrapped and bottom-anchored, growing the way a terminal grows. The
 * earlier design followed the newest words on a single collapsed line, which
 * meant every delta shifted the whole line left — motion nobody can read.
 * Words appearing at a reading pace under a stable header is the TUI's
 * streaming-thought presentation, sized for the web column.
 *
 * SEALED, it collapses to one quiet row — first line as the summary, the
 * full text one click away. Reasoning is context, not the answer; it never
 * holds the transcript's full height once the thought is finished.
 */
function ReasoningRowImpl({ node }: ReasoningRowProps) {
  const [expanded, setExpanded] = useState(false)
  const live = !node.sealed

  if (live) {
    return (
      <div className={css.liveRoot}>
        <DisclosureRow icon={<BrainIcon size={14} />} state="running" title="Thinking" />
        <div aria-live="off" className={css.liveWindow}>
          <div className={css.liveText}>{node.text}</div>
        </div>
      </div>
    )
  }

  const summary = (node.text.split('\n').find(line => line.trim() !== '') ?? '').trim()

  return (
    <DisclosureRow
      body={<div className={css.body}>{node.text}</div>}
      expanded={expanded}
      icon={<BrainIcon size={14} />}
      onToggle={() => {
        setExpanded(value => !value)
      }}
      summary={summary}
      title="Thought"
    />
  )
}

/** Memoized on the node, like the other flow rows. */
export const ReasoningRow = memo(ReasoningRowImpl)
