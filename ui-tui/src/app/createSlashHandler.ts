import { parseSlashCommand } from '../domain/slash.js'
import type { SlashExecResponse } from '../gatewayTypes.js'
import { asCommandDispatch, rpcErrorMessage } from '../lib/rpc.js'

import type { SlashHandlerContext } from './interfaces.js'
import { findSlashCommand } from './slash/registry.js'
import type { SlashRunCtx } from './slash/types.js'
import { getUiState } from './uiStore.js'

export function createSlashHandler(ctx: SlashHandlerContext): (cmd: string) => boolean {
  const { gw } = ctx.gateway
  const { catalog } = ctx.local
  const { page, send, sys } = ctx.transcript

  const handler = (cmd: string): boolean => {
    const ui = getUiState()
    const sid = ui.sid
    const parsed = parseSlashCommand(cmd)
    const argTail = parsed.arg ? ` ${parsed.arg}` : ''

    const found = findSlashCommand(parsed.name)

    // A registered command counts its own dispatches; everything that falls
    // through to the backend pipeline below shares one lane.
    //
    // The guard exists so a late reply cannot clobber state the user has moved
    // on from, but "the user ran some other command" is not moving on from this
    // one — and a single shared counter could not tell those apart. The /model
    // picker dispatches `/model …` and `/effort …` back-to-back in one tick
    // (useMainApp onModelSelect), so the second marked the first superseded and
    // the pending `/model` reply — the one that folds provider+model into
    // ui.info and prints `model → …` — was dropped, leaving the stats line
    // describing the previous session while the backend had already switched.
    //
    // The shared exec lane is deliberate rather than incidental: there the
    // OUTPUT is the whole product, so printing the results of a command the
    // user has already moved past is noise, and any newer exec slash should
    // still suppress it. Keyed on the canonical name so aliases of one command
    // supersede each other; a session change is caught by the sid check.
    const flights = ctx.slashFlightRef
    const key = found ? `cmd:${found.name}` : 'exec'
    const flight = (flights.current[key] ?? 0) + 1
    flights.current[key] = flight

    const stale = () => flight !== flights.current[key] || getUiState().sid !== sid

    const guarded =
      <T>(fn: (r: T) => void) =>
      (r: null | T): void => {
        if (!stale() && r) {
          fn(r)
        }
      }

    const guardedErr = (e: unknown) => {
      if (!stale()) {
        sys(`error: ${rpcErrorMessage(e)}`)
      }
    }

    const runCtx: SlashRunCtx = { ...ctx, flight, guarded, guardedErr, sid, stale, ui }

    if (found) {
      found.run(parsed.arg, runCtx, cmd)

      return true
    }

    if (catalog?.canon) {
      const needle = `/${parsed.name}`.toLowerCase()
      const exact = Object.entries(catalog.canon).find(([alias]) => alias.toLowerCase() === needle)?.[1]

      if (exact) {
        if (exact.toLowerCase() !== needle) {
          return handler(`${exact}${argTail}`)
        }
      } else {
        const matches = [
          ...new Set(
            Object.entries(catalog.canon)
              .filter(([alias]) => alias.startsWith(needle))
              .map(([, canon]) => canon)
          )
        ]

        if (matches.length === 1 && matches[0]!.toLowerCase() !== needle) {
          return handler(`${matches[0]}${argTail}`)
        }

        if (matches.length > 1) {
          sys(`ambiguous command: ${matches.slice(0, 6).join(', ')}${matches.length > 6 ? ', …' : ''}`)

          return true
        }
      }
    }

    const handleDispatch = (raw: unknown): void => {
      const d = asCommandDispatch(raw)

      if (!d) {
        return sys('error: invalid response: command.dispatch')
      }

      if (d.type === 'exec' || d.type === 'plugin') {
        return sys(d.output || '(no output)')
      }

      if (d.type === 'alias') {
        return void handler(`/${d.target}${argTail}`)
      }

      if (d.type === 'skill') {
        sys(`⚡ loading skill: ${d.name}`)

        return d.message?.trim() ? send(d.message) : sys(`/${parsed.name}: skill payload missing message`)
      }

      if (d.type === 'send') {
        if (d.notice?.trim()) {
          sys(d.notice)
        }

        // Submit without echoing the expanded payload as a user bubble — the
        // typed slash line is already in the transcript (dispatchSubmission
        // appends it as kind:'slash' before invoking this handler).
        return d.message?.trim() ? send(d.message, false) : sys(`/${parsed.name}: empty message`)
      }

      if (d.type === 'prefill') {
        // /undo returns prefill: drop the backed-up message text into
        // the composer so the user can edit and resubmit, instead of
        // submitting it immediately like 'send'.
        if (d.notice?.trim()) {
          sys(d.notice)
        }

        if (d.message) {
          ctx.composer.setInput(d.message)
        }
      }
    }

    gw.request<SlashExecResponse>('slash.exec', { command: cmd.slice(1), session_id: sid })
      .then(r => {
        if (stale()) {
          return
        }

        if (asCommandDispatch(r)) {
          return handleDispatch(r)
        }

        const body = r?.output || `/${parsed.name}: no output`
        const text = r?.warning ? `warning: ${r.warning}\n${body}` : body
        const long = text.length > 180 || text.split('\n').filter(Boolean).length > 2

        long ? page(text, parsed.name[0]!.toUpperCase() + parsed.name.slice(1)) : sys(text)
      })
      .catch(() => {
        gw.request('command.dispatch', { arg: parsed.arg, name: parsed.name, session_id: sid })
          .then((raw: unknown) => {
            if (stale()) {
              return
            }

            handleDispatch(raw)
          })
          .catch(guardedErr)
      })

    return true
  }

  return handler
}
