/**
 * Folding a completed model switch back into `ui.info`.
 *
 * The stats line under the composer and the banner's model line both read
 * provider and model out of the same `SessionInfo`:
 *
 *   anthropic · gpt-5.6-luna · ~/work/app · turns: 4 · …
 *              ^^^^^^^^^^^^^ moved      ^^ did not
 *
 * `profile_name` used to be written exactly once, from the backend's `init`,
 * while every switch patched `model` alone — so selecting a model from another
 * provider left the two halves describing different sessions. Both apply sites
 * (the `/model` command and the prompt-session model arg) go through here so
 * they cannot drift apart again.
 */
import type { SessionInfo } from '../types.js'

/**
 * `info` with the switch applied. `provider` is optional on purpose: the
 * backend omits it when it has nothing to say (and older backends never send
 * it), and an absent provider means "unchanged", not "unknown" — blanking a
 * still-correct label would be a worse bug than the stale one. A null `info`
 * means the switch beat the backend's init; seed the minimum the type needs.
 */
export function infoAfterModelSwitch(
  info: null | SessionInfo | undefined,
  model: string,
  provider?: string
): SessionInfo {
  const next: SessionInfo = info
    ? { ...info, model }
    : { model, skills: {}, tools: {} }

  if (provider) {
    next.profile_name = provider
  }

  return next
}

/**
 * The slash commands a /model picker selection expands to, in dispatch order.
 *
 * The picker's three steps land on two independent settings, so each half
 * re-enters its own command (the /model + /logo picker pattern) and keeps
 * owning its RPC, persistence and transcript line — rather than teaching the
 * /model grammar a second dimension. Effort goes second so the transcript
 * reads model-then-effort; it is model-independent, so the order is
 * cosmetic. An absent effort — `auto`, or a model with no ladder — emits no
 * `/effort` at all, leaving the session's level untouched.
 */
export function modelPickerCommands(value: string, effort?: string): string[] {
  const commands = [`/model ${value}`]

  if (effort) {
    commands.push(`/effort ${effort}`)
  }

  return commands
}
