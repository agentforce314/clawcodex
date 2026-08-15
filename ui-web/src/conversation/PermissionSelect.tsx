import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { FullAccessDialog } from '../ui/FullAccessDialog.tsx'
import { Menu, type MenuEntry } from '../ui/primitives/Menu.tsx'
import {
  ChevronDownIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  ShieldPenIcon,
} from '../ui/icons.tsx'
import css from './Pickers.module.css'

export type ApprovalMode = 'manual' | 'off' | 'smart'

interface ModeSpec {
  danger?: boolean
  hint: string
  icon: ReactNode
  label: string
}

/**
 * The three modes, in increasing order of what the agent may do unasked —
 * which is also the order they are listed in, so the menu reads as a scale.
 */
const MODES: Record<ApprovalMode, ModeSpec> = {
  manual: {
    hint: 'Every tool asks first',
    icon: <ShieldCheckIcon size={16} />,
    label: 'Ask every time',
  },
  smart: {
    hint: 'Safe tools run; risky ones ask',
    icon: <ShieldPenIcon size={16} />,
    label: 'Smart approvals',
  },
  off: {
    danger: true,
    hint: 'Nothing asks — including writes and shell',
    icon: <ShieldAlertIcon size={16} />,
    label: 'Full access',
  },
}

const ORDER: ApprovalMode[] = ['manual', 'smart', 'off']

export interface PermissionSelectProps {
  disabled?: boolean
  onChange: (mode: ApprovalMode) => void
  /**
   * The session-reported mode; undefined until `session.info` carries one.
   * While undefined the picker displays 'off' — sessions spawn in Full Access
   * under the backend's implicit interactive default (same as the TUI), and a
   * deployment that dials that down corrects the display as soon as the
   * session reports its real mode.
   */
  value?: ApprovalMode
}

/**
 * Approval mode, as a themed menu with a guard on the dangerous end.
 *
 * Full access turns off every permission check for the session, so it is the
 * one option that asks for confirmation rather than switching on a single
 * click. The acknowledgement is a checkbox, not just a second button: the
 * point is to make the reader parse the sentence, and an "are you sure" dialog
 * people dismiss reflexively would not.
 */
export function PermissionSelect({ disabled = false, onChange, value }: PermissionSelectProps) {
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)

  // A mode change from elsewhere (a /permissions command, a session switch)
  // invalidates an in-flight confirmation.
  useEffect(() => {
    setConfirming(false)
  }, [value])

  const close = useCallback(() => {
    setOpen(false)
  }, [])

  const choose = useCallback(
    (id: string) => {
      const mode = id as ApprovalMode
      setOpen(false)

      // Only a KNOWN equal mode is a no-op. While `value` is undefined the
      // display is a guess, so every pick must reach `onChange` — otherwise a
      // deliberate pre-session "Full access" click would be swallowed by the
      // fallback that happens to show the same label.
      if (mode === value) return

      if (mode === 'off') {
        setConfirming(true)

        return
      }

      onChange(mode)
    },
    [onChange, value],
  )

  const shown: ApprovalMode = value ?? 'off'
  const current = MODES[shown] ?? MODES.manual
  const items: MenuEntry[] = ORDER.map(mode => ({
    danger: MODES[mode].danger,
    hint: MODES[mode].hint,
    icon: MODES[mode].icon,
    id: mode,
    label: MODES[mode].label,
  }))

  return (
    <>
      <Menu
        align="start"
        anchor={
          <button
            aria-expanded={open}
            aria-haspopup="menu"
            aria-label={`Approval mode: ${current.label}`}
            className={[css.trigger, shown === 'off' ? css.triggerDanger : '']
              .filter(Boolean)
              .join(' ')}
            disabled={disabled}
            onClick={() => {
              setOpen(value_ => !value_)
            }}
            title={current.hint}
            type="button"
          >
            <span className={css.triggerIcon}>{current.icon}</span>
            <span className={css.triggerLabel}>{current.label}</span>
            <span className={[css.chevron, open ? css.chevronOpen : ''].filter(Boolean).join(' ')}>
              <ChevronDownIcon size={12} />
            </span>
          </button>
        }
        items={items}
        onClose={close}
        onSelect={choose}
        open={open}
        selectedId={shown}
        side="top"
      />
      {confirming && (
        <FullAccessDialog
          acknowledgeLabel="I understand this session will not ask again."
          body={
            'In Full access the agent runs every tool without asking — editing ' +
            'and deleting files, and running shell commands — for the rest of ' +
            'this session.'
          }
          onCancel={() => {
            setConfirming(false)
          }}
          onConfirm={() => {
            setConfirming(false)
            onChange('off')
          }}
        />
      )}
    </>
  )
}
