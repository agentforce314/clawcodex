import { useState } from 'react'

import { Button } from './primitives/Button.tsx'
import { AlertIcon } from './icons.tsx'
import css from './FullAccessDialog.module.css'

export interface FullAccessDialogProps {
  /** The acknowledgement sentence; the reader must check it to proceed. */
  acknowledgeLabel: string
  /** What enabling Full access means on THIS surface (session vs default). */
  body: string
  onCancel: () => void
  onConfirm: () => void
}

/**
 * The Full-access confirmation: mounted is open, and confirming requires
 * checking the acknowledgement first.
 *
 * Full access turns off every permission check, so it is the one choice that
 * asks for confirmation rather than switching on a single click. The
 * acknowledgement is a checkbox, not just a second button: the point is to
 * make the reader parse the sentence, and an "are you sure" dialog people
 * dismiss reflexively would not. Shared by the composer picker and the
 * settings row so there is exactly one wording of the warning.
 */
export function FullAccessDialog({
  acknowledgeLabel,
  body,
  onCancel,
  onConfirm,
}: FullAccessDialogProps) {
  const [acknowledged, setAcknowledged] = useState(false)

  return (
    <div className={css.scrim} onClick={onCancel}>
      <div
        className={css.dialog}
        onClick={event => {
          event.stopPropagation()
        }}
        role="alertdialog"
      >
        <div className={css.dialogHead}>
          <AlertIcon size={16} />
          Turn off approvals?
        </div>
        <div className={css.dialogBody}>{body}</div>
        <label className={css.acknowledge}>
          <input
            checked={acknowledged}
            onChange={event => {
              setAcknowledged(event.currentTarget.checked)
            }}
            type="checkbox"
          />
          {acknowledgeLabel}
        </label>
        <div className={css.dialogActions}>
          <Button onClick={onCancel} size="sm" variant="outline">
            Cancel
          </Button>
          <Button disabled={!acknowledged} onClick={onConfirm} size="sm" variant="primary">
            Enable full access
          </Button>
        </div>
      </div>
    </div>
  )
}
