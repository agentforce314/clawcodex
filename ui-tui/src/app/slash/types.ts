import type { MutableRefObject } from 'react'

import type { SlashHandlerContext, UiState } from '../interfaces.js'

export interface SlashRunCtx extends SlashHandlerContext {
  flight: number
  guarded: <T>(fn: (r: T) => void) => (r: null | T) => void
  guardedErr: (e: unknown) => void
  sid: null | string
  slashFlightRef: MutableRefObject<number>
  stale: () => boolean
  ui: UiState
}

export interface SlashCommand {
  aliases?: string[]
  /** Allowed values / argument grammar, e.g. `[on|off|toggle]` — shown dim in
   *  the completion menu and as ghost text after `/name ` (original CC's
   *  Command.argumentHint). */
  argumentHint?: string
  help?: string
  name: string
  run: (arg: string, ctx: SlashRunCtx, cmd: string) => void
  usage?: string
}
