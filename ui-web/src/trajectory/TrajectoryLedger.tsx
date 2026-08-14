import { memo } from 'react'

import type { TrajectoryRecord } from '../state/trajectory.ts'
import css from './TrajectoryLedger.module.css'

const KIND_LABEL: Record<TrajectoryRecord['kind'], string> = {
  assistant: 'ASSISTANT',
  notice: 'ERROR',
  tool: 'TOOL',
  user: 'USER',
}

const KIND_CLASS: Record<TrajectoryRecord['kind'], string | undefined> = {
  assistant: css.assistant,
  notice: css.notice,
  tool: css.tool,
  user: css.user,
}

/** Longest inline preview of a tool result before it is cut. */
const RESULT_PREVIEW = 400

function oneLine(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()

  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat
}

/**
 * A tool call as one line: the name, then its arguments as compact JSON.
 *
 * JSON rather than a prettified summary because this is the forensic view —
 * the question it answers is "what exactly was this called with", and a
 * friendly rendering of `{"command": "..."}` loses the quoting that usually
 * turns out to be the bug.
 */
function toolCallLine(record: TrajectoryRecord): string {
  const name = record.toolName ?? 'tool'
  const args = record.args ?? {}

  if (Object.keys(args).length === 0) return name

  try {
    return `${name} ${oneLine(JSON.stringify(args), 600)}`
  } catch {
    // Circular or non-serialisable input: the summary still names the call.
    return `${name} ${record.text}`
  }
}

function resultLine(record: TrajectoryRecord): string {
  if (record.error !== undefined) return oneLine(record.error, RESULT_PREVIEW)

  const result = record.result

  if (result === undefined) return ''

  const text =
    result.output ?? result.content ?? result.message ?? result.context ?? result.inline_diff ?? ''

  if (text !== '') return oneLine(text, RESULT_PREVIEW)

  if (result.file_count !== undefined) return `${result.file_count} files`
  if (result.match_count !== undefined) return `${result.match_count} matches`

  return '(no output)'
}

export interface LedgerRowProps {
  /** Collapsed: this row hides others, and says how many. */
  foldCount: number | null
  folded: boolean
  isDim: boolean
  isMatch: boolean
  onSelect: (index: number) => void
  onToggleFold: (record: TrajectoryRecord) => void
  record: TrajectoryRecord
  selected: boolean
  /** First row of its turn: carries the turn marker. */
  turnStart: boolean
}

function LedgerRowImpl({
  foldCount,
  folded,
  isDim,
  isMatch,
  onSelect,
  onToggleFold,
  record,
  selected,
  turnStart,
}: LedgerRowProps) {
  const isTool = record.kind === 'tool'
  const running = isTool && record.endedAt === null
  const result = isTool ? resultLine(record) : ''

  return (
    <tr
      className={css.row}
      data-dim={isDim ? 'true' : undefined}
      data-match={isMatch ? 'true' : undefined}
      data-selected={selected ? 'true' : undefined}
      onClick={() => {
        onSelect(record.index)
      }}
    >
      <td className={css.event}>
        {turnStart && <span className={css.turnLabel}>Turn {record.turn}</span>}
        <div className={css.eventInner}>
          {foldCount !== null && (
            <button
              aria-label={folded ? 'Expand' : 'Collapse'}
              className={css.caret}
              onClick={event => {
                event.stopPropagation()
                onToggleFold(record)
              }}
              type="button"
            >
              {folded ? '▸' : '▾'}
            </button>
          )}
          <span className={css.kindSlot}>
            <span className={[css.kindTag, KIND_CLASS[record.kind]].join(' ')}>
              {KIND_LABEL[record.kind]}
            </span>
          </span>
        </div>
      </td>
      <td className={css.content}>
        <span className={css.contentText}>
          {isTool ? (
            <>
              <span className={css.mono}>{toolCallLine(record)}</span>
              {running ? (
                <span className={css.pending}> · running…</span>
              ) : (
                result !== '' && (
                  <>
                    <span className={css.resultArrow}>→</span>
                    <span
                      className={[css.mono, record.isError === true ? css.resultError : css.resultText].join(
                        ' ',
                      )}
                    >
                      {result}
                    </span>
                  </>
                )
              )}
            </>
          ) : (
            record.text || <span className={css.dim}>(empty)</span>
          )}
          {folded && foldCount !== null && (
            <span className={css.foldSummary}> · {foldCount} hidden</span>
          )}
        </span>
      </td>
    </tr>
  )
}

/**
 * Memoized per record: a streaming turn re-renders the ledger on every delta,
 * and only the row being written to actually changed.
 */
export const LedgerRow = memo(LedgerRowImpl)

export interface TrajectoryLedgerProps {
  focusIndexes: ReadonlySet<number> | null
  foldCounts: ReadonlyMap<number, number>
  foldedIndexes: ReadonlySet<number>
  matchIndexes: ReadonlySet<number> | null
  onSelect: (index: number) => void
  onToggleFold: (record: TrajectoryRecord) => void
  records: readonly TrajectoryRecord[]
  selectedIndex: number | null
  turnStarts: ReadonlySet<number>
}

export function TrajectoryLedger({
  focusIndexes,
  foldCounts,
  foldedIndexes,
  matchIndexes,
  onSelect,
  onToggleFold,
  records,
  selectedIndex,
  turnStarts,
}: TrajectoryLedgerProps) {
  if (records.length === 0) {
    return (
      <div className={css.root}>
        <div className={css.empty}>
          Nothing recorded yet. Send a prompt and the run appears here, step by step.
        </div>
      </div>
    )
  }

  return (
    <div className={css.root}>
      <table className={css.table}>
        <colgroup>
          <col className={css.eventColumn} />
          <col className={css.contentColumn} />
        </colgroup>
        <tbody>
          {records.map(record => (
            <LedgerRow
              foldCount={foldCounts.get(record.index) ?? null}
              folded={foldedIndexes.has(record.index)}
              isDim={focusIndexes !== null && !focusIndexes.has(record.index)}
              isMatch={matchIndexes?.has(record.index) === true}
              key={record.id}
              onSelect={onSelect}
              onToggleFold={onToggleFold}
              record={record}
              selected={record.index === selectedIndex}
              turnStart={turnStarts.has(record.index)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
