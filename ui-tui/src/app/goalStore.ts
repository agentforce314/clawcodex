import { atom } from 'nanostores'

import type { GoalSnapshot } from '../gatewayTypes.js'

// /goal indicator state — feeds the persistent right-aligned
// "◎ /goal active (14s)" line above the composer (GoalIndicator). The
// backend is the single source of truth: every /goal, /subgoal and /clear
// reply and every goal_status event carries a snapshot (or null), folded in
// here. (The goal TEXT stays wire-only — the indicator never renders it;
// /goal status is the reading surface.)

export interface GoalIndicatorState {
  maxTurns: number
  /** Epoch ms the goal was set (backend created_at) — elapsed ticks off it. */
  startedAt: number
  status: 'active' | 'paused'
  turnsUsed: number
}

export const $goalState = atom<GoalIndicatorState | null>(null)

// Highest backend capture-rev applied so far. Wire order is enqueue order,
// not capture order (and control-reply promises resolve after same-chunk
// events), so a stale "active" carrier can arrive AFTER the pause/clear
// that superseded it — without this guard it would stick forever, because
// paused/done goals emit no further events. Rev-less carriers (legacy
// backend) apply unconditionally; legacy and revved backends never mix
// within a session.
let lastRev = 0

export const getGoalState = () => $goalState.get()

export const resetGoalState = () => {
  lastRev = 0
  $goalState.set(null)
}

/** Fold a wire snapshot into the store. Anything but a well-formed
 *  active|paused snapshot hides the indicator; carriers with a rev at or
 *  below one already applied are dropped as stale. */
export const applyGoalSnapshot = (snap: GoalSnapshot | null | undefined, rev?: number) => {
  if (typeof rev === 'number') {
    if (rev <= lastRev) {
      return
    }

    lastRev = rev
  }

  if (!snap || typeof snap !== 'object' || (snap.status !== 'active' && snap.status !== 'paused')) {
    $goalState.set(null)

    return
  }

  // created_at is epoch SECONDS (python time.time()); missing/garbage falls
  // back to "now" so the timer still runs instead of showing a 56-year gap.
  const createdS = typeof snap.created_at === 'number' && snap.created_at > 0 ? snap.created_at : null

  $goalState.set({
    maxTurns: Math.max(0, Math.trunc(Number(snap.max_turns ?? 0) || 0)),
    startedAt: createdS === null ? Date.now() : Math.round(createdS * 1000),
    status: snap.status,
    turnsUsed: Math.max(0, Math.trunc(Number(snap.turns_used ?? 0) || 0))
  })
}
