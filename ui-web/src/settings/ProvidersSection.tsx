import { useState } from 'react'

import type { ModelOption } from '../gateway/protocol.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { CheckIcon, XIcon } from '../ui/icons.tsx'
import css from './Settings.module.css'

/**
 * Order providers by how much the reader cares: the one running now, then the
 * ones with credentials, then the rest alphabetically. The catalog knows ~30
 * providers and a user has configured two of them.
 */
export function orderProviders(providers: ModelOption[]): ModelOption[] {
  return [...providers].sort((a, b) => {
    if ((a.is_current === true) !== (b.is_current === true)) return a.is_current === true ? -1 : 1
    if ((a.authenticated === true) !== (b.authenticated === true)) {
      return a.authenticated === true ? -1 : 1
    }

    return (a.name ?? '').localeCompare(b.name ?? '')
  })
}

/**
 * Whether this provider takes a key at all.
 *
 * Local endpoints — ollama, sglang, vllm — report `auth_type: "none"`. There
 * is nothing to set and nothing to disconnect, so offering either would be
 * offering something that cannot happen.
 */
export function takesKey(provider: ModelOption): boolean {
  return provider.auth_type !== 'none'
}

/** What to tell the reader about a provider's credentials. */
export function credentialState(provider: ModelOption): string {
  if (!takesKey(provider)) return 'No key needed'
  if (provider.authenticated !== true) return 'No key'
  if (provider.auth_type === 'subscription' || provider.auth_type === 'oauth') return 'Signed in'

  return 'Key set'
}

interface ProviderRowProps {
  onDisconnect: (slug: string) => void
  onSave: (slug: string, apiKey: string) => Promise<boolean>
  provider: ModelOption
}

function ProviderRow({ onDisconnect, onSave, provider }: ProviderRowProps) {
  const [editing, setEditing] = useState(false)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)

  const slug = provider.slug ?? provider.name
  const keyed = takesKey(provider)
  const configured = provider.authenticated === true

  const save = async () => {
    if (key.trim() === '') return

    setBusy(true)

    const ok = await onSave(slug, key.trim())

    setBusy(false)

    // Clear it either way: the field held a secret, and a failed save is a
    // reason to retype rather than to leave it sitting on screen.
    setKey('')
    if (ok) setEditing(false)
  }

  return (
    <div className={css.row}>
      <div className={css.rowHead}>
        <span className={css.rowName}>{provider.name}</span>
        {provider.is_current === true && <span className={css.badge}>Running</span>}
        <span className={[css.state, configured ? css.stateOn : ''].filter(Boolean).join(' ')}>
          {configured && <CheckIcon size={11} />}
          {credentialState(provider)}
        </span>
        <span className={css.rowSpacer} />
        {!editing && keyed && (
          <Button
            onClick={() => {
              setEditing(true)
            }}
            size="sm"
            variant="outline"
          >
            {configured ? 'Replace key' : 'Add key'}
          </Button>
        )}
        {!editing && keyed && configured && (
          <Button
            onClick={() => {
              onDisconnect(slug)
            }}
            size="sm"
            variant="ghost"
          >
            Disconnect
          </Button>
        )}
      </div>

      <div className={css.rowMeta}>
        {provider.total_models !== undefined && provider.total_models > 0 && (
          <span>
            {provider.total_models} {provider.total_models === 1 ? 'model' : 'models'}
          </span>
        )}
        {/* Naming the variable matters: a key exported in the shell cannot be
            removed from here, and this is the only clue to where it came from. */}
        {keyed && provider.key_env !== undefined && provider.key_env !== '' && (
          <span className={css.env}>{provider.key_env}</span>
        )}
      </div>

      {editing && (
        <div className={css.keyRow}>
          <input
            aria-label={`API key for ${provider.name}`}
            autoComplete="off"
            className={css.keyInput}
            onChange={event => {
              setKey(event.target.value)
            }}
            onKeyDown={event => {
              if (event.key === 'Enter') void save()
              if (event.key === 'Escape') {
                setKey('')
                setEditing(false)
              }
            }}
            placeholder={`Paste the ${provider.name} API key`}
            // A password field: the key is a secret, and settings pages get
            // screen-shared.
            type="password"
            value={key}
          />
          <Button disabled={busy || key.trim() === ''} onClick={() => void save()} size="sm" variant="primary">
            Save
          </Button>
          <button
            aria-label="Cancel"
            className={css.cancel}
            onClick={() => {
              setKey('')
              setEditing(false)
            }}
            type="button"
          >
            <XIcon size={12} />
          </button>
        </div>
      )}
    </div>
  )
}

export interface ProvidersSectionProps {
  onDisconnect: (slug: string) => void
  onSave: (slug: string, apiKey: string) => Promise<boolean>
  providers: ModelOption[]
}

/**
 * Providers and their credentials.
 *
 * The one thing that could not be done from the web at all: a broken or
 * missing key meant hand-editing `~/.clawcodex/config.json`. Keys are written
 * where `clawcodex login` writes them, so a key set here survives a restart
 * and the two paths agree.
 */
export function ProvidersSection({ onDisconnect, onSave, providers }: ProvidersSectionProps) {
  if (providers.length === 0) {
    return <div className={css.empty}>No providers to show.</div>
  }

  return (
    <div className={css.section}>
      <p className={css.blurb}>
        Keys are stored where <code>clawcodex login</code> stores them, so they survive a
        restart. A key exported in your shell cannot be removed from here.
      </p>
      {orderProviders(providers).map(provider => (
        <ProviderRow
          key={provider.slug ?? provider.name}
          onDisconnect={onDisconnect}
          onSave={onSave}
          provider={provider}
        />
      ))}
    </div>
  )
}
