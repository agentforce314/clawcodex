import { useStore } from '@nanostores/react'
import { computed } from 'nanostores'
import { useEffect } from 'react'

import {
  disconnectProvider,
  refreshGeneralSettings,
  refreshProviders,
  saveProviderKey,
  setApprovalMode,
  setDefaultProvider,
  setOutputStyle,
  setRecap,
  setResponseLanguage,
} from '../state/actions.ts'
import { $themePreference, setThemePreference } from '../state/theme.ts'
import { GeneralSection } from './GeneralSection.tsx'
import {
  $generalSettings,
  $pendingApprovalMode,
  $providers,
  $sessionId,
  $settingsTab,
  $transcript,
} from '../state/store.ts'
import { XIcon } from '../ui/icons.tsx'
import { ProvidersSection } from './ProvidersSection.tsx'
import css from './Settings.module.css'

/**
 * The session's reported mode, or the choice held for the session that does
 * not exist yet — the same resolution the composer's picker shows. Computed so
 * the overlay re-renders on mode changes, not on every streamed delta the
 * transcript atom carries.
 */
const $approvalMode = computed(
  [$transcript, $pendingApprovalMode],
  (transcript, pending) => transcript.info.approval_mode ?? pending ?? undefined,
)

const TABS = [
  { id: 'general', label: 'General' },
  { id: 'providers', label: 'Providers' },
] as const

/**
 * Settings, as a full-screen takeover.
 *
 * Over the app rather than beside it: the three-column layout already gives up
 * the details pane on a narrow window, and settings is a place you go, finish,
 * and leave — not something to read alongside a conversation.
 */
export function SettingsOverlay() {
  const tab = useStore($settingsTab)
  const providers = useStore($providers)
  const general = useStore($generalSettings)
  const theme = useStore($themePreference)
  const sessionId = useStore($sessionId)
  const approvalMode = useStore($approvalMode)

  const open = tab !== null

  // Re-read on every open: a key may have been set from the CLI, or another
  // window, since this was last looked at.
  useEffect(() => {
    if (!open) return

    void refreshProviders()
    void refreshGeneralSettings()
  }, [open])

  useEffect(() => {
    if (!open) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') $settingsTab.set(null)
    }

    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!open) return null

  return (
    <div
      className={css.scrim}
      onClick={() => {
        $settingsTab.set(null)
      }}
    >
      <div
        aria-label="Settings"
        className={css.panel}
        onClick={event => {
          event.stopPropagation()
        }}
        role="dialog"
      >
        <div className={css.head}>
          <span className={css.title}>Settings</span>
          <button
            aria-label="Close settings"
            className={css.close}
            onClick={() => {
              $settingsTab.set(null)
            }}
            type="button"
          >
            <XIcon size={14} />
          </button>
        </div>

        <div className={css.body}>
          <nav className={css.nav}>
            {TABS.map(entry => (
              <button
                aria-current={tab === entry.id}
                className={[css.navItem, tab === entry.id ? css.navItemOn : '']
                  .filter(Boolean)
                  .join(' ')}
                key={entry.id}
                onClick={() => {
                  $settingsTab.set(entry.id)
                }}
                type="button"
              >
                {entry.label}
              </button>
            ))}
          </nav>

          <div className={css.content}>
            {tab === 'general' ? (
              <GeneralSection
                approvalMode={approvalMode}
                onApproval={mode => {
                  void setApprovalMode(mode)
                }}
                onLanguage={language => {
                  void setResponseLanguage(language)
                }}
                onRecap={enabled => {
                  void setRecap(enabled)
                }}
                onStyle={style => {
                  void setOutputStyle(style)
                }}
                onTheme={setThemePreference}
                sessionLive={sessionId !== null}
                settings={general}
                theme={theme}
              />
            ) : (
              <ProvidersSection
                defaultSlug={providers.default ?? ''}
                onDisconnect={slug => {
                  void disconnectProvider(slug)
                }}
                onMakeDefault={slug => {
                  void setDefaultProvider(slug)
                }}
                onSave={saveProviderKey}
                providers={providers.providers ?? []}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
