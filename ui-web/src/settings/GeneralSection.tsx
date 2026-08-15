import { useEffect, useState } from 'react'

import type { ApprovalMode } from '../conversation/PermissionSelect.tsx'
import type { GeneralSettingsResult } from '../gateway/protocol.ts'
import { Button } from '../ui/primitives/Button.tsx'
import { FullAccessDialog } from '../ui/FullAccessDialog.tsx'
import { type ThemePreference } from '../state/theme.ts'
import css from './Settings.module.css'

const THEMES: { id: ThemePreference; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
]

/** Same vocabulary and order as the composer's picker: a scale of what runs unasked. */
const APPROVALS: { id: ApprovalMode; label: string }[] = [
  { id: 'manual', label: 'Ask every time' },
  { id: 'smart', label: 'Smart approvals' },
  { id: 'off', label: 'Full access' },
]

/** Title-case an output style name for its chip ("default" → "Default"). */
export function styleLabel(style: string): string {
  if (style === '') return style

  return style.charAt(0).toUpperCase() + style.slice(1)
}

export interface GeneralSectionProps {
  /**
   * The session's reported mode, or the choice held for the session that does
   * not exist yet; undefined when neither has said anything.
   */
  approvalMode?: ApprovalMode
  onApproval: (mode: ApprovalMode) => void
  onLanguage: (language: string) => void
  onRecap: (enabled: boolean) => void
  onStyle: (style: string) => void
  onTheme: (preference: ThemePreference) => void
  /** False before the first session: the session-side rows need one to talk to. */
  sessionLive: boolean
  settings: GeneralSettingsResult
  theme: ThemePreference
}

/**
 * The general section: appearance, approvals, response language, output
 * style, recap.
 *
 * Appearance is a property of THIS BROWSER (localStorage), and the last three
 * are properties of the session — the layout says so by disabling the session
 * rows until a session exists, while the theme row always works. Approvals is
 * neither: a pre-session choice is held and applied to the session that gets
 * created, so its row never disables.
 */
export function GeneralSection({
  approvalMode,
  onApproval,
  onLanguage,
  onRecap,
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

  const [confirmingFullAccess, setConfirmingFullAccess] = useState(false)

  useEffect(() => {
    if (!touched) setDraft(settings.language ?? '')
  }, [settings.language, touched])

  // A mode change from elsewhere (the composer picker, a /permissions
  // command) invalidates an in-flight confirmation.
  useEffect(() => {
    setConfirmingFullAccess(false)
  }, [approvalMode])

  const styles = settings.available_output_styles ?? []

  // Sessions spawn in Full Access under the backend's implicit interactive
  // default, so the unknown-mode display must not claim something stricter.
  const shownApproval: ApprovalMode = approvalMode ?? 'off'

  const chooseApproval = (mode: ApprovalMode): void => {
    // Only a KNOWN equal mode is a no-op. While `approvalMode` is undefined
    // the display is a guess, so every pick must reach `onApproval`.
    if (mode === approvalMode) return

    if (mode === 'off') {
      setConfirmingFullAccess(true)

      return
    }

    onApproval(mode)
  }

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
          <span className={css.rowName}>Approvals</span>
        </div>
        <div className={css.rowMeta}>
          <span>
            What the agent may run without asking
            {sessionLive ? '' : ', applied to the session you start'}. Ask every time and Full
            access persist as the default for future sessions; Smart approvals lasts one session.
          </span>
        </div>
        <div className={css.choiceRow}>
          {APPROVALS.map(entry => (
            <button
              aria-pressed={shownApproval === entry.id}
              className={[css.choice, shownApproval === entry.id ? css.choiceOn : '']
                .filter(Boolean)
                .join(' ')}
              key={entry.id}
              onClick={() => {
                chooseApproval(entry.id)
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

      <div className={css.row}>
        <div className={css.rowHead}>
          <span className={css.rowName}>End-of-turn recap</span>
        </div>
        <div className={css.rowMeta}>
          <span>
            {sessionLive
              ? 'A short end-of-turn summary of what changed, with a suggested next step.'
              : 'Start a session to change this.'}
          </span>
        </div>
        <div className={css.choiceRow}>
          {[true, false].map(enabled => (
            <button
              aria-pressed={settings.recap === enabled}
              className={[css.choice, settings.recap === enabled ? css.choiceOn : '']
                .filter(Boolean)
                .join(' ')}
              disabled={!sessionLive}
              key={enabled ? 'on' : 'off'}
              onClick={() => {
                if (settings.recap !== enabled) onRecap(enabled)
              }}
              type="button"
            >
              {enabled ? 'On' : 'Off'}
            </button>
          ))}
        </div>
      </div>

      {confirmingFullAccess && (
        <FullAccessDialog
          acknowledgeLabel="I understand this and future sessions will not ask."
          body={
            'In Full access the agent runs every tool without asking — editing ' +
            'and deleting files, and running shell commands. The choice also ' +
            'becomes the default mode for new sessions.'
          }
          onCancel={() => {
            setConfirmingFullAccess(false)
          }}
          onConfirm={() => {
            setConfirmingFullAccess(false)
            onApproval('off')
          }}
        />
      )}
    </div>
  )
}
