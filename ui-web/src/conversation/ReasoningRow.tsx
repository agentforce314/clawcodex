import { memo, useState } from 'react'

import { BrainIcon } from '../ui/icons.tsx'
import { DisclosureRow } from '../ui/primitives/DisclosureRow.tsx'
import type { ReasoningNode } from '../state/transcript.ts'
import css from './ReasoningRow.module.css'

export interface ReasoningRowProps {
  node: ReasoningNode
}

/** Characters of the live tail shown beside the title while thinking. */
const TAIL = 160

/**
 * The model's reasoning, as one collapsed row.
 *
 * Live, the summary follows the newest words so the reader can see thinking
 * happen; sealed, it collapses to the first line and the full text is one
 * click away. Reasoning is context, not the answer — it never takes the
 * transcript's full width by default.
 */
function ReasoningRowImpl({ node }: ReasoningRowProps) {
  const [expanded, setExpanded] = useState(false)
  const live = !node.sealed
  const flat = node.text.replace(/\s+/g, ' ').trim()
  const summary = live ? flat.slice(-TAIL) : flat

  return (
    <DisclosureRow
      body={<div className={css.body}>{node.text}</div>}
      expanded={expanded}
      icon={<BrainIcon size={14} />}
      onToggle={() => {
        setExpanded(value => !value)
      }}
      state={live ? 'running' : undefined}
      summary={live ? <span className={css.summaryLive}>{summary}</span> : summary}
      title={live ? 'Thinking' : 'Thought'}
    />
  )
}

/** Memoized on the node, like the other flow rows. */
export const ReasoningRow = memo(ReasoningRowImpl)
