import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getOverlayState, patchOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { resetUiState } from '../app/uiStore.js'

// idle() drops every flow-scoped overlay, including a question dialog that is
// still up because the turn is being torn down under the user (recordError,
// /clear). The backend worker is BLOCKED in ask_user's wait at that moment, so
// dropping the overlay without replying strands it for ask_user_timeout_s
// (30 minutes) while the composer comes back looking ready. "Overlay gone" and
// "tool released" have to stay a single invariant.

const QUESTIONS = { questions: [{ question: 'Which posts?' }] }

describe('turnController.idle — releases a still-open question dialog', () => {
  beforeEach(() => {
    resetUiState()
    resetTurnState()
    resetOverlayState()
    turnController.fullReset()
    turnController.releaseQuestions = undefined
  })

  it('declines the dialog before dropping the overlay', () => {
    const release = vi.fn()

    turnController.releaseQuestions = release
    patchOverlayState({ questions: QUESTIONS })

    turnController.idle()

    expect(release).toHaveBeenCalledTimes(1)
    // And the overlay really is gone — the release is in addition to the drop,
    // not instead of it.
    expect(getOverlayState().questions).toBeNull()
  })

  it('does not fire when no dialog is open', () => {
    // Every turn ends through idle(); firing a decline on each one would send
    // a stray control_response for a request that was never made.
    const release = vi.fn()

    turnController.releaseQuestions = release

    turnController.idle()

    expect(release).not.toHaveBeenCalled()
  })

  it('is a no-op when nothing injected the callback', () => {
    // Tests and SDK paths never wire it; idle() must not throw there.
    patchOverlayState({ questions: QUESTIONS })

    expect(() => turnController.idle()).not.toThrow()
    expect(getOverlayState().questions).toBeNull()
  })
})
