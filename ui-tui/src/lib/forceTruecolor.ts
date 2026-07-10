/**
 * Targeted 24-bit truecolor override before chalk / supports-color imports.
 *
 * macOS Terminal.app before Tahoe 26 does not support RGB SGR, so do not
 * infer truecolor from TERM_PROGRAM=Apple_Terminal. Users can still opt in
 * explicitly on terminals that support RGB but do not advertise COLORTERM.
 */

const TRUE_RE = /^(?:1|true|yes|on)$/i
const FALSE_RE = /^(?:0|false|no|off)$/i

export function shouldForceTruecolor(env: NodeJS.ProcessEnv = process.env): boolean {
  const override = (env.CLAWCODEX_TUI_TRUECOLOR ?? '').trim()

  if (FALSE_RE.test(override) || 'NO_COLOR' in env) {
    return false
  }

  return TRUE_RE.test(override)
}

const isAppleTerminal = (env: NodeJS.ProcessEnv = process.env) => (env.TERM_PROGRAM ?? '').trim() === 'Apple_Terminal'

const isAdvertisedTruecolor = (env: NodeJS.ProcessEnv = process.env) => {
  const colorTerm = (env.COLORTERM ?? '').trim().toLowerCase()
  const forceColor = (env.FORCE_COLOR ?? '').trim()

  return colorTerm === 'truecolor' || colorTerm === '24bit' || forceColor === '3'
}

export function shouldDowngradeAppleTerminalTruecolor(env: NodeJS.ProcessEnv = process.env): boolean {
  if (!isAppleTerminal(env)) {
    return false
  }

  if (shouldForceTruecolor(env)) {
    return false
  }

  return isAdvertisedTruecolor(env)
}

// The bundled chalk predates NO_COLOR support (it never checks the variable),
// so a NO_COLOR terminal still got full color output. Translate NO_COLOR into
// FORCE_COLOR=0 — the env channel this chalk does honor — unless the user set
// FORCE_COLOR themselves (explicit FORCE_COLOR outranks NO_COLOR, matching
// supports-color/Node getColorDepth precedence). This also keeps the
// transcript's monochrome `───` fallback (config/env.ts::TRANSCRIPT_COLOR,
// from stdout.hasColors which DOES honor NO_COLOR) agreeing with what the
// renderer actually emits.
export function shouldSuppressColorForNoColor(env: NodeJS.ProcessEnv = process.env): boolean {
  return 'NO_COLOR' in env && (env.FORCE_COLOR ?? '') === ''
}

if (shouldSuppressColorForNoColor()) {
  process.env.FORCE_COLOR = '0'
} else if (shouldForceTruecolor()) {
  if (!process.env.COLORTERM) {
    process.env.COLORTERM = 'truecolor'
  }

  process.env.FORCE_COLOR = '3'
} else if (shouldDowngradeAppleTerminalTruecolor()) {
  // Terminal.app may advertise truecolor even when RGB SGR paths render
  // incorrectly. Keep Clawcodex on the safer TERM-driven 256-color path unless
  // users explicitly opt back in via CLAWCODEX_TUI_TRUECOLOR=1.
  delete process.env.COLORTERM

  if ((process.env.FORCE_COLOR ?? '').trim() === '3') {
    delete process.env.FORCE_COLOR
  }
}

export {}
