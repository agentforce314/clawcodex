import { Box, Text, useInput, wrapAnsi } from '@clawcodex/ink'
import { useState } from 'react'

import { isMac } from '../lib/platform.js'
import type { Theme } from '../theme.js'
import type { ApprovalReq, ClarifyReq, ConfirmReq } from '../types.js'

import { TextInput } from './textInput.js'

type ApprovalChoice = 'always' | 'deny' | 'once'

// Original Claude Code's 3-option model: Yes / Yes-and-don't-ask-again / No.
// "always" persists the (editable) rule to disk so it survives across sessions.
const APPROVAL_OPTS: readonly ApprovalChoice[] = ['once', 'always', 'deny']
// No persistable rule (tirith warning, or backend sent no suggestion) → drop
// the "don't ask again" option.
const APPROVAL_OPTS_NO_ALWAYS: readonly ApprovalChoice[] = ['once', 'deny']
const LABELS: Record<ApprovalChoice, string> = {
  always: "Yes, and don't ask again for",
  deny: 'No',
  once: 'Yes'
}
const CMD_PREVIEW_LINES = 10

type ApprovalKey = {
  downArrow?: boolean
  escape?: boolean
  return?: boolean
  upArrow?: boolean
}

type ApprovalAction = { kind: 'choose'; choice: ApprovalChoice } | { kind: 'move'; delta: -1 | 1 } | { kind: 'noop' }

/**
 * Pure key-dispatch for the approval prompt — exported so the regression
 * matrix (Esc, Ctrl+C-equivalent, number keys, Enter, ↑↓) is testable
 * without mounting React + Ink + a fake stdin.  The component just maps the
 * action onto its own state setters.
 *
 * Esc and number keys both terminate the prompt; Esc maps to deny (parity
 * with the global Ctrl+C handler that already calls cancelOverlayFromCtrlC
 * for approvals).  Numbers 1..opts.length pick the labelled choice.  Enter
 * confirms the current selection.  ↑/↓ moves the selection within bounds.
 */
export function approvalAction(
  ch: string,
  key: ApprovalKey,
  sel: number,
  opts: readonly ApprovalChoice[] = APPROVAL_OPTS
): ApprovalAction {
  if (key.escape) {
    return { kind: 'choose', choice: 'deny' }
  }

  const n = parseInt(ch, 10)

  if (n >= 1 && n <= opts.length) {
    return { kind: 'choose', choice: opts[n - 1]! }
  }

  if (key.return) {
    return { kind: 'choose', choice: opts[sel]! }
  }

  if (key.upArrow && sel > 0) {
    return { kind: 'move', delta: -1 }
  }

  if (key.downArrow && sel < opts.length - 1) {
    return { kind: 'move', delta: 1 }
  }

  return { kind: 'noop' }
}

export function ApprovalPrompt({ cols = 80, onChoice, req, t }: ApprovalPromptProps) {
  const opts = req.allowPermanent === false || !req.rule ? APPROVAL_OPTS_NO_ALWAYS : APPROVAL_OPTS
  const [sel, setSel] = useState(0)
  // The editable grant rule (e.g. "git status:*"), which the user can widen to
  // "git:*" so one grant covers the family. Seeded from the backend suggestion.
  const [ruleText, setRuleText] = useState(req.rule ?? '')
  const [editing, setEditing] = useState(false)
  const alwaysIdx = opts.indexOf('always')

  const confirm = (choice: ApprovalChoice) =>
    onChoice(choice, choice === 'always' ? ruleText.trim() || req.rule : undefined)

  // Navigation — disabled while the rule field is focused (TextInput owns input).
  useInput(
    (ch, key) => {
      // On the "don't ask again" row, open the editable rule field.
      if (opts[sel] === 'always' && (key.tab || key.rightArrow || ch === 'e')) {
        setEditing(true)
        return
      }
      const action = approvalAction(ch, key, sel, opts)
      if (action.kind === 'choose') confirm(action.choice)
      else if (action.kind === 'move') setSel(s => s + action.delta)
    },
    { isActive: !editing }
  )
  // While editing, Esc cancels the edit (back to the option list), not deny.
  useInput((_ch, key) => { if (key.escape) setEditing(false) }, { isActive: editing })

  // Border + paddingX ≈ 4 cols. Show the real command, wrapped, not clipped.
  const innerWidth = Math.max(20, cols - 4)
  const rawLines = req.command
    .split('\n')
    .flatMap(line => wrapAnsi(line, innerWidth, { hard: true, trim: false }).split('\n'))
  const shown = rawLines.slice(0, CMD_PREVIEW_LINES)
  const overflow = rawLines.length - shown.length

  return (
    <Box borderColor={t.color.warn} borderStyle="round" flexDirection="column" paddingX={1}>
      <Text bold color={t.color.warn}>{req.toolName} command</Text>

      <Box flexDirection="column" paddingLeft={1}>
        {shown.map((line, i) => (
          <Text color={t.color.text} key={i} wrap="truncate-end">{line || ' '}</Text>
        ))}
        {overflow > 0 ? (
          <Text color={t.color.muted}>… +{overflow} more line{overflow === 1 ? '' : 's'}</Text>
        ) : null}
      </Box>

      <Text color={t.color.muted}>Do you want to proceed?</Text>

      {opts.map((o, i) => {
        const isSel = sel === i
        const head = `${isSel ? '❯ ' : '  '}${i + 1}. `
        if (o === 'always') {
          return (
            <Box key={o}>
              <Text bold={isSel} color={isSel ? t.color.warn : t.color.muted}>{head}{LABELS.always} </Text>
              {editing ? (
                <TextInput columns={innerWidth} focus onChange={setRuleText} onSubmit={() => confirm('always')} value={ruleText} />
              ) : (
                <Text bold={isSel} color={isSel ? t.color.text : t.color.muted}>{ruleText || req.ruleLabel || ''}</Text>
              )}
            </Box>
          )
        }
        return (
          <Text bold={isSel} color={isSel ? t.color.warn : t.color.muted} key={o}>{head}{LABELS[o]}</Text>
        )
      })}

      <Text color={t.color.muted}>
        {editing
          ? 'Enter to allow · Esc to cancel edit'
          : `↑/↓ select · Enter confirm${alwaysIdx >= 0 ? ' · e edit rule' : ''} · Esc deny`}
      </Text>
    </Box>
  )
}

