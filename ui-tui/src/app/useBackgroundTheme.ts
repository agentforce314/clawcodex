import { useStdin } from '@hermes/ink'
import { useEffect } from 'react'

import { hasExplicitBackgroundSignal, oscBackgroundIsLight, themeForLightMode } from '../theme.js'

import { patchUiState } from './uiStore.js'

type OscResponse = { code: number; data: string; type: 'osc' }
type Querier = {
  flush: () => Promise<void>
  send: <T>(q: { match: (r: unknown) => r is T; request: string }) => Promise<T | undefined>
}

// OSC 11 "query background color": terminal replies on stdin with
// `ESC ] 11 ; rgb:RRRR/GGGG/BBBB ST`. The querier (shared with XTVERSION /
// OSC 52) recognizes the reply and resolves our send().
const OSC11_BG_QUERY = '\x1b]11;?\x1b\\'

/**
 * Match clawcodex's theme to the terminal's ACTUAL background via OSC 11.
 *
 * `detectLightMode()`'s final fallback is a TERM_PROGRAM allow-list (Apple
 * Terminal → light), which is wrong for a dark-profile Apple Terminal — the
 * banner and tool-trail labels then render in the light palette and wash out
 * on the dark background. When the user hasn't pinned a theme explicitly, ask
 * the terminal its background color and re-theme to match.
 *
 * Silent no-op when an explicit signal is set (HERMES_TUI_THEME / _LIGHT /
 * _BACKGROUND / COLORFGBG win) or the terminal doesn't answer (timeout → the
 * default theme stands, i.e. exactly today's behavior). Runs once on mount.
 */
export function useBackgroundTheme(): void {
  const { querier } = useStdin() as { querier?: Querier }

  useEffect(() => {
    if (!querier || hasExplicitBackgroundSignal()) {
      return
    }

    let cancelled = false
    // The querier is timeout-free (a send() stays pending until flush()'s DA1
    // sentinel). Race a short timer so a terminal that never answers OSC 11
    // doesn't hold the theme update; flush() then drains the pending query.
    const timeout = new Promise<undefined>(resolve => setTimeout(() => resolve(undefined), 600))
    const query = querier.send<OscResponse>({
      request: OSC11_BG_QUERY,
      match: (r): r is OscResponse =>
        !!r && typeof r === 'object' && (r as OscResponse).type === 'osc' && (r as OscResponse).code === 11
    })

    // Detection is async by design: the UI paints once in the default theme,
    // then flips when the reply lands (the Banner reads $uiState reactively, so
    // it repaints). A ≤600ms flash beats blocking startup on a terminal
    // round-trip — do NOT make this synchronous.
    void Promise.race([query, timeout]).then(response => {
      void querier.flush()

      if (cancelled || !response) {
        return
      }

      const isLight = oscBackgroundIsLight(response.data)

      if (isLight !== null) {
        patchUiState({ theme: themeForLightMode(isLight) })
      }
    })

    return () => {
      cancelled = true
    }
  }, [querier])
}
