import { useStore } from '@nanostores/react'
import { memo, useMemo } from 'react'

import { openFile } from '../sidebar-right/store.ts'
import { $workspace } from '../state/store.ts'
import { AlertIcon, InfoIcon } from '../ui/icons.tsx'
import { CopyButton } from '../ui/primitives/CopyButton.tsx'
import { FileTypeIcon } from '../ui/primitives/FileTypeIcon.tsx'
import { fileExtension, formatBytes } from './attachments.ts'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import type { AssistantNode, NoticeNode, UserNode } from '../state/transcript.ts'
import { projectUserText, userMessageCaption } from './user-text.tsx'
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
  const workspace = useStore($workspace)
  const caption = userMessageCaption(node.text, node.images, node.files)
  // Plain text, not markdown: this is what the user typed, and rendering it
  // as markdown would silently rewrite their own words. The one decoration is
  // an @file mention, which becomes a chip that opens the file beside the
  // conversation.
  const content = useMemo(
    () => projectUserText(caption, { onOpen: openFile, workspace }),
    [caption, workspace],
  )

  return (
    <div className={css.userRow}>
      {node.images !== undefined && node.images.length > 0 && (
        <div className={css.images}>
          {node.images.map((image, index) => (
            <img
              alt={image.name}
              className={css.image}
              key={index}
              src={image.url}
              title={image.placeholder ?? image.name}
            />
          ))}
        </div>
      )}
      {node.files !== undefined && node.files.length > 0 && (
        <div className={css.files}>
          {node.files.map((file, index) => (
            <div
              className={css.fileCard}
              data-file-card=""
              key={index}
              title={file.path ?? file.placeholder ?? file.name}
            >
              <FileTypeIcon className={css.fileIcon} path={file.name} size={18} />
              <span className={css.fileBody}>
                <span className={css.fileName}>{file.name}</span>
                <span className={css.fileMeta}>
                  {[fileExtension(file.name), file.size === undefined ? '' : formatBytes(file.size)]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
      {caption.trim() !== '' && <div className={css.bubble}>{content}</div>}
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

function AssistantMessageImpl({
  node,
  onRetry,
}: {
  node: AssistantNode
  onRetry?: () => void
}) {
  return (
    <div className={css.assistantRow}>
      <Markdown className={css.assistant} streaming={!node.sealed} text={node.text} />
      {node.sealed && (
        <div className={css.actions}>
          <CopyButton className={css.action} text={node.text} />
          {/* Only the newest reply gets this. Re-running an earlier turn would
              discard every turn after it, which is a different and much more
              destructive action than "give me another answer". */}
          {onRetry !== undefined && (
            <button className={css.action} onClick={onRetry} title="Re-run this prompt" type="button">
              Retry
            </button>
          )}
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
