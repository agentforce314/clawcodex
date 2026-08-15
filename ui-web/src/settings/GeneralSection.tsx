import { useEffect, useState } from 'react'

import type { GeneralSettingsResult } from '../gateway/protocol.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { type ThemePreference } from '../state/theme.ts'
import css from './Settings.module.css'

const THEMES: { id: ThemePreference; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
]

/** Title-case an output style name for its chip ("default" → "Default"). */
export function styleLabel(style: string): string {
  if (style === '') return style

  return style.charAt(0).toUpperCase() + style.slice(1)
}

export interface GeneralSectionProps {
  onLanguage: (language: string) => void
  onStyle: (style: string) => void
  onTheme: (preference: ThemePreference) => void
  /** False before the first session: the session-side rows need one to talk to. */
  sessionLive: boolean
  settings: GeneralSettingsResult
  theme: ThemePreference
}

/**
 * The general section: appearance, response language, output style.
 *
 * Appearance is a property of THIS BROWSER (localStorage), and the other two
 * are properties of the session — the layout says so by disabling the session
 * rows until a session exists, while the theme row always works.
 */
export function GeneralSection({
  onLanguage,
  onStyle,
  onTheme,
  sessionLive,
  settings,
  theme,
}: GeneralSectionProps) {
  const [draft, setDraft] = useState(settings.language ?? '')

  // The stored language arrives async; a draft the user has not touched
  // follows it, one they have does not get overwritten mid-edit.
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!touched) setDraft(settings.language ?? '')
  }, [settings.language, touched])

  const styles = settings.available_output_styles ?? []

  return (
    <div className={css.section}>
      <div className={css.row}>
        <div className={css.rowHead}>
          <span className={css.rowName}>Appearance</span>
        </div>
        <div className={css.rowMeta}>
          <span>How this browser draws the app. Other windows keep their own choice.</span>
        </div>
        <div className={css.choiceRow}>
          {THEMES.map(entry => (
            <button
              aria-pressed={theme === entry.id}
              className={[css.choice, theme === entry.id ? css.choiceOn : '']
                .filter(Boolean)
                .join(' ')}
              key={entry.id}
              onClick={() => {
                onTheme(entry.id)
              }}
              type="button"
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>

      <div className={css.row}>
        <div className={css.rowHead}>
          <span className={css.rowName}>Response language</span>
        </div>
        <div className={css.rowMeta}>
          <span>
            {sessionLive
              ? 'The language the agent answers in. Leave empty to follow your prompts.'
              : 'Start a session to change this.'}
          </span>
        </div>
        <div className={css.keyRow}>
          <input
            aria-label="Response language"
            className={css.keyInput}
            disabled={!sessionLive}
            onChange={event => {
              setTouched(true)
              setDraft(event.target.value)
            }}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                setTouched(false)
                onLanguage(draft.trim())
              }
            }}
            placeholder="e.g. English, 中文, Español"
            value={draft}
          />
          <Button
            disabled={!sessionLive || draft.trim() === (settings.language ?? '')}
            onClick={() => {
              setTouched(false)
              onLanguage(draft.trim())
            }}
            size="sm"
            variant="primary"
          >
            Save
          </Button>
          {(settings.language ?? '') !== '' && (
            <Button
              disabled={!sessionLive}
              onClick={() => {
                setTouched(false)
                setDraft('')
                onLanguage('')
              }}
              size="sm"
              variant="ghost"
            >
              Clear
            </Button>
          )}
        </div>
      </div>

      <div className={css.row}>
        <div className={css.rowHead}>
          <span className={css.rowName}>Output style</span>
        </div>
        <div className={css.rowMeta}>
          <span>
            {sessionLive
              ? 'How the agent writes: default keeps it terse, explanatory teaches as it goes.'
              : 'Start a session to change this.'}
          </span>
        </div>
        {styles.length > 0 && (
          <div className={css.choiceRow}>
            {styles.map(style => (
              <button
                aria-pressed={settings.output_style === style}
                className={[css.choice, settings.output_style === style ? css.choiceOn : '']
                  .filter(Boolean)
                  .join(' ')}
                disabled={!sessionLive}
                key={style}
                onClick={() => {
                  if (style !== settings.output_style) onStyle(style)
                }}
                type="button"
              >
                {styleLabel(style)}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