export function ClarifyPrompt({ cols = 80, onAnswer, onCancel, req, t }: ClarifyPromptProps) {
  const [sel, setSel] = useState(0)
  const [custom, setCustom] = useState('')
  const [typing, setTyping] = useState(false)
  const choices = req.choices ?? []

  const heading = (
    <Text bold>
      <Text color={t.color.accent}>ask</Text>
      <Text color={t.color.text}> {req.question}</Text>
    </Text>
  )

  useInput((ch, key) => {
    if (key.escape) {
      typing && choices.length ? setTyping(false) : onCancel()

      return
    }

    if (typing || !choices.length) {
      return
    }

    if (key.upArrow && sel > 0) {
      setSel(s => s - 1)
    }

    if (key.downArrow && sel < choices.length) {
      setSel(s => s + 1)
    }

    if (key.return) {
      sel === choices.length ? setTyping(true) : choices[sel] && onAnswer(choices[sel]!)
    }

    const n = parseInt(ch)

    if (n >= 1 && n <= choices.length) {
      onAnswer(choices[n - 1]!)
    }
  })

  if (typing || !choices.length) {
    return (
      <Box flexDirection="column">
        {heading}

        <Box>
          <Text color={t.color.label}>{'> '}</Text>
          <TextInput columns={Math.max(20, cols - 6)} onChange={setCustom} onSubmit={onAnswer} value={custom} />
        </Box>

        <Text color={t.color.muted}>
          Enter send · Esc {choices.length ? 'back' : 'cancel'} ·{' '}
          {isMac ? 'Cmd+C copy · Cmd+V paste · Ctrl+C cancel' : 'Ctrl+C cancel'}
        </Text>
      </Box>
    )
  }

  return (
    <Box flexDirection="column">
      {heading}

      {[...choices, 'Other (type your answer)'].map((c, i) => (
        <Text key={i}>
          <Text bold={sel === i} color={sel === i ? t.color.label : t.color.muted} inverse={sel === i}>
            {sel === i ? '▸ ' : '  '}
            {i + 1}. {c}
          </Text>
        </Text>
      ))}

      <Text color={t.color.muted}>↑/↓ select · Enter confirm · 1-{choices.length} quick pick · Esc/Ctrl+C cancel</Text>
    </Box>
  )
}

export function ConfirmPrompt({ onCancel, onConfirm, req, t }: ConfirmPromptProps) {
  const [sel, setSel] = useState(0)

  useInput((ch, key) => {
    const lower = ch.toLowerCase()

    if (key.escape || (key.ctrl && lower === 'c') || lower === 'n') {
      return onCancel()
    }

    if (lower === 'y') {
      return onConfirm()
    }

    if (key.upArrow) {
      setSel(0)
    }

    if (key.downArrow) {
      setSel(1)
    }

    if (key.return) {
      sel === 0 ? onCancel() : onConfirm()
    }
  })

  const accent = req.danger ? t.color.error : t.color.warn

  const rows = [
    { color: t.color.text, label: req.cancelLabel ?? 'No' },
    { color: req.danger ? t.color.error : t.color.text, label: req.confirmLabel ?? 'Yes' }
  ]

  return (
    <Box borderColor={accent} borderStyle="double" flexDirection="column" paddingX={1}>
      <Text bold color={accent}>
        {req.danger ? '⚠' : '?'} {req.title}
      </Text>

      {req.detail ? (
        <Box paddingLeft={1}>
          <Text color={t.color.text} wrap="truncate-end">
            {req.detail}
          </Text>
        </Box>
      ) : null}

      <Text />

      {rows.map((row, i) => (
        <Text key={row.label}>
          <Text color={sel === i ? accent : t.color.muted}>{sel === i ? '▸ ' : '  '}</Text>
          <Text color={sel === i ? row.color : t.color.muted}>{row.label}</Text>
        </Text>
      ))}

      <Text color={t.color.muted}>↑/↓ select · Enter confirm · Y/N quick · Esc cancel</Text>
    </Box>
  )
}

interface ApprovalPromptProps {
  cols?: number
  onChoice: (s: string, rule?: string) => void
  req: ApprovalReq
  t: Theme
}

interface ClarifyPromptProps {
  cols?: number
  onAnswer: (s: string) => void
  onCancel: () => void
  req: ClarifyReq
  t: Theme
}

interface ConfirmPromptProps {
  onCancel: () => void
  onConfirm: () => void
  req: ConfirmReq
  t: Theme
}
