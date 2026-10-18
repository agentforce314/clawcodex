import { useCallback, useMemo, useState } from 'react'

import type { ModelOptionsResult } from '../gateway/protocol.ts'
import { Menu, type MenuEntry } from '../ui/primitives/Menu.tsx'
import { ChevronDownIcon } from '../ui/icons.tsx'
import { rankRows } from './command-menu.ts'
import css from './Pickers.module.css'

export interface ModelChoice {
  model: string
  provider: string
}

/**
 * Flatten the catalog into menu rows and the choice each row stands for.
 *
 * Row ids are positional (`m0`, `m1`, …) with the pair held beside them,
 * rather than a `provider<sep>model` string the click handler parses back
 * apart. There is no separator that is provably absent from every provider
 * name and every model id — `openrouter` ships ids like
 * `deepseek/deepseek-v4-pro` — and a parse that is *usually* right is the
 * worst kind. Positional ids cannot be wrong.
 */
export function buildModelMenu(models: ModelOptionsResult, query = ''): {
  choices: Map<string, ModelChoice>
  items: MenuEntry[]
} {
  const items: MenuEntry[] = []
  const choices = new Map<string, ModelChoice>()

  for (const provider of models.providers ?? []) {
    const list = provider.models ?? []

    if (list.length === 0) continue

    const ranked = rankRows(list.map(model => ({ name: model })), query.trim())
    if (ranked.length === 0) continue
    items.push({ id: `group-${provider.slug ?? provider.name}`, text: provider.name, type: 'label' })

    for (const { name: model } of ranked) {
      const id = `m${choices.size}`
      // The SLUG, not the display name: the backend answers to "deepseek" and
      // rejects "DeepSeek" outright. Falls back to the name for a catalog row
      // that carries no slug, which is what the shape was before.
      choices.set(id, { model, provider: provider.slug ?? provider.name })
      items.push({ id, label: model })
    }
  }

  return { choices, items }
}

/**
 * How a model is named wherever it appears: `provider:model`.
 *
 * Bare until a provider is known, rather than showing a lone colon — an
 * unqualified name is incomplete, but a dangling separator is broken.
 */
export function qualifiedModel(model: string, provider?: string): string {
  if (model === '') return 'Model'

  return provider === undefined || provider === '' ? model : `${provider}:${model}`
}

/** The row to check: the exact pair when the catalog has it, else the model. */
export function selectedModelId(
  choices: Map<string, ModelChoice>,
  model: string,
  provider: string,
): string | undefined {
  if (model === '') return undefined

  for (const [id, choice] of choices) {
    if (choice.model === model && choice.provider === provider) return id
  }

  // The session can report a provider the catalog spells differently; the
  // model alone still identifies the row well enough to mark it.
  for (const [id, choice] of choices) {
    if (choice.model === model) return id
  }

  return undefined
}

export interface ModelSelectProps {
  disabled?: boolean
  models: ModelOptionsResult
  onChange: (model: string, provider?: string) => void | Promise<unknown>
  /** The session's live model/provider, which outrank the catalog's defaults. */
  sessionModel?: string
  sessionProvider?: string
}

/**
 * The model chip.
 *
 * Grouped under provider headings rather than flattened: the catalog runs to a
 * hundred-odd entries across a dozen providers, and `deepseek-v4-pro` from
 * deepseek and from openrouter are different choices that look identical in a
 * flat list.
 */
export function ModelSelect({
  disabled = false,
  models,
  onChange,
  sessionModel,
  sessionProvider,
}: ModelSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [pending, setPending] = useState(false)

  const currentModel = sessionModel ?? models.model ?? ''
  const currentProvider = sessionProvider ?? models.provider ?? ''

  const { choices, items } = useMemo(() => buildModelMenu(models, query), [models, query])
  const selectedId = useMemo(
    () => selectedModelId(choices, currentModel, currentProvider),
    [choices, currentModel, currentProvider],
  )

  const choose = useCallback(
    async (id: string) => {
      if (pending) return
      setOpen(false)

      const choice = choices.get(id)

      if (choice !== undefined) {
        setPending(true)
        try { await onChange(choice.model, choice.provider) }
        finally { setPending(false) }
      }
    },
    [choices, onChange, pending],
  )

  // Qualified, because the same model id is offered by several providers —
  // `deepseek-v4-pro` comes from both deepseek and openrouter, and they are
  // different choices with different keys and different bills. A bare model
  // name does not say which one is running.
  const label = qualifiedModel(currentModel, currentProvider)

  return (
    <Menu
      align="end"
      anchor={
        <button
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={`Model: ${label}`}
          className={css.trigger}
          disabled={disabled || pending}
          aria-busy={pending}
          onClick={() => {
            setQuery('')
            setOpen(value => !value)
          }}
          title={`Model: ${label}`}
          type="button"
        >
          <span className={css.triggerLabel}>{pending ? 'Switching model…' : label}</span>
          <span className={[css.chevron, open ? css.chevronOpen : ''].filter(Boolean).join(' ')}>
            <ChevronDownIcon size={12} />
          </span>
        </button>
      }
      emptyText={query === '' ? 'No configured providers yet.' : 'No matching models.'}
      search={{ label: 'Search models', query, onChange: setQuery }}
      items={items}
      onClose={() => {
        setOpen(false)
      }}
      onSelect={choose}
      open={open}
      selectedId={selectedId}
      side="top"
    />
  )
}
