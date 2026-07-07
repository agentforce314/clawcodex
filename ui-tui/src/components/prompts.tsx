import { Box, Text, useInput, wrapAnsi } from '@clawcodex/ink'
import { useState } from 'react'

import { isMac } from '../lib/platform.js'
import type { Theme } from '../theme.js'
import type { ApprovalReq, ClarifyReq, ConfirmReq, PlanApprovalReq } from '../types.js'

import { Md } from './markdown.js'
import { TextInput } from './textInput.js'

type ApprovalChoice = 'always' | 'deny' | 'once'

// Original Claude Code's 3-option model: Yes / Yes-and-don't-ask-again / No.
// "always" sends the backend's suggestion, which persists at its own intended
// scope (Bash → localSettings/disk; file edits → session acceptEdits; etc.).
const APPROVAL_OPTS: readonly ApprovalChoice[] = ['once', 'always', 'deny']
// No suggestion at all (tirith warning, allowPermanent=false) → drop the
// "don't ask again" option.
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

/**
 * Which options to show, and whether the grant rule is editable. Exported +
 * pure so the gating is testable without mounting Ink (this is the exact site
 * of a fixed regression: the persist option must appear for EVERY tool the
 * backend sent a suggestion for, not only Bash).
 *
 * - `allowPermanent` is the backend's "a suggestion exists" signal
 *   (`suggestions.length > 0`), false only for a tirith warning / no suggestion
 *   → no persist option.
 * - `editable` is Bash-only: only a Bash suggestion carries a `rule` to widen.
 */
export function approvalOptions(
  req: Pick<ApprovalReq, 'allowPermanent' | 'rule'>
): { editable: boolean; opts: readonly ApprovalChoice[] } {
  return {
    editable: !!req.rule,
    opts: req.allowPermanent === false ? APPROVAL_OPTS_NO_ALWAYS : APPROVAL_OPTS
  }
}

export function ApprovalPrompt({ cols = 80, onChoice, req, t }: ApprovalPromptProps) {
  const { editable, opts } = approvalOptions(req)
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
      // On the "don't ask again" row, open the editable rule field (Bash only —
      // other tools have no editable rule).
      if (editable && opts[sel] === 'always' && (key.tab || key.rightArrow || ch === 'e')) {
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

  // Border (2) + paddingX (2) + the command box's paddingLeft (1) = 5 cols.
  const innerWidth = Math.max(20, cols - 5)
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

      {req.warning ? (
        // Destructive-command caution (backend-computed, e.g. "Note: may
        // overwrite remote history") — parity with the original's dialog
        // warning now that destructive commands prompt through the ordinary
        // grantable flow.
        <Text bold color={t.color.warn}>⚠ {req.warning}</Text>
      ) : null}

      <Text color={t.color.muted}>Do you want to proceed?</Text>

      {opts.map((o, i) => {
        const isSel = sel === i
        const head = `${isSel ? '❯ ' : '  '}${i + 1}. `
        if (o === 'always') {
          // Bash edits the rule inline: "Yes, and don't ask again for [git:*]".
          if (editable) {
            const label = `${head}${LABELS.always} `
            const staticRule = ruleText || req.ruleLabel || req.toolName
            return (
              <Box key={o}>
                <Text bold={isSel} color={isSel ? t.color.warn : t.color.muted}>{label}</Text>
                {editing ? (
                  <TextInput columns={Math.max(12, innerWidth - label.length)} focus onChange={setRuleText} onSubmit={() => confirm('always')} value={ruleText} />
                ) : (
                  <Text bold={isSel} color={isSel ? t.color.text : t.color.muted}>{staticRule}</Text>
                )}
              </Box>
            )
          }
          // Other tools: the backend's authoritative per-tool wording, which
          // states the real scope ("Yes, allow all edits during this session"),
          // not a generic "don't ask again for <tool>".
          const text = req.sessionLabel
            ? `Yes, ${req.sessionLabel}`
            : `${LABELS.always} ${req.ruleLabel || req.toolName}`
          return (
            <Text bold={isSel} color={isSel ? t.color.warn : t.color.muted} key={o}>{head}{text}</Text>
          )
        }
        return (
          <Text bold={isSel} color={isSel ? t.color.warn : t.color.muted} key={o}>{head}{LABELS[o]}</Text>
        )
      })}

      <Text color={t.color.muted}>
        {editing
          ? 'Enter to allow · Esc to cancel edit'
          : `↑/↓ select · Enter confirm${alwaysIdx >= 0 && editable ? ' · e edit rule' : ''} · Esc deny`}
      </Text>
    </Box>
  )
}

// ── Plan approval (ExitPlanModePermissionRequest analog) ────────────────────

type PlanApprovalChoice = 'accept-edits' | 'bypass' | 'default' | 'deny'

const PLAN_PREVIEW_LINES = 24

/**
 * Pure option builder (exported for tests) — mirrors the original's
 * buildPlanApprovalOptions (open-build arms): the elevated approve reads
 * "bypass permissions" when the session launched with bypass available,
 * "auto-accept edits" otherwise; then manual approve; then reject-with-
 * feedback ("No, keep planning").
 */
