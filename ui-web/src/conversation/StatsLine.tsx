import type { UsagePayload } from '../gateway/protocol.ts'
import css from './StatsLine.module.css'

export interface StatsLineProps {
  model?: string
  provider?: string
  usage?: UsagePayload
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`

  return String(value)
}

/** The quiet line under the composer: what ran, and what it cost in tokens. */
export function StatsLine({ model, provider, usage }: StatsLineProps) {
  const parts: string[] = []

  if (model !== undefined && model !== '') {
    parts.push(provider === undefined || provider === '' ? model : `${provider} · ${model}`)
  }

  if (usage !== undefined && usage.total > 0) {
    parts.push(`${formatTokens(usage.input)} in / ${formatTokens(usage.output)} out`)
    parts.push(`${usage.calls} ${usage.calls === 1 ? 'turn' : 'turns'}`)
  }

  if (parts.length === 0) return null

  return (
    <div className={css.root}>
      {parts.map((part, index) => (
        <span key={`${index}-${part}`}>
          {index > 0 && <span className={css.sep}>·</span>}
          {part}
        </span>
      ))}
    </div>
  )
}
