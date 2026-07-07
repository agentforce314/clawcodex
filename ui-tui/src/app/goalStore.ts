import { atom } from 'nanostores'

import type { GoalSnapshot } from '../gatewayTypes.js'

// /goal indicator state — feeds the persistent right-aligned
// "◎ /goal active (14s)" line above the composer (GoalIndicator). The
// backend is the single source of truth: every /goal-/subgoal reply and
// every goal_status event carries a snapshot (or null), folded in here.

export interface GoalIndicatorState {
  goal: string
  maxTurns: number
  /** Epoch ms the goal was set (backend created_at) — elapsed ticks off it. */
  startedAt: number
  status: 'active' | 'paused'
  turnsUsed: number
}

export const $goalState = atom<GoalIndicatorState | null>(null)

export const getGoalState = () => $goalState.get()

export const resetGoalState = () => $goalState.set(null)

/** Fold a wire snapshot into the store. Anything but a well-formed
 *  active|paused snapshot hides the indicator. */
export const applyGoalSnapshot = (snap: GoalSnapshot | null | undefined) => {
  if (!snap || typeof snap !== 'object' || (snap.status !== 'active' && snap.status !== 'paused')) {
    $goalState.set(null)

    return
  }

  // created_at is epoch SECONDS (python time.time()); missing/garbage falls
  // back to "now" so the timer still runs instead of showing a 56-year gap.
  const createdS = typeof snap.created_at === 'number' && snap.created_at > 0 ? snap.created_at : null

  $goalState.set({
    goal: String(snap.goal ?? ''),
    maxTurns: Math.max(0, Math.trunc(Number(snap.max_turns ?? 0) || 0)),
    startedAt: createdS === null ? Date.now() : Math.round(createdS * 1000),
    status: snap.status,
    turnsUsed: Math.max(0, Math.trunc(Number(snap.turns_used ?? 0) || 0))
  })
}
