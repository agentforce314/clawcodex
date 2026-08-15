import { CopyButton } from './CopyButton.tsx'
import css from './IoCard.module.css'

export interface IoCardProps {
  className?: string
  /** The call's arguments, already formatted for reading. */
  input?: string
  /** The call's result text; empty renders as an explicit "(no output)". */
  output?: string
  tone?: 'default' | 'error'
}

/**
 * The generic tool card: both sides of the exchange, labelled IN and OUT.
 *
 * A tool with no dedicated shape (Task, an MCP tool) used to show its output
 * alone — but for exactly those tools the *arguments* are the only record of
 * what was asked, and a failed call is unreadable without them. Each side
 * scrolls in its own window with its label held sticky, so a long prompt
 * cannot push the result out of reach.
 */
export function IoCard({ className, input, output, tone = 'default' }: IoCardProps) {
  const hasInput = input !== undefined && input !== ''

  return (
    <div className={[css.card, className].filter(Boolean).join(' ')}>
      {hasInput && (
        <div className={css.section}>
          <span className={css.label}>IN</span>
          <pre className={css.text}>{input}</pre>
        </div>
      )}
      {hasInput && <span aria-hidden="true" className={css.divider} />}
      <div className={css.section}>
        <span className={css.label}>OUT</span>
        <div className={css.outWrap}>
          <pre className={css.text} data-error={tone === 'error' ? '' : undefined}>
            {output === undefined || output === '' ? '(no output)' : output}
          </pre>
          {output !== undefined && output !== '' && (
            <CopyButton className={css.copyButton} text={output} />
          )}
        </div>
      </div>
    </div>
  )
}
