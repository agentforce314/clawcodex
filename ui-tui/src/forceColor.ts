/**
 * Force color ON before any color library (chalk via cli-highlight, and Ink)
 * loads — this MUST be imported first.
 *
 * The launcher spawns this process with stdout inherited, but in that spawned
 * context cli-highlight's `supports-color` can resolve to "no color" (level 0),
 * so syntax highlighting silently drops to plain text — which then sits on the
 * diff's hardcoded rgb tint as flat, unreadable text. The TUI always renders to
 * a terminal (Ink itself emits truecolor), so advertise truecolor — unless the
 * user explicitly opted out via NO_COLOR or their own FORCE_COLOR.
 */
if (process.env['FORCE_COLOR'] === undefined && process.env['NO_COLOR'] === undefined) {
  process.env['FORCE_COLOR'] = '3'
}

export {}
