import { supportsHyperlinks } from '@clawcodex/ink'

import { INLINE_MODE } from '../config/env.js'

import { isMac } from './platform.js'

/**
 * How links in agent output get opened, per terminal.
 *
 * Inline mode (the default) never arms mouse tracking — the transcript lives
 * in native scrollback, and capturing the mouse would break the terminal's
 * own selection and wheel scrolling. So opening a link is delegated entirely
 * to the terminal emulator:
 *
 * - OSC 8-capable terminals (iTerm2, VS Code, kitty, Ghostty, WezTerm, …)
 *   open our hyperlink metadata on Cmd+click (Ctrl+click elsewhere).
 * - Apple Terminal has NO OSC 8 support (still true on macOS 26). Its only
 *   native affordance is URL detection over the visible text:
 *   Cmd+double-click the URL (or right-click → Open URL). This is why the
 *   markdown renderer keeps the target URL visible next to the label.
 * - Anything else without OSC 8: we don't know a gesture, so we stay quiet
 *   rather than advertise one that doesn't work.
 *
 * (Fullscreen/alt-screen mode is different: mouse tracking is armed there and
 * clicks open links in-process via onHyperlinkClick, in every terminal.)
 */

const APPLE_TERMINAL = 'Apple_Terminal'

const isAppleTerminal = (env: NodeJS.ProcessEnv) => env.TERM_PROGRAM === APPLE_TERMINAL

/**
 * Hotkey row for the ? quick help / /help panel: [keys, description].
 * Null when the terminal has no link-opening gesture we know of.
 */
export function linkOpenHotkey(
  env: NodeJS.ProcessEnv = process.env,
  supported: boolean = supportsHyperlinks(),
  inline: boolean = INLINE_MODE
): [string, string] | null {
  if (supported) {
    // OSC 8 terminals open our hyperlink metadata on Cmd/Ctrl+click in both
    // modes (xterm.js and iTerm2 handle it themselves even with mouse
    // tracking armed — the double-open dance the renderer's click dispatcher
    // defers to in @clawcodex/ink's components/App.tsx). They also render
    // their own hover affordance (underline/tooltip while the modifier is
    // held), so tell the user that's how to spot a clickable link.
    const mod = isMac ? 'Cmd' : 'Ctrl'

    return [`${mod}+click link`, `open link in browser (${mod}+hover underlines it)`]
  }

  if (!inline) {
    // Fullscreen arms mouse tracking, so clicks are handled in-process
    // (onHyperlinkClick) in every terminal — including Apple Terminal,
    // where the captured mouse suppresses the native Cmd+double-click.
    // The renderer's hover overlay (applyHyperlinkHoverHighlight) inverts
    // a link's cells while the pointer is over it — no modifier, since
    // terminals don't put Cmd in mouse reports.
    return ['click link', 'open link in browser (highlights on hover)']
  }

  if (isAppleTerminal(env)) {
    return ['Cmd+double-click URL', 'open link in browser']
  }

  return null
}

// Matches any http(s) URL — both bare URLs and the target inside a markdown
// [label](url), since the renderer always keeps the URL text visible.
const URL_RE = /https?:\/\//

export const LINK_TIP_TEXT = 'tip: to open links in Apple Terminal, hold ⌘ and double-click the URL'

// Once per session: the tip teaches a single gesture; repeating it under
// every linky message would just be noise. Module state (not React state)
// because the decision is made in the gateway event handler, outside render.
let linkTipShown = false

export const resetLinkTipForTests = () => {
  linkTipShown = false
}

/**
 * The one-time "how to open links here" transcript tip, or null.
 *
 * Fires only when (a) it hasn't fired this session, (b) the TUI runs in
 * inline mode — fullscreen captures the mouse and opens links in-process on
 * a plain click, making the tip's gesture both unnecessary and suppressed —
 * (c) the terminal lacks OSC 8 support, (d) it's Apple Terminal — the one
 * non-OSC 8 terminal whose gesture we can name — and (e) the just-completed
 * assistant text actually contains a URL, so the tip lands directly under
 * the links it explains.
 */
export function linkTipFor(
  texts: readonly string[],
  env: NodeJS.ProcessEnv = process.env,
  supported: boolean = supportsHyperlinks(),
  inline: boolean = INLINE_MODE
): string | null {
  if (linkTipShown || supported || !inline || !isAppleTerminal(env)) {
    return null
  }

  if (!texts.some(t => URL_RE.test(t))) {
    return null
  }

  linkTipShown = true

  return LINK_TIP_TEXT
}
