import { useEffect } from 'react'

import type { ApprovalChoice, ApprovalRequestPayload } from '../gateway/protocol.ts'
import { Button } from '../ui/primitives/Button.tsx'
import css from './ApprovalPanel.module.css'

export interface ApprovalPanelProps {
  onRespond: (choice: ApprovalChoice) => void
  request: ApprovalRequestPayload
}

/**
 * The permission ask, as a composer takeover.
 *
 * It replaces the input rather than floating over it: while the agent is
 * waiting on this answer there is nothing else to type, and a modal over the
 * transcript would hide the very tool call the user is being asked about.
 */
export function ApprovalPanel({ onRespond, request }: ApprovalPanelProps) {
  // Escape denies. The agent is blocked until this resolves, so the panel must
  // answer to the gesture people use to dismiss things.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onRespond('deny')
    }

    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onRespond])

  const headline =
    request.description !== undefined && request.description !== ''
      ? request.description
      : `Use ${request.tool_name ?? 'a tool'}`

  return (
    <div className={css.root}>
      <div className={css.card} role="alertdialog">
        <div className={css.strip}>
          <span className={css.dot} />
          <span>Permission needed</span>
        </div>
        <div className={css.body}>
          <div className={css.headline}>{headline}</div>
          {request.command !== undefined && request.command !== '' && (
            <div className={css.command}>{request.command}</div>
          )}
          {request.warning !== undefined && request.warning !== '' && (
            <div className={css.warning}>{request.warning}</div>
          )}
        </div>
        <div className={css.actionRow}>
          <Button
            className={css.reject}
            onClick={() => {
              onRespond('deny')
            }}
            size="sm"
            variant="outline"
          >
            Deny
          </Button>
          <Button
            onClick={() => {
              onRespond('session')
            }}
            size="sm"
            variant="outline"
          >
            Allow for session
          </Button>
          <Button
            autoFocus
            onClick={() => {
              onRespond('allow')
            }}
            size="sm"
            variant="primary"
          >
            Allow once
          </Button>
        </div>
      </div>
    </div>
  )
}
