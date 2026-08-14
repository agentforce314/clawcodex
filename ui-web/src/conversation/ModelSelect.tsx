import { useCallback, useMemo, useState } from 'react'

import type { ModelOptionsResult } from '../gateway/protocol.ts'
import { Menu, type MenuEntry } from '../ui/primitives/Menu.tsx'
import { ChevronDownIcon } from '../ui/icons.tsx'
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
export function buildModelMenu(models: ModelOptionsResult): {
  choices: Map<string, ModelChoice>
  items: MenuEntry[]
} {
  const items: MenuEntry[] = []
  const choices = new Map<string, ModelChoice>()

  for (const provider of models.providers ?? []) {
    const list = provider.models ?? []

    if (list.length === 0) continue

    items.push({ id: `group-${provider.name}`, text: provider.name, type: 'label' })

    for (const model of list) {
      const id = `m${choices.size}`
      choices.set(id, { model, provider: provider.name })
      items.push({ id, label: model })
    }
  }

  return { choices, items }
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
  onChange: (model: string, provider?: string) => void
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

  const currentModel = sessionModel ?? models.model ?? ''
  const currentProvider = sessionProvider ?? models.provider ?? ''

  const { choices, items } = useMemo(() => buildModelMenu(models), [models])
  const selectedId = useMemo(
    () => selectedModelId(choices, currentModel, currentProvider),
    [choices, currentModel, currentProvider],
  )

  const choose = useCallback(
    (id: string) => {
      setOpen(false)

      const choice = choices.get(id)

      if (choice !== undefined) onChange(choice.model, choice.provider)
    },
    [choices, onChange],
  )

  const label = currentModel === '' ? 'Model' : currentModel

  return (
    <Menu
      align="end"
      anchor={
        <button
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={`Model: ${label}`}
          className={css.trigger}
          disabled={disabled}
          onClick={() => {
            setOpen(value => !value)
          }}
          title={currentProvider === '' ? label : `${currentProvider} · ${label}`}
          type="button"
        >
          <span className={css.triggerLabel}>{label}</span>
          <span className={[css.chevron, open ? css.chevronOpen : ''].filter(Boolean).join(' ')}>
            <ChevronDownIcon size={12} />
          </span>
        </button>
      }
      emptyText="No configured providers yet."
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
