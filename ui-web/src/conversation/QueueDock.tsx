import { XIcon } from '../ui/icons.tsx'
import { Button } from '../ui/primitives/Button.tsx'
import { steeringUnavailableReason } from './steering.ts'
import css from './QueueDock.module.css'

export interface QueueDockProps {
  items: string[]
  onRemove: (index: number) => void
  onSendNow: (index: number) => void
  sendingNow: boolean
}

/**
 * Prompts typed while a turn was running.
 *
 * Prompts wait for the next turn unless explicitly sent into the active one.
 */
export function QueueDock({ items, onRemove, onSendNow, sendingNow }: QueueDockProps) {
  if (items.length === 0) return null

  return (
    <div className={css.root}>
      <div className={css.header}>
        Queued
        <span className={css.count}>
          {items.length} {items.length === 1 ? 'prompt' : 'prompts'}
        </span>
      </div>
      <ul className={css.list}>
        {items.map((item, index) => (
          <li className={css.item} key={`${index}-${item.slice(0, 24)}`}>
            <span className={css.text}>{item}</span>
            <Button
              className={css.sendNow}
              size="sm"
              disabled={sendingNow || steeringUnavailableReason(item) !== null}
              onClick={() => { onSendNow(index) }}
              title={steeringUnavailableReason(item) ?? 'Add to the active turn after the current step'}
            >
              Send now
            </Button>
            <button
              aria-label="Remove from queue"
              className={css.remove}
              disabled={sendingNow}
              onClick={() => {
                onRemove(index)
              }}
              type="button"
            >
              <XIcon size={12} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
