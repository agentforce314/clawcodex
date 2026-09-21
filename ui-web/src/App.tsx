import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { ConversationRoot } from './conversation/ConversationRoot.tsx'
import { SettingsOverlay } from './settings/SettingsOverlay.tsx'
import { SidebarRight } from './sidebar-right/SidebarRight.tsx'
import { closeSidebar, resetSidebar } from './sidebar-right/store.ts'
import { AppFrame } from './layout/AppFrame.tsx'
import { NewSessionDialog, openNewSessionDialog } from './sidebar/NewSessionDialog.tsx'
import { Sidebar } from './sidebar/Sidebar.tsx'
import { start } from './state/actions.ts'
import { $detailsOpen, $detailsWidth, openDetails, toggleSidebar } from './state/layout.ts'
import { $bootError, $bootPhase, $sessionId, $workspace } from './state/store.ts'
import { installTheme } from './state/theme.ts'
import { Button } from './ui/primitives/Button.tsx'
import { BrandMark } from './ui/BrandMark.tsx'
import css from './App.module.css'

function BootScreen({ error }: { error: string }) {
  const failed = error !== ''

  return (
    <div className={css.boot}>
      <div className={css.bootCard}>
        <BrandMark className={css.bootMark} size={40} />
        <div className={css.bootTitle}>
          {failed ? 'Cannot reach the ClawCodex backend' : 'Connecting to ClawCodex…'}
        </div>
        {failed && (
          <>
            <div className={[css.bootMessage, css.bootError].join(' ')}>{error}</div>
            <div className={css.bootHint}>clawcodex web</div>
            <Button
              onClick={() => {
                window.location.reload()
              }}
              variant="outline"
            >
              Retry
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * The shell.
 *
 * Boot order matters: the theme is stamped before anything renders (the inline
 * script in index.html has already painted the right ground), and the gateway
 * connection is opened once, here — every other component reads state rather
 * than reaching for the socket.
 */
export function App() {
  const phase = useStore($bootPhase)
  const error = useStore($bootError)
  const detailsOpen = useStore($detailsOpen)
  const sessionId = useStore($sessionId)
  const workspace = useStore($workspace)

  useEffect(() => installTheme(), [])

  // The right column's open files belong to one session's workspace. Carrying
  // them into another would show a tree rooted somewhere the reader never went.
  useEffect(() => {
    resetSidebar()
  }, [sessionId])

  useEffect(() => {
    void start()
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const accel = event.metaKey || event.ctrlKey

      if (!accel) return

      if (event.key === 'b') {
        event.preventDefault()
        toggleSidebar()

        return
      }

      if (event.key === 'i') {
        event.preventDefault()

        if ($detailsWidth.get() === 0) openDetails()
        else closeSidebar()

        return
      }

      // Shift is required: plain Cmd+N is the browser's own new-window, and
      // stealing it would surprise the user in their own browser.
      if (event.key === 'n' && event.shiftKey) {
        event.preventDefault()
        openNewSessionDialog()
      }
    }

    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  useEffect(() => {
    document.title = workspace === '' ? 'ClawCodex' : `ClawCodex — ${workspace.split(/[/\\]/).pop()}`
  }, [workspace])

  if (phase !== 'ready') return <BootScreen error={phase === 'failed' ? error : ''} />

  return (
    <>
      <AppFrame
        conversation={<ConversationRoot />}
        details={detailsOpen ? <SidebarRight /> : null}
        sidebar={state => <Sidebar collapsed={state.collapsed} />}
      />
      {/* Outside the frame: they cover the whole app, including the sidebar
          they are opened from. */}
      <SettingsOverlay />
      <NewSessionDialog />
    </>
  )
}
