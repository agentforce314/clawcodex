import { Box, Text, useInput, useStdout } from '@clawcodex/ink'
import { useEffect, useMemo, useState } from 'react'

import { providerDisplayNames } from '../domain/providers.js'
import { TUI_SESSION_MODEL_FLAG } from '../domain/slash.js'
import type { GatewayClient } from '../gatewayClient.js'
import type { EffortOptionsResponse, ModelOptionProvider, ModelOptionsResponse } from '../gatewayTypes.js'
import { fuzzyRank } from '../lib/fuzzy.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { OverlayHint, useOverlayKeys, windowItems } from './overlayControls.js'

const VISIBLE = 12
const MIN_WIDTH = 40
const MAX_WIDTH = 90

type Stage = 'provider' | 'key' | 'model' | 'effort' | 'disconnect'

/**
 * Offered above the model's real levels on step 3: clears the session
 * override so the provider picks. Kept out of the backend's `levels` because
 * "let the provider decide" is only meaningful alongside a real level.
 */
const AUTO_EFFORT = 'auto'

export function ModelPicker({
  allowEffortStep = true,
  allowPersistGlobal = true,
  gw,
  onCancel,
  onSelect,
  sessionId,
  t
}: ModelPickerProps) {
  const [providers, setProviders] = useState<ModelOptionProvider[]>([])
  const [currentModel, setCurrentModel] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [persistGlobal, setPersistGlobal] = useState(false)
  const [providerIdx, setProviderIdx] = useState(0)
  const [modelIdx, setModelIdx] = useState(0)
  const [stage, setStage] = useState<Stage>('provider')
  const [keyInput, setKeyInput] = useState('')
  const [keySaving, setKeySaving] = useState(false)
  const [keyError, setKeyError] = useState('')
  // Step 3. `effortLevels` is auto + whatever the chosen model accepts;
  // `pendingModel` is the step-2 choice held while step 3 runs, so the
  // switch is applied once, with both halves, rather than twice.
  const [effortLevels, setEffortLevels] = useState<string[]>([])
  const [effortIdx, setEffortIdx] = useState(0)
  const [effortLoading, setEffortLoading] = useState(false)
  const [pendingModel, setPendingModel] = useState('')
  // Type-to-filter query, scoped per stage (cleared on stage change).
  const [filter, setFilter] = useState('')

  const { stdout } = useStdout()
  // Pin the picker to a stable width so the FloatBox parent (which shrinks-
  // to-fit with alignSelf="flex-start") doesn't resize as long provider /
  // model names scroll into view, and so `wrap="truncate-end"` on each row
  // has an actual constraint to truncate against.
  const width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - 6))

  useEffect(() => {
    gw.request<ModelOptionsResponse>('model.options', sessionId ? { session_id: sessionId } : {})
      .then(raw => {
        const r = asRpcResult<ModelOptionsResponse>(raw)

        if (!r) {
          setErr('invalid response: model.options')
          setLoading(false)

          return
        }

        const next = r.providers ?? []
        setProviders(next)
        setCurrentModel(String(r.model ?? ''))
        setProviderIdx(
          Math.max(
            0,
            next.findIndex(p => p.is_current)
          )
        )
        setModelIdx(0)
        setStage('provider')
        setErr('')
        setLoading(false)
      })
      .catch((e: unknown) => {
        setErr(rpcErrorMessage(e))
        setLoading(false)
      })
  }, [gw, sessionId])

  const names = useMemo(() => providerDisplayNames(providers), [providers])

  // Provider rows carry their display name so fuzzy filtering can match on
  // name + slug while keeping the name/provider pairing intact across ranking.
  const providerRows = useMemo(
    () => providers.map((p, i) => ({ provider: p, name: names[i] ?? p.name ?? p.slug })),
    [providers, names]
  )

  // providerIdx / modelIdx always index into the *displayed* (filtered) lists.
  // With an empty filter the filtered list equals the full list, so navigation
  // behaves exactly as before. Filtering only applies on the relevant stage.
  const filteredProviderRows = useMemo(() => {
    if (stage !== 'provider' || !filter.trim()) {
      return providerRows
    }

    return fuzzyRank(
      providerRows,
      filter,
      row => `${row.name} ${row.provider.slug} ${(row.provider.models ?? []).join(' ')}`
    ).map(r => r.item)
  }, [providerRows, filter, stage])

  const provider = filteredProviderRows[providerIdx]?.provider
  const allModels = useMemo(() => provider?.models ?? [], [provider])

  const filteredModels = useMemo(() => {
    if (stage !== 'model' || !filter.trim()) {
      return allModels
    }

    return fuzzyRank(allModels, filter, m => m).map(r => r.item)
  }, [allModels, filter, stage])

  const models = filteredModels

  // Keep the active selection within the (possibly filtered) list bounds.
  useEffect(() => {
    if (providerIdx >= filteredProviderRows.length && filteredProviderRows.length > 0) {
      setProviderIdx(0)
    }
  }, [filteredProviderRows.length, providerIdx])

  useEffect(() => {
    if (modelIdx >= models.length && models.length > 0) {
      setModelIdx(0)
    }
  }, [models.length, modelIdx])

  const buildModelValue = (slug: string, model: string) =>
    `${model} --provider ${slug}${allowPersistGlobal && persistGlobal ? ' --global' : ` ${TUI_SESSION_MODEL_FLAG}`}`

  // One exit point for the whole flow, so the switch is dispatched exactly
  // once whether step 3 ran, was skipped, or failed to load.
  const apply = (value: string, effort?: string) => {
    onSelect(value, effort && effort !== AUTO_EFFORT ? effort : undefined)
  }

  const back = () => {
    // Esc first clears an active filter on the list stages, before navigating.
    if ((stage === 'provider' || stage === 'model') && filter.trim()) {
      setFilter('')
      setProviderIdx(stage === 'provider' ? 0 : providerIdx)
      setModelIdx(0)

      return
    }

    // Step 3 goes back one step, to the model list it came from — not all
    // the way out to the providers.
    if (stage === 'effort') {
      setStage('model')
      setEffortLevels([])
      setEffortIdx(0)
      setEffortLoading(false)
      setPendingModel('')
      setFilter('')

      return
    }

    if (stage === 'model' || stage === 'key' || stage === 'disconnect') {
      setStage('provider')
      setModelIdx(0)
      setKeyInput('')
      setKeyError('')
      setKeySaving(false)
      setFilter('')

      return
    }

    onCancel()
  }

  // On the list stages we capture printable keys (including 'q') into the
  // filter, so the shared overlay q/Esc handler must yield to our own handler.
  // Step 3 joins the list stages so the shared q/Esc handler yields to ours
  // (it has no filter of its own, but it does own Enter/Esc/arrows).
  const listStage = stage === 'provider' || stage === 'model' || stage === 'effort'
  useOverlayKeys({ disabled: listStage, onBack: back, onClose: onCancel })

  useInput((ch, key) => {
    // Key entry stage handles its own input
    if (stage === 'key') {
      if (keySaving) {
        return
      }

      if (key.return) {
        if (!keyInput.trim()) {
          return
        }

        setKeySaving(true)
        setKeyError('')
        gw.request<{ provider?: ModelOptionProvider }>('model.save_key', {
          slug: provider?.slug,
          api_key: keyInput.trim(),
          ...(sessionId ? { session_id: sessionId } : {})
        })
          .then(raw => {
            const r = asRpcResult<{ provider?: ModelOptionProvider }>(raw)

            if (!r?.provider) {
              setKeyError('failed to save key')
              setKeySaving(false)

              return
            }

            // Update the provider in our list with fresh data
            setProviders(prev => prev.map(p => (p.slug === r.provider!.slug ? r.provider! : p)))
            setKeyInput('')
            setKeySaving(false)
            setStage('model')
            setModelIdx(0)
          })
          .catch((e: unknown) => {
            setKeyError(rpcErrorMessage(e))
            setKeySaving(false)
          })

        return
      }

      if (key.backspace || key.delete) {
        setKeyInput(v => v.slice(0, -1))

        return
      }

      // ctrl+u clears input
      if (ch === '\u0015') {
        setKeyInput('')

        return
      }

      if (ch && !key.ctrl && !key.meta) {
        setKeyInput(v => v + ch)
      }

      return
    }

    // Disconnect confirmation stage
    if (stage === 'disconnect') {
      if (ch.toLowerCase() === 'y' || key.return) {
        if (!provider) {
          setStage('provider')

          return
        }

        setKeySaving(true)
        setKeyError('')
        gw.request<{ disconnected?: boolean }>('model.disconnect', {
          slug: provider.slug,
          ...(sessionId ? { session_id: sessionId } : {})
        })
          .then(raw => {
            const r = asRpcResult<{ disconnected?: boolean }>(raw)

            if (r?.disconnected) {
              // Mark provider as unauthenticated in local state
              setProviders(prev =>
                prev.map(p =>
                  p.slug === provider.slug
                    ? {
                        ...p,
                        authenticated: false,
                        models: [],
                        total_models: 0,
                        warning: p.key_env ? `paste ${p.key_env} to activate` : 'run `clawcodex login` to configure'
                      }
                    : p
                )
              )
            }

            setKeySaving(false)
            setStage('provider')
          })
          .catch((e: unknown) => {
            // Refusals are real information — disconnecting the active
            // provider, or a key still exported in the shell. Stay put and
            // show why instead of dropping back as if it had worked.
            setKeyError(rpcErrorMessage(e))
            setKeySaving(false)
          })

        return
      }

      if (ch.toLowerCase() === 'n' || key.escape) {
        setStage('provider')

        return
      }

      return
    }

    // List-stage Esc/q handling (overlay keys are disabled while on a list
    // stage so 'q' can be typed into the filter).
    if (key.escape) {
      back()

      return
    }

    if (ch === 'q' && !filter) {
      onCancel()

      return
    }

    if (stage === 'effort' && effortLoading) {
      return
    }

    const count = stage === 'provider' ? filteredProviderRows.length : stage === 'effort' ? effortLevels.length : models.length
    const sel = stage === 'provider' ? providerIdx : stage === 'effort' ? effortIdx : modelIdx
    const setSel = stage === 'provider' ? setProviderIdx : stage === 'effort' ? setEffortIdx : setModelIdx

    if (key.upArrow && sel > 0) {
      setSel(v => v - 1)

      return
    }

    if (key.downArrow && sel < count - 1) {
      setSel(v => v + 1)

      return
    }

    if (key.return) {
      if (stage === 'provider') {
        if (!provider) {
          return
        }

        if (provider.authenticated === false) {
          // api_key providers: prompt for key inline
          if (provider.auth_type === 'api_key' && provider.key_env) {
            setStage('key')
            setKeyInput('')
            setKeyError('')
            setFilter('')
          }

          // Other auth types: no-op (warning shown tells them to run clawcodex model)
          return
        }

        setStage('model')
        setModelIdx(0)
        setFilter('')

        return
      }

      if (stage === 'effort') {
        const level = effortLevels[effortIdx]

        // `auto` is the picker's own row, not a level to send — it means
        // "clear the override", which is exactly what /effort auto does.
        apply(pendingModel, level)

        return
      }

      const model = models[modelIdx]

      if (!provider || !model) {
        setStage('provider')

        return
      }

      const value = buildModelValue(provider.slug, model)

      if (!allowEffortStep) {
        apply(value)

        return
      }

      // Step 3 is offered only where it can be applied: a model that takes no
      // effort parameter gets the two-step flow it had before, rather than a
      // list whose every row is a no-op.
      setPendingModel(value)
      setEffortLoading(true)
      setFilter('')
      setStage('effort')
      gw.request<EffortOptionsResponse>('model.effort_options', { model, provider: provider.slug })
        .then(raw => {
          const r = asRpcResult<EffortOptionsResponse>(raw)
          const levels = r?.supported ? (r.levels ?? []) : []

          if (!levels.length) {
            apply(value)

            return
          }

          const rows = [AUTO_EFFORT, ...levels]
          setEffortLevels(rows)
          // Land on the session's live level so Enter-through is a no-op
          // rather than a silent reset to auto.
          setEffortIdx(Math.max(0, rows.indexOf(r?.current || AUTO_EFFORT)))
          setEffortLoading(false)
        })
        .catch(() => {
          // The model is already chosen; a failed capability lookup must not
          // strand the user on a blank step. Apply what we have.
          apply(value)
        })

      return
    }

    // Step 3 has no filter, and its model value (with the persist flag baked
    // in) was built on the way in — so a ^g toggle here would silently do
    // nothing. Everything below this line belongs to the two list stages.
    if (stage === 'effort') {
      return
    }

    // Backspace removes the last filter character; Esc (above) clears a
    // non-empty filter before navigating back.
    if (key.backspace || key.delete) {
      setFilter(v => v.slice(0, -1))
      setSel(0)

      return
    }

    // Ctrl+U clears the filter. (Ctrl held → ch is the key name 'u'.)
    if (key.ctrl && ch === 'u') {
      setFilter('')
      setSel(0)

      return
    }

    // Persist-global toggle moved to Ctrl+G so 'g' can be typed into the
    // filter. With Ctrl held, @clawcodex/ink reports `ch` as the key name ('g'),
    // not the raw control byte (see input-event.ts: input = ctrl ? name : seq).
    if (allowPersistGlobal && key.ctrl && ch === 'g') {
      setPersistGlobal(v => !v)

      return
    }

    // Disconnect (Ctrl+D): only in provider stage, only for authenticated providers.
    if (key.ctrl && ch === 'd' && stage === 'provider' && provider?.authenticated !== false) {
      setStage('disconnect')

      return
    }

    // Any other printable single character extends the filter.
    if (ch && !key.ctrl && !key.meta && ch.length === 1 && ch >= ' ') {
      setFilter(v => v + ch)
      setSel(0)
    }
  })

  if (loading) {
    return <Text color={t.color.muted}>loading models…</Text>
  }

  if (err) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.label}>error: {err}</Text>
        <OverlayHint t={t}>Esc/q cancel</OverlayHint>
      </Box>
    )
  }

  if (!providers.length) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.muted}>no providers available</Text>
        <OverlayHint t={t}>Esc/q cancel</OverlayHint>
      </Box>
    )
  }

  // ── Key entry stage ──────────────────────────────────────────────────
  if (stage === 'key' && provider) {
    const masked = keyInput ? '•'.repeat(Math.min(keyInput.length, 40)) : ''

    return (
      <Box flexDirection="column" width={width}>
        <Text bold color={t.color.accent} wrap="truncate-end">
          Configure {provider.name}
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          Paste your API key below — saved to providers.{provider.slug}.api_key
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {' '}
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {provider.name} API key ({provider.key_env}):
        </Text>

        <Text color={t.color.accent} wrap="truncate-end">
          {'  '}
          {masked || '(empty)'}
          {keySaving ? '' : '▎'}
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {' '}
        </Text>

        {keyError ? (
          <Text color={t.color.label} wrap="truncate-end">
            error: {keyError}
          </Text>
        ) : keySaving ? (
          <Text color={t.color.muted} wrap="truncate-end">
            saving…
          </Text>
        ) : (
          <Text color={t.color.muted} wrap="truncate-end">
            {' '}
          </Text>
        )}

        <OverlayHint t={t}>Enter save · Ctrl+U clear · Esc back</OverlayHint>
      </Box>
    )
  }

  // ── Disconnect confirmation stage ─────────────────────────────────────
  if (stage === 'disconnect' && provider) {
    return (
      <Box flexDirection="column" width={width}>
        <Text bold color={t.color.accent} wrap="truncate-end">
          Disconnect {provider.name}?
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {' '}
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          Clears providers.{provider.slug}.api_key from ~/.clawcodex/config.json
        </Text>

        {provider.removes_env?.length ? (
          <Text color={t.color.muted} wrap="truncate-end">
            …and {provider.removes_env.join(', ')} from its env block
          </Text>
        ) : (
          <Text color={t.color.muted} wrap="truncate-end">
            {' '}
          </Text>
        )}

        <Text color={t.color.muted} wrap="truncate-end">
          You can re-authenticate later by selecting it again.
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {' '}
        </Text>

        {keyError ? (
          <Text color={t.color.label} wrap="truncate-end">
            error: {keyError}
          </Text>
        ) : (
          <Text color={t.color.muted} wrap="truncate-end">
            {' '}
          </Text>
        )}

        {keySaving ? (
          <Text color={t.color.muted} wrap="truncate-end">
            disconnecting…
          </Text>
        ) : (
          <OverlayHint t={t}>y/Enter confirm · n/Esc cancel</OverlayHint>
        )}
      </Box>
    )
  }

  // ── Effort selection stage (step 3) ──────────────────────────────────
  if (stage === 'effort') {
    if (effortLoading) {
      return <Text color={t.color.muted}>loading effort levels…</Text>
    }

    const { items, offset } = windowItems(effortLevels, effortIdx, VISIBLE)

    return (
      <Box flexDirection="column" width={width}>
        <Text bold color={t.color.accent} wrap="truncate-end">
          Select effort (step 3/3)
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {pendingModel.split(' ')[0] || '(model)'} · Esc back
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          Levels this model accepts · Enter to switch
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          {offset > 0 ? ` ↑ ${offset} more` : ' '}
        </Text>

        {Array.from({ length: Math.min(VISIBLE, Math.max(effortLevels.length, 1)) }, (_, i) => {
          const row = items[i]
          const idx = offset + i

          if (!row) {
            return (
              <Text color={t.color.muted} key={`pad-${i}`} wrap="truncate-end">
                {' '}
              </Text>
            )
          }

          return (
            <Text
              bold={effortIdx === idx}
              color={effortIdx === idx ? t.color.accent : t.color.muted}
              inverse={effortIdx === idx}
              key={`effort-${row}`}
              wrap="truncate-end"
            >
              {effortIdx === idx ? '▸ ' : '  '}
              {idx + 1}. {row}
              {row === AUTO_EFFORT ? ' · provider default' : ''}
            </Text>
          )
        })}

        <Text color={t.color.muted} wrap="truncate-end">
          {offset + VISIBLE < effortLevels.length ? ` ↓ ${effortLevels.length - offset - VISIBLE} more` : ' '}
        </Text>

        <OverlayHint t={t}>↑/↓ select · Enter switch · Esc back · q close</OverlayHint>
      </Box>
    )
  }

  // ── Provider selection stage ─────────────────────────────────────────
  if (stage === 'provider') {
    const rows = filteredProviderRows.map(({ provider: p, name }) => {
      const authMark = p.authenticated === false ? '○' : p.is_current ? '*' : '●'
      const modelCount = p.total_models ?? p.models?.length ?? 0

      const suffix =
        p.authenticated === false ? (p.auth_type === 'api_key' ? '(no key)' : '(needs setup)') : `${modelCount} models`

      return `${authMark} ${name} · ${suffix}`
    })

    const { items, offset } = windowItems(rows, providerIdx, VISIBLE)
    const noMatches = !!filter.trim() && rows.length === 0

    return (
      <Box flexDirection="column" width={width}>
        <Text bold color={t.color.accent} wrap="truncate-end">
          Select provider (step 1/3)
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          Then a model, then its effort level · Enter to continue
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          Current: {currentModel || '(unknown)'}
        </Text>
        <Text color={filter ? t.color.accent : t.color.muted} wrap="truncate-end">
          {filter ? `filter: ${filter}▎` : 'type to filter · ↑/↓ select'}
        </Text>
        <Text color={t.color.label} wrap="truncate-end">
          {provider?.warning ? `warning: ${provider.warning}` : ' '}
        </Text>
        <Text color={t.color.muted} wrap="truncate-end">
          {offset > 0 ? ` ↑ ${offset} more` : ' '}
        </Text>

        {noMatches ? (
          <Text color={t.color.muted} wrap="truncate-end">
            no providers match
          </Text>
        ) : (
          Array.from({ length: VISIBLE }, (_, i) => {
            const row = items[i]
            const idx = offset + i
            const p = filteredProviderRows[idx]?.provider
            const dimmed = p?.authenticated === false

            return row ? (
              <Text
                bold={providerIdx === idx}
                color={providerIdx === idx ? t.color.accent : dimmed ? t.color.label : t.color.muted}
                inverse={providerIdx === idx}
                key={p?.slug ?? `row-${idx}`}
                wrap="truncate-end"
              >
                {providerIdx === idx ? '▸ ' : '  '}
                {idx + 1}. {row}
              </Text>
            ) : (
              <Text color={t.color.muted} key={`pad-${i}`} wrap="truncate-end">
                {' '}
              </Text>
            )
          })
        )}

        <Text color={t.color.muted} wrap="truncate-end">
          {offset + VISIBLE < rows.length ? ` ↓ ${rows.length - offset - VISIBLE} more` : ' '}
        </Text>

        <Text color={t.color.muted} wrap="truncate-end">
          persist: {allowPersistGlobal ? (persistGlobal ? 'global' : 'session') : 'session'}
          {allowPersistGlobal ? ' · ^g toggle' : ' only'}
        </Text>
        <OverlayHint t={t}>↑/↓ select · Enter choose · ^d disconnect · Esc clear/back · q close</OverlayHint>
      </Box>
    )
  }

  // ── Model selection stage ────────────────────────────────────────────
  const { items, offset } = windowItems(models, modelIdx, VISIBLE)
  const noModelMatches = !!filter.trim() && models.length === 0

  return (
    <Box flexDirection="column" width={width}>
      <Text bold color={t.color.accent} wrap="truncate-end">
        Select model (step 2/3)
      </Text>

      <Text color={t.color.muted} wrap="truncate-end">
        {filteredProviderRows[providerIdx]?.name || '(unknown provider)'} · Esc back
      </Text>
      <Text color={filter ? t.color.accent : t.color.muted} wrap="truncate-end">
        {filter ? `filter: ${filter}▎` : 'type to filter · ↑/↓ select'}
      </Text>
      <Text color={t.color.label} wrap="truncate-end">
        {provider?.warning ? `warning: ${provider.warning}` : ' '}
      </Text>
      <Text color={t.color.muted} wrap="truncate-end">
        {offset > 0 ? ` ↑ ${offset} more` : ' '}
      </Text>

      {Array.from({ length: VISIBLE }, (_, i) => {
        const row = items[i]
        const idx = offset + i

        if (!row) {
          return (!allModels.length || noModelMatches) && i === 0 ? (
            <Text color={t.color.muted} key="empty" wrap="truncate-end">
              {noModelMatches ? 'no models match filter' : 'no models listed for this provider'}
            </Text>
          ) : (
            <Text color={t.color.muted} key={`pad-${i}`} wrap="truncate-end">
              {' '}
            </Text>
          )
        }

        const prefix = modelIdx === idx ? '▸ ' : row === currentModel ? '* ' : '  '

        return (
          <Text
            bold={modelIdx === idx}
            color={modelIdx === idx ? t.color.accent : t.color.muted}
            inverse={modelIdx === idx}
            key={`${provider?.slug ?? 'prov'}:${idx}:${row}`}
            wrap="truncate-end"
          >
            {prefix}
            {idx + 1}. {row}
          </Text>
        )
      })}

      <Text color={t.color.muted} wrap="truncate-end">
        {offset + VISIBLE < models.length ? ` ↓ ${models.length - offset - VISIBLE} more` : ' '}
      </Text>

      <Text color={t.color.muted} wrap="truncate-end">
        persist: {allowPersistGlobal ? (persistGlobal ? 'global' : 'session') : 'session'}
        {allowPersistGlobal ? ' · ^g toggle' : ' only'}
      </Text>
      <OverlayHint t={t}>
        {models.length ? '↑/↓ select · Enter switch · Esc clear/back · q close' : 'Esc back · q close'}
      </OverlayHint>
    </Box>
  )
}

interface ModelPickerProps {
  /**
   * Whether to offer step 3. False for callers that capture the selection as
   * a draft instead of switching the live session — they have nowhere to put
   * an effort level, and offering a choice they would discard is worse than
   * not offering it.
   */
  allowEffortStep?: boolean
  allowPersistGlobal?: boolean
  gw: GatewayClient
  onCancel: () => void
  /** `effort` is the step-3 choice, omitted for `auto` and for models with no ladder. */
  onSelect: (value: string, effort?: string) => void
  sessionId: string | null
  t: Theme
}
