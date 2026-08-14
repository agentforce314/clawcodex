/**
 * Session timestamps for the sidebar.
 *
 * The backend hands out two shapes for the same field. `desktop_projects.py`
 * says so in as many words — *"Both may be epoch floats or ISO strings (saved
 * rows)"* — and coerces both on its side. The client only ever handled the
 * string, so `Date.parse(1786740090.6)` returned NaN and every live row showed
 * a blank where its age should be.
 */

/** Milliseconds in the units epoch timestamps plausibly arrive in. */
const SECONDS_CUTOFF = 1e11

/**
 * Read either shape into epoch milliseconds, or `null` when there is no
 * usable timestamp.
 *
 * Numeric input is treated as seconds or milliseconds by magnitude: epoch
 * *seconds* pass 1e11 around the year 5138, and epoch *milliseconds* passed it
 * in 1973, so anything below the cutoff is seconds. Guessing wrong by a factor
 * of 1000 turns "3m ago" into "50 years ago", which is worse than showing
 * nothing — hence the explicit split rather than a bare `new Date(value)`.
 */
export function epochMs(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined) return null

  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return null

    return value < SECONDS_CUTOFF ? value * 1000 : value
  }

  const text = value.trim()

  if (text === '') return null

  // A numeric string is an epoch that survived a JSON round-trip as text.
  if (/^\d+(\.\d+)?$/.test(text)) return epochMs(Number(text))

  const parsed = Date.parse(text)

  return Number.isNaN(parsed) ? null : parsed
}

/**
 * A short age for a session row: "now", "5m", "3h", "2d", "6w".
 *
 * Empty string when there is no timestamp — the row simply carries no age,
 * which is honest, rather than a placeholder like "unknown" repeated down the
 * whole list.
 */
export function relativeTime(
  value: number | string | null | undefined,
  now: number = Date.now(),
): string {
  const at = epochMs(value)

  if (at === null) return ''

  const seconds = Math.round((now - at) / 1000)

  // A clock skew between the browser and the backend can put a row a few
  // seconds into the future; "now" is truer than a negative age.
  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${String(Math.floor(seconds / 60))}m`
  if (seconds < 86_400) return `${String(Math.floor(seconds / 3600))}h`
  if (seconds < 604_800) return `${String(Math.floor(seconds / 86_400))}d`

  return `${String(Math.floor(seconds / 604_800))}w`
}

/** The full timestamp, for the row's tooltip. Empty when there is none. */
export function absoluteTime(value: number | string | null | undefined): string {
  const at = epochMs(value)

  return at === null ? '' : new Date(at).toLocaleString()
}
