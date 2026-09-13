/**
 * The column's start page: a muted compass over one capsule per page the
 * column can show, and nothing else — no heading, as a browser start page
 * shows its doors without a caption. Picking a capsule opens that page in this
 * tab's place, so the guide is a doorway rather than a page that stays open.
 *
 * Adapted from the reference's guide tab, whose entries are what the registered
 * tab types contributed; this column has two, so both are listed with their
 * descriptions (the reference drops descriptions past four entries).
 */

import type { ReactNode } from 'react'

import { CompassIcon, LayersIcon } from '../ui/icons.tsx'
import { FileTypeIcon } from '../ui/primitives/FileTypeIcon.tsx'
import { openPage } from './store.ts'
import css from './GuideTab.module.css'

interface GuideEntry {
  description: string
  icon: ReactNode
  kind: 'files' | 'session'
  title: string
}

const ENTRIES: GuideEntry[] = [
  {
    description: 'What this session read, wrote and ran',
    icon: <LayersIcon size={26} />,
    kind: 'session',
    title: 'Session',
  },
  {
    description: "Browse files in this session's workspace",
    icon: <FileTypeIcon kind="folder" size={26} />,
    kind: 'files',
    title: 'Workspace files',
  },
]

export function GuideTab({ tabId }: { tabId: string }) {
  return (
    <div className={css.guide} data-sidebar-guide="">
      <span aria-hidden="true" className={css.hero}>
        <CompassIcon size={56} strokeWidth={1.2} />
      </span>
      {ENTRIES.map(entry => (
        <button
          className={css.entry}
          data-guide-entry={entry.kind}
          key={entry.kind}
          onClick={() => {
            openPage(entry.kind, tabId)
          }}
          type="button"
        >
          <span className={css.entryIcon}>{entry.icon}</span>
          <span className={css.entryText}>
            <span className={css.entryTitle}>{entry.title}</span>
            <span className={css.entryDescription}>{entry.description}</span>
          </span>
        </button>
      ))}
    </div>
  )
}
