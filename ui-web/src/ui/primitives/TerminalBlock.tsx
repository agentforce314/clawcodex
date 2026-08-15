import { useMemo, useState } from 'react'

import { parseAnsi } from '../ansi.ts'
import { AnsiSpans } from './AnsiText.tsx'
import { CopyButton } from './CopyButton.tsx'
import { StateDot, type RunState } from './StateDot.tsx'
import { headTailCap, splitByCap } from './head-tail-cap.ts'
import css from './TerminalBlock.module.css'

export interface TerminalBlockProps {
  className?: string
  command: string
  cwd?: string
  output?: string
  state: RunState
}

/** Visible lines when capped; the fold sits mid-card so the tail stays. */
const MAX_LINES = 40

export function TerminalBlock({ className, command, cwd, output, state }: TerminalBlockProps) {
  const [expanded, setExpanded] = useState(false)

  // ANSI is parsed once for the whole output: colors set on one line style the
  // next, so per-line parsing would drop every multi-line span.
  const lines = useMemo(() => parseAnsi(output ?? ''), [output])
  const cap = headTailCap(lines.length, MAX_LINES)
  const folded = cap.hidden > 0 && !expanded
  const { head, tail } = folded ? splitByCap(lines, cap) : { head: lines, tail: [] }
  const running = state === 'running'

  return (
    <div
      className={[css.block, className].filter(Boolean).join(' ')}
      data-running={running ? '' : undefined}
    >
      <div className={css.header}>
        <div className={css.prompt}>
          <div className={css.promptLine}>
            <span className={css.runState}>
              <StateDot label={running ? 'Running' : state} state={state} />
            </span>
            {cwd !== undefined && cwd !== '' && <span className={css.cwd}>{cwd}</span>}
            <span className={css.command}>{command}</span>
          </div>
        </div>
        <CopyButton className={css.copyButton} text={command} />
      </div>
      {!running &&
        (output === undefined || output === '' ? (
          <div className={css.empty}>(no output)</div>
        ) : (
          <div className={css.output}>
            {head.map((line, index) => (
              // Output lines have no identity of their own; the index is the
              // only stable key and the list is append-only in practice.
              <div className={css.line} key={index}>
                <AnsiSpans line={line} />
              </div>
            ))}
            {cap.hidden > 0 && (
              <button
                aria-expanded={expanded}
                className={css.expand}
                onClick={() => {
                  setExpanded(value => !value)
                }}
                type="button"
              >
                {expanded ? 'collapse' : `… ${cap.hidden} more lines`}
              </button>
            )}
            {tail.map((line, index) => (
              <div className={css.line} key={`tail-${index}`}>
                <AnsiSpans line={line} />
              </div>
            ))}
          </div>
        ))}
    </div>
  )
}
