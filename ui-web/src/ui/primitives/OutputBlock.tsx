import { CopyButton } from './CopyButton.tsx'
import css from './OutputBlock.module.css'

export interface OutputBlockProps {
  className?: string
  label?: string
  text: string
  tone?: 'default' | 'error'
}

/** Card for a tool result with no dedicated shape: plain, scrollable, copyable. */
export function OutputBlock({ className, label, text, tone = 'default' }: OutputBlockProps) {
  return (
    <div
      className={[css.block, tone === 'error' ? css.error : '', className]
        .filter(Boolean)
        .join(' ')}
    >
      {(label !== undefined || text !== '') && (
        <div className={css.banner}>
          <span>{label ?? 'output'}</span>
          <CopyButton className={css.copyButton} text={text} />
        </div>
      )}
      <pre className={css.body}>{text}</pre>
    </div>
  )
}
