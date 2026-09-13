/**
 * The right column: a tab strip and whatever the active tab draws.
 *
 * One surface per window, holding the session facts, the workspace tree, and a
 * tab per file the conversation opened. The strip is the only chrome — a type's
 * own controls (wrap, reload) belong in its body, not up here beside the
 * column's. Each chip leads with its kind: the folder for the tree, the file's
 * own type icon for a file, the compass for the start page.
 *
 * The strip's `+` opens the start page, drawn only while the column holds
 * none: a door to each page, the way the reference's add control works.
 *
 * Fullscreen takes the whole frame rather than widening the column: a file worth
 * reading is worth reading at full width, and the conversation is still one
 * click away.
 */

import { useStore } from '@nanostores/react'

import { CollapseIcon, CompassIcon, ExpandIcon, LayersIcon, PlusIcon, XIcon } from '../ui/icons.tsx'
import { FileTypeIcon } from '../ui/primitives/FileTypeIcon.tsx'
import { FilesTree } from './FilesTree.tsx'
import { GuideTab } from './GuideTab.tsx'
import { SessionTab } from './SessionTab.tsx'
import { TextPreview } from './TextPreview.tsx'
import {
  $activeTabId,
  $fullscreen,
  $tabs,
  closeSidebar,
  closeTab,
  focusTab,
  openGuide,
  toggleFullscreen,
  type SidebarTab,
} from './store.ts'
import css from './SidebarRight.module.css'

function TabBody({ tab }: { tab: SidebarTab }) {
  if (tab.kind === 'session') return <SessionTab />
  if (tab.kind === 'files') return <FilesTree />
  if (tab.kind === 'guide') return <GuideTab tabId={tab.id} />

  return <TextPreview path={tab.address} tabId={tab.id} />
}

/** The chip's leading glyph: what kind of thing the tab holds. */
function ChipIcon({ tab }: { tab: SidebarTab }) {
  if (tab.kind === 'session') return <LayersIcon size={14} />
  if (tab.kind === 'files') return <FileTypeIcon kind="folder" size={14} />
  if (tab.kind === 'guide') return <CompassIcon size={14} />

  return <FileTypeIcon path={tab.address} size={14} />
}

export function SidebarRight() {
  const tabs = useStore($tabs)
  const activeId = useStore($activeTabId)
  const fullscreen = useStore($fullscreen)

  const active = tabs.find(tab => tab.id === activeId) ?? tabs[0]
  const hasGuide = tabs.some(tab => tab.kind === 'guide')

  return (
    <div className={css.root} data-fullscreen={fullscreen ? '' : undefined}>
      <div className={css.strip}>
        {/* The tablist is the chips alone: the controls after them act on the
            column, not on any one tab, and a tablist that contained them would
            make a screen reader announce four extra "tabs". */}
        <div className={css.chips} role="tablist">
          {tabs.map(tab => (
            <div
              className={[css.chip, tab.id === active?.id ? css.chipActive : '']
                .filter(Boolean)
                .join(' ')}
              key={tab.id}
            >
              <button
                aria-selected={tab.id === active?.id}
                className={css.chipTitle}
                onClick={() => {
                  focusTab(tab.id)
                }}
                role="tab"
                title={tab.address === '' ? tab.title : tab.address}
                type="button"
              >
                <span aria-hidden="true" className={css.chipIcon}>
                  <ChipIcon tab={tab} />
                </span>
                <span className={css.chipLabel}>{tab.title}</span>
              </button>
              {/* The session tab is the column's floor: closing the last tab
                  would leave a strip around nothing, so it stays. */}
              {tab.kind !== 'session' && (
                <button
                  aria-label={`Close ${tab.title}`}
                  className={css.chipClose}
                  onClick={() => {
                    closeTab(tab.id)
                  }}
                  type="button"
                >
                  <XIcon size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
        {!hasGuide && (
          <button
            aria-label="New tab"
            className={css.tool}
            onClick={openGuide}
            title="New tab"
            type="button"
          >
            <PlusIcon size={16} />
          </button>
        )}
        <span className={css.fill} />
        <button
          aria-label={fullscreen ? 'Leave full screen' : 'Full screen'}
          aria-pressed={fullscreen}
          className={css.tool}
          onClick={toggleFullscreen}
          title={fullscreen ? 'Leave full screen' : 'Full screen'}
          type="button"
        >
          {fullscreen ? <CollapseIcon size={16} /> : <ExpandIcon size={16} />}
        </button>
        <button
          aria-label="Close the sidebar"
          className={css.tool}
          onClick={closeSidebar}
          title="Close the sidebar (⌘I)"
          type="button"
        >
          <XIcon size={16} />
        </button>
      </div>
      <div
        aria-label={active === undefined ? undefined : active.title}
        className={css.body}
        role="tabpanel"
      >
        {active === undefined ? null : <TabBody tab={active} />}
      </div>
    </div>
  )
}
