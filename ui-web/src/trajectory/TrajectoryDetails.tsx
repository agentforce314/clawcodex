import { useState } from 'react'

import {
  formatMs,
  stepMetrics,
  stepTtft,
  type TrajectoryRecord,
} from '../state/trajectory.ts'
import { promptTokens } from '../gateway/protocol.ts'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import { XIcon } from '../ui/icons.tsx'
import css from './TrajectoryDetails.module.css'

type DetailTab = 'preview' | 'raw' | 'summary'

const KIND_LABEL: Record<TrajectoryRecord['kind'], string> = {
  assistant: 'ASSISTANT',
  notice: 'ERROR',
  tool: 'TOOL',
  user: 'USER',
}

/** Readings the recorder returns as prose when it could not take one. */
const ABSENT = new Set([
  'Duration too short',
  'First token unavailable',
  'Not recorded',
  'Pending',
  'Step start unavailable',
  'Usage unavailable',
])

function Fact({ label, nested, value }: { label: string; nested?: boolean; value: string }) {
  return (
    <>
      <dt data-nested={nested === true ? '' : undefined}>{label}</dt>
      <dd data-absent={ABSENT.has(value) ? '' : undefined}>{value}</dd>
    </>
  )
}

function rawPayload(record: TrajectoryRecord): string {
  const payload: Record<string, unknown> = {
    index: record.index,
    kind: record.kind,
    step: record.step,
    turn: record.turn,
  }

  if (record.toolName !== undefined) payload.tool = record.toolName
  if (record.callId !== undefined) payload.call_id = record.callId
  if (record.args !== undefined) payload.args = record.args
  if (record.result !== undefined) payload.result = record.result
  if (record.error !== undefined) payload.error = record.error
  if (record.metrics !== undefined) payload.metrics = record.metrics
  if (record.detail !== undefined && record.detail !== '') payload.content = record.detail
  if (record.thinking !== undefined) payload.thinking = record.thinking

  try {
    return JSON.stringify(payload, null, 2)
  } catch {
    return String(record.text)
  }
}

/** The text a Preview tab renders, and whether it is markdown or plain. */
function previewOf(record: TrajectoryRecord): { markdown: boolean; text: string } {
  if (record.kind === 'tool') {
    const result = record.result

    return {
      markdown: false,
      text:
        record.error ??
        result?.output ??
        result?.content ??
        result?.message ??
        result?.inline_diff ??
        '(no output)',
    }
  }

  return { markdown: true, text: record.detail ?? record.text }
}

export interface TrajectoryDetailsProps {
  onClose: () => void
  record: TrajectoryRecord
}

/**
 * What one record actually was.
 *
 * Three tabs because there are three questions: Summary answers "what did this
 * cost and how long did it take", Preview answers "what did it say", and Raw
 * answers "what exactly crossed the wire". The last is why the panel exists —
 * the ledger's one-line rows are lossy by design, and this is where the loss is
 * recoverable.
 */
export function TrajectoryDetails({ onClose, record }: TrajectoryDetailsProps) {
  const [tab, setTab] = useState<DetailTab>('summary')
  const timing = stepMetrics(record.metrics)
  const usage = record.metrics?.usage
  const preview = previewOf(record)

  return (
    <aside className={css.root}>
      <div className={css.header}>
        <span className={css.kind}>{KIND_LABEL[record.kind]}</span>
        <span className={css.position}>
          Turn {record.turn}
          {record.step > 0 && ` · Step ${record.step}`}
          {` · #${record.index}`}
        </span>
        <button aria-label="Close" className={css.close} onClick={onClose} type="button">
          <XIcon size={12} />
        </button>
      </div>

      <div className={css.tabs} role="tablist">
        {(['summary', 'preview', 'raw'] as const).map(id => (
          <button
            aria-selected={tab === id}
            className={css.tab}
            key={id}
            onClick={() => {
              setTab(id)
            }}
            role="tab"
            type="button"
          >
            {id === 'summary' ? 'Summary' : id === 'preview' ? 'Preview' : 'Raw'}
          </button>
        ))}
      </div>

      <div className={css.body}>
        {tab === 'summary' && (
          <>
            <section className={css.section}>
              <dl className={css.facts}>
                <Fact
                  label="Source"
                  value={record.step > 0 ? `Step ${record.step}` : `Record #${record.index}`}
                />
                <Fact
                  label="Status"
                  value={
                    record.isError === true
                      ? 'Failed'
                      : record.endedAt === null
                        ? 'Running'
                        : 'Completed'
                  }
                />
                {record.metrics?.model !== undefined && (
                  <Fact label="Model" value={record.metrics.model} />
                )}
                {record.metrics?.stopReason !== undefined && (
                  <Fact label="Stop reason" value={record.metrics.stopReason} />
                )}
                {record.toolName !== undefined && <Fact label="Tool" value={record.toolName} />}
                {usage !== undefined && (
                  <>
                    {/* Prompt + output, computed here: the backend's own
                        `total` follows its billing split and leaves the cached
                        part out. */}
                    <Fact
                      label="Tokens"
                      value={`${promptTokens(usage) + usage.output} tok`}
                    />
                    <Fact label="Input" nested value={`${promptTokens(usage)} tok`} />
                    {usage.cache_read !== undefined && (
                      <Fact
                        label="Cached"
                        nested
                        value={`${usage.cache_read} tok (${Math.round(
                          (usage.cache_read / Math.max(1, promptTokens(usage))) * 100,
                        )}%)`}
                      />
                    )}
                    {usage.reasoning !== undefined && (
                      <Fact label="Reasoning" nested value={`${usage.reasoning} tok`} />
                    )}
                    <Fact label="Content" nested value={`${usage.output} tok`} />
                  </>
                )}
                {record.kind === 'tool' && (
                  <Fact label="Duration" value={formatMs(record.durationMs)} />
                )}
              </dl>
            </section>

            {record.kind === 'assistant' && (
              <section className={css.section}>
                <div className={css.sectionTitle}>Request timing</div>
                <dl className={css.facts}>
                  <Fact label="Started" value={timing.started} />
                  <Fact label="Total duration" value={timing.total} />
                  <Fact label="TTFT" value={stepTtft(record.metrics)} />
                  <Fact label="Generation" value={timing.generation} />
                  <Fact label="Throughput" value={timing.throughput} />
                </dl>
              </section>
            )}

            {record.thinking !== undefined && record.thinking !== '' && (
              <details className={css.disclosure}>
                <summary className={css.disclosureSummary}>Thinking</summary>
                <div className={css.thinking}>{record.thinking}</div>
              </details>
            )}
          </>
        )}

        {tab === 'preview' &&
          (preview.text === '' ? (
            <div className={css.empty}>Nothing to preview.</div>
          ) : preview.markdown ? (
            <Markdown text={preview.text} />
          ) : (
            <div className={css.prose}>{preview.text}</div>
          ))}

        {tab === 'raw' && <pre className={css.raw}>{rawPayload(record)}</pre>}
      </div>
    </aside>
  )
}
