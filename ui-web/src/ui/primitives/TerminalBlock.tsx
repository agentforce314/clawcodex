import { useMemo, useState } from 'react'

import { CopyButton } from './CopyButton.tsx'
import { StateDot, type RunState } from './StateDot.tsx'
import css from './TerminalBlock.module.css'

export interface TerminalBlockProps {
  className?: string
  command: string
  cwd?: string
  output?: string
  state: RunState
}

/** Lines shown before the "show all" affordance; long output is the norm. */
const HEAD_LINES = 40

export function TerminalBlock({ className, command, cwd, output, state }: TerminalBlockProps) {
  const [expanded, setExpanded] = useState(false)

  const lines = useMemo(() => (output ?? '').replace(/\n$/, '').split('\n'), [output])
  const overflowing = lines.length > HEAD_LINES && !expanded
  const shown = overflowing ? lines.slice(0, HEAD_LINES) : lines
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
            {shown.map((line, index) => (
              // Output lines have no identity of their own; the index is the
              // only stable key and the list is append-only in practice.
              <div className={css.line} key={index}>
                {line === '' ? ' ' : line}
              </div>
            ))}
            {overflowing && (
              <button
                className={css.expand}
                onClick={() => {
                  setExpanded(true)
                }}
                type="button"
              >
                … show {lines.length - HEAD_LINES} more lines
              </button>
            )}
          </div>
        ))}
    </div>
  )
}
