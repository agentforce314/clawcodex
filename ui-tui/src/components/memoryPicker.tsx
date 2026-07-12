import { Box, Text, useInput, useStdout } from '@clawcodex/ink'
import { useEffect, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { MemoryTarget, MemoryTargetsResponse } from '../gatewayTypes.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { OverlayHint, windowItems } from './overlayControls.js'

const VISIBLE = 10
const MIN_WIDTH = 40
const MAX_WIDTH = 90

// The TS MemoryFileSelector remembers the previously chosen file across
// opens (module-level `lastSelectedPath`) — same idiom here.
let lastSelectedPath: string | undefined

/**
 * Interactive /memory picker overlay — port of openclaude's `/memory` dialog
 * (`commands/memory/memory.tsx` + the core of `MemoryFileSelector`). Rows come
 * from the backend's `memory_targets` control (the shared
 * `build_memory_options` hierarchy): the synthetic **User memory** and
 * **Project memory** candidates first, then every loaded memory file, each
 * differentiated by its dim description ("Saved in …", "@-imported"). Enter
 * hands the chosen path to `onSelect` (the app ensure-creates it and spawns
 * `$EDITOR` under the alt-screen suspend); Esc/q cancels.
 */
export function MemoryPicker({ gw, onCancel, onSelect, t }: MemoryPickerProps) {
  const [targets, setTargets] = useState<MemoryTarget[]>([])
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [idx, setIdx] = useState(0)

  const { stdout } = useStdout()
  const width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - 6))

  useEffect(() => {
    gw.request<MemoryTargetsResponse>('memory.targets', {})
      .then(raw => {
        const r = asRpcResult<MemoryTargetsResponse>(raw)

        if (!r || r.ok === false || !Array.isArray(r.targets) || !r.targets.length) {
          setErr(r?.error || 'no memory files reported by the backend')
          setLoading(false)

          return
        }

        setTargets(r.targets)
        setIdx(Math.max(0, r.targets.findIndex(target => target.path === lastSelectedPath)))
        setLoading(false)
      })
      .catch((e: unknown) => {
        setErr(rpcErrorMessage(e))
        setLoading(false)
      })
  }, [gw])

  useInput((ch, key) => {
    if (key.escape || ch === 'q') {
      return onCancel()
    }

    if (key.upArrow) {
      return setIdx(i => Math.max(0, i - 1))
    }

    if (key.downArrow) {
      return setIdx(i => Math.min(Math.max(0, targets.length - 1), i + 1))
    }

    if (key.return) {
      const target = targets[idx]

      if (target) {
        lastSelectedPath = target.path
        onSelect(target.path)
      }
    }
  })

  if (loading) {
    return <Text color={t.color.muted}>loading memory files…</Text>
  }

  if (err) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.label}>error: {err}</Text>
        <OverlayHint t={t}>Esc/q cancel</OverlayHint>
      </Box>
    )
  }

  const { items, offset } = windowItems(targets, idx, VISIBLE)

  return (
    <Box flexDirection="column" width={width}>
      <Text bold color={t.color.accent} wrap="truncate-end">
        Memory
      </Text>

      <Text color={t.color.muted} wrap="truncate-end">
        Select a memory file to edit
      </Text>

      <Text color={t.color.muted} wrap="truncate-end">
        {offset > 0 ? ` ↑ ${offset} more` : ' '}
      </Text>

      {items.map((target, i) => {
        const at = offset + i === idx

        return (
          <Text
            bold={at}
            color={at ? t.color.accent : t.color.text}
            inverse={at}
            key={target.path}
            wrap="truncate-end"
          >
            {at ? '▸ ' : '  '}
            {target.label}
            {target.description ? <Text color={at ? undefined : t.color.muted}> · {target.description}</Text> : null}
          </Text>
        )
      })}

      <Text color={t.color.muted} wrap="truncate-end">
        {offset + VISIBLE < targets.length ? ` ↓ ${targets.length - offset - VISIBLE} more` : ' '}
      </Text>

      <OverlayHint t={t}>↑/↓ select · Enter edit in $EDITOR · Esc/q cancel</OverlayHint>
    </Box>
  )
}

interface MemoryPickerProps {
  gw: GatewayClient
  onCancel: () => void
  onSelect: (path: string) => void
  t: Theme
}
