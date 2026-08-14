import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { disconnectProvider, refreshProviders, saveProviderKey } from '../state/actions.ts'
import { $providers, $settingsTab } from '../state/store.ts'
import { XIcon } from '../ui/icons.tsx'
import { ProvidersSection } from './ProvidersSection.tsx'
import css from './Settings.module.css'

const TABS = [{ id: 'providers', label: 'Providers' }] as const

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

  const open = tab !== null

  // Re-read on every open: a key may have been set from the CLI, or another
  // window, since this was last looked at.
  useEffect(() => {
    if (open) void refreshProviders()
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
            <ProvidersSection
              onDisconnect={slug => {
                void disconnectProvider(slug)
              }}
              onSave={saveProviderKey}
              providers={providers.providers ?? []}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
