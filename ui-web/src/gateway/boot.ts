/**
 * Where the client gets its backend address and session token.
 *
 * Served by `clawcodex web`, the page arrives with
 * `window.__CLAWCODEX_SESSION_TOKEN__` inlined by the Python backend (the same
 * global the desktop shell scrapes to adopt a running backend), and `/api/*`
 * lives on the page's own origin. Nothing else is needed in that path.
 *
 * The other two paths exist for development and for opening the app against a
 * backend you started yourself:
 *   - `?token=…` in the URL — stripped from the address bar after adoption so
 *     the token does not linger in history or in a copied link,
 *   - `localStorage` — remembers the adopted token across reloads, since the
 *     Vite dev server has no way to inline one.
 */

const TOKEN_STORAGE_KEY = 'clawcodex.web.token'

declare global {
  interface Window {
    __CLAWCODEX_SESSION_TOKEN__?: string
  }
}

export interface BackendTarget {
  /** Absolute `/api` base for REST calls, no trailing slash. */
  apiBase: string
  token: string
  /** Fully-qualified gateway socket URL, token included. */
  wsUrl: string
}

function readStoredToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

function storeToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    /* private mode: the token still works for this page load */
  }
}

/**
 * Pull `?token=` out of the URL and remember it, leaving a clean address bar.
 * Returns the token it found, or '' when the URL carried none.
 */
function adoptUrlToken(): string {
  const url = new URL(window.location.href)
  const token = url.searchParams.get('token') ?? ''

  if (token === '') return ''

  storeToken(token)
  url.searchParams.delete('token')
  window.history.replaceState(null, '', url.toString())

  return token
}

export function resolveToken(): string {
  // URL first: an explicitly-passed token is the user's current intent and
  // must beat both a stale stored one and the inlined page default.
  return adoptUrlToken() || window.__CLAWCODEX_SESSION_TOKEN__ || readStoredToken()
}

export function resolveBackend(): BackendTarget {
  const token = resolveToken()
  const origin = window.location.origin
  const wsScheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsBase = `${wsScheme}//${window.location.host}/api/ws`

  return {
    apiBase: `${origin}/api`,
    token,
    wsUrl: token === '' ? wsBase : `${wsBase}?token=${encodeURIComponent(token)}`,
  }
}

/** REST helper for the handful of endpoints that are not on the socket. */
export async function apiGet<T>(target: BackendTarget, path: string): Promise<T> {
  const response = await fetch(`${target.apiBase}${path}`, {
    headers: target.token === '' ? {} : { 'X-ClawCodex-Session-Token': target.token },
  })

  if (!response.ok) throw new Error(`GET ${path} failed: ${response.status}`)

  return (await response.json()) as T
}