export function planApprovalOptions(
  bypassAvailable: boolean
): { choice: PlanApprovalChoice; label: string }[] {
  return [
    bypassAvailable
      ? { choice: 'bypass', label: 'Yes, and bypass permissions' }
      : { choice: 'accept-edits', label: 'Yes, auto-accept edits' },
    { choice: 'default', label: 'Yes, manually approve edits' },
    { choice: 'deny', label: 'No, keep planning' }
  ]
}

export function PlanApprovalPrompt({ cols = 80, onChoice, req, t }: PlanApprovalPromptProps) {
  const isEmpty = !req.plan || !req.plan.trim()
  // Empty plan → the original's simplified "Exit plan mode?" Yes/No branch
  // (Yes approves with manual-approve mode; no feedback field).
  const opts: { choice: PlanApprovalChoice; label: string }[] = isEmpty
    ? [
        { choice: 'default', label: 'Yes' },
        { choice: 'deny', label: 'No' }
      ]
    : planApprovalOptions(req.bypassAvailable)
  const [sel, setSel] = useState(0)
  const [feedback, setFeedback] = useState('')
  const [typing, setTyping] = useState(false)

  const pick = (o: { choice: PlanApprovalChoice }) => {
    // "No, keep planning" focuses the feedback field first (full dialog
    // only) — Enter there submits the rejection with whatever was typed.
    if (o.choice === 'deny' && !isEmpty) {
      return setTyping(true)
    }

    return onChoice(o.choice)
  }

  useInput(
    (ch, key) => {
      // Esc = reject without feedback (stay in plan mode), matching the
      // original dialog's onCancel.
      if (key.escape) {
        return onChoice('deny')
      }

      const n = parseInt(ch, 10)

      if (n >= 1 && n <= opts.length) {
        return pick(opts[n - 1]!)
      }

      if (key.return) {
        return pick(opts[sel]!)
      }

      if (key.upArrow && sel > 0) {
        setSel(s => s - 1)
      }

      if (key.downArrow && sel < opts.length - 1) {
        setSel(s => s + 1)
      }
    },
    { isActive: !typing }
  )

  // Feedback field: Enter submits the rejection (with feedback), Esc returns
  // to the option list.
  useInput(
    (_ch, key) => {
      if (key.escape) {
        setTyping(false)
      }
    },
    { isActive: typing }
  )

  const innerWidth = Math.max(20, cols - 6)

  const optionRows = opts.map((o, i) => {
    const isSel = sel === i
    const head = `${isSel ? '❯ ' : '  '}${i + 1}. `

    if (o.choice === 'deny' && !isEmpty && (typing || feedback)) {
      const label = `${head}${o.label}: `

      return (
        <Box key={o.choice}>
          <Text bold={isSel} color={isSel ? t.color.planMode : t.color.muted}>{label}</Text>
          <TextInput
            columns={Math.max(12, innerWidth - label.length)}
            focus={typing}
            onChange={setFeedback}
            onSubmit={() => onChoice('deny', feedback.trim() || undefined)}
            value={feedback}
          />
        </Box>
      )
    }

    return (
      <Text bold={isSel} color={isSel ? t.color.planMode : t.color.muted} key={o.choice}>
        {head}{o.label}
      </Text>
    )
  })

  if (isEmpty) {
    return (
      <Box borderColor={t.color.planMode} borderStyle="round" flexDirection="column" paddingX={1}>
        <Text bold color={t.color.planMode}>Exit plan mode?</Text>
        <Text color={t.color.text}>Claude wants to exit plan mode</Text>
        {optionRows}
        <Text color={t.color.muted}>↑/↓ select · Enter confirm · Esc keep planning</Text>
      </Box>
    )
  }

  const planLines = (req.plan ?? '').split('\n')
  const shown = planLines.slice(0, PLAN_PREVIEW_LINES)
  const overflow = planLines.length - shown.length

  return (
    <Box borderColor={t.color.planMode} borderStyle="round" flexDirection="column" paddingX={1}>
      <Text bold color={t.color.planMode}>Ready to code?</Text>
      <Text color={t.color.text}>Here is Claude&apos;s plan:</Text>

      <Box borderColor={t.color.muted} borderStyle="single" flexDirection="column" paddingX={1}>
        <Md cols={innerWidth} t={t} text={shown.join('\n')} />
        {overflow > 0 ? (
          <Text color={t.color.muted}>… +{overflow} more line{overflow === 1 ? '' : 's'} · {req.planFilePath ?? ''}</Text>
        ) : null}
      </Box>

      <Text color={t.color.muted}>Would you like to proceed?</Text>

      {optionRows}

      <Text color={t.color.muted}>
        {typing
          ? 'Enter to reject with feedback · Esc back'
          : `↑/↓ select · Enter confirm · 1-${opts.length} quick pick · Esc keep planning`}
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

interface PlanApprovalPromptProps {
  cols?: number
  onChoice: (choice: PlanApprovalChoice, feedback?: string) => void
  req: PlanApprovalReq
  t: Theme
}

interface ConfirmPromptProps {
  onCancel: () => void
  onConfirm: () => void
  req: ConfirmReq
  t: Theme
}
