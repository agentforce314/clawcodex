import { memo } from 'react'

import { AlertIcon, InfoIcon } from '../ui/icons.tsx'
import { CopyButton } from '../ui/primitives/CopyButton.tsx'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import type { AssistantNode, NoticeNode, UserNode } from '../state/transcript.ts'
import css from './MessageItem.module.css'

/*
 * All three are memoized on their node.
 *
 * A streaming turn pushes a delta every few milliseconds and each one produces
 * a fresh nodes array — but the reducer copies only the node it changed, so
 * every settled row keeps its identity and re-renders nothing. Without this, a
 * long transcript re-renders in full on every token.
 */

function UserMessageImpl({ node, onEdit }: { node: UserNode; onEdit?: (text: string) => void }) {
  return (
    <div className={css.userRow}>
      {/* Plain text, not markdown: this is what the user typed, and rendering
          it as markdown would silently rewrite their own words. */}
      <div className={css.bubble}>{node.text}</div>
      <div className={css.actions}>
        <CopyButton className={css.action} text={node.text} />
        {onEdit !== undefined && (
          <button
            className={css.action}
            onClick={() => {
              onEdit(node.text)
            }}
            type="button"
          >
            Edit
          </button>
        )}
      </div>
    </div>
  )
}

function AssistantMessageImpl({ node }: { node: AssistantNode }) {
  return (
    <div className={css.assistantRow}>
      <Markdown className={css.assistant} streaming={!node.sealed} text={node.text} />
      {node.sealed && (
        <div className={css.actions}>
          <CopyButton className={css.action} text={node.text} />
        </div>
      )}
    </div>
  )
}

function NoticeMessageImpl({ node }: { node: NoticeNode }) {
  const toneClass =
    node.tone === 'error' ? css.noticeError : node.tone === 'warn' ? css.noticeWarn : css.noticeInfo

  return (
    <div className={[css.notice, toneClass].join(' ')}>
      <span className={css.noticeGlyph}>
        {node.tone === 'info' ? <InfoIcon size={14} /> : <AlertIcon size={14} />}
      </span>
      <span className={css.noticeBody}>
        <span className={css.noticeTitle}>{node.title}</span>
        {node.body}
      </span>
    </div>
  )
}

export const UserMessage = memo(UserMessageImpl)
export const AssistantMessage = memo(AssistantMessageImpl)
export const NoticeMessage = memo(NoticeMessageImpl)
