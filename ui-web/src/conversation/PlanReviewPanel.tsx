import { useEffect, useState } from 'react'

import { Button } from '../ui/primitives/Button.tsx'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import { ListIcon } from '../ui/icons.tsx'
import css from './PlanReviewPanel.module.css'

/** What approving a plan does to the permission mode. */
export type PlanApproval = 'auto' | 'manual'

/** The `setMode` update each choice sends as `chosen_updates`. */
export function updatesFor(approval: PlanApproval): { destination: string; mode: string; type: string }[] {
  return [
    {
      destination: 'session',
      // `acceptEdits` lets file edits through and still asks about shell and
      // everything else; `default` asks about all of it. Neither is
      // bypassPermissions — turning off every check is a decision the
      // approval-mode picker guards behind its own confirmation, and a plan
      // dialog is not the place to smuggle it in.
      mode: approval === 'auto' ? 'acceptEdits' : 'default',
      type: 'setMode',
    },
  ]
}

export interface PlanReviewPanelProps {
  /** null while the plan is still being fetched. */
  plan: string | null
  onApprove: (approval: PlanApproval) => void
  onReject: () => void
}

/**
 * The plan-mode exit, as a composer takeover.
 *
 * ExitPlanMode is a V2 tool — the plan lives in the session plan FILE, not the
 * tool input — so the generic approval had nothing to show and rendered as
 * "Use ExitPlanMode" over a blank card. Approving a plan you cannot read is
 * not approval.
 *
 * The two approve buttons are the mode choice the TUI's dialog offers, and
 * they are the point of the dialog: the plan is agreed either way, the
 * question is whether the edits that follow need watching.
 */
export function PlanReviewPanel({ onApprove, onReject, plan }: PlanReviewPanelProps) {
  const [busy, setBusy] = useState(false)

  // Escape keeps planning. It is the safe end of this decision — the agent
  // stays read-only — which is what a dismiss gesture should map to.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onReject()
    }

    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onReject])

  const approve = (approval: PlanApproval) => {
    setBusy(true)
    onApprove(approval)
  }

  return (
    <div className={css.root}>
      <section aria-labelledby="plan-review-title" className={css.card} role="dialog">
        <div className={css.strip}>
          <ListIcon size={14} />
          <span id="plan-review-title">Ready to implement</span>
        </div>

        <div className={css.body}>
          {plan === null ? (
            <div className={css.settling}>Loading the plan…</div>
          ) : plan.trim() === '' ? (
            // Saying so beats an empty card that looks like a rendering
            // failure — and the decision is still the user's to make.
            <div className={css.settling}>
              The agent wrote no plan file. Approving will leave plan mode anyway.
            </div>
          ) : (
            <Markdown className={css.plan} text={plan} />
          )}
        </div>

        <div className={css.actions}>
          <Button disabled={busy} onClick={onReject} size="sm" variant="outline">
            Keep planning
          </Button>
          <span className={css.spacer} />
          <Button
            disabled={busy}
            onClick={() => {
              approve('manual')
            }}
            size="sm"
            variant="outline"
          >
            Approve, ask before edits
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              approve('auto')
            }}
            size="sm"
            variant="primary"
          >
            Approve, edit freely
          </Button>
        </div>
      </section>
    </div>
  )
}
