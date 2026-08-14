import { XIcon } from '../ui/icons.tsx'
import css from './QueueDock.module.css'

export interface QueueDockProps {
  items: string[]
  onRemove: (index: number) => void
}

/**
 * Prompts typed while a turn was running.
 *
 * The agent takes one turn at a time, so a second prompt has to wait — but
 * dropping the draft is the one outcome a user cannot recover from. Queued
 * prompts are visible and removable until their turn comes.
 */
export function QueueDock({ items, onRemove }: QueueDockProps) {
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
            <button
              aria-label="Remove from queue"
              className={css.remove}
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
