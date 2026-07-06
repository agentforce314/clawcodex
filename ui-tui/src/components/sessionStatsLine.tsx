/**
 * Persistent session-stats line — the last row under the composer, the
 * deleted REPL's bottom toolbar reborn:
 *
 *   deepseek · deepseek-v4-flash · ~/work/app · turns: 3 · tokens: 33189 in / 622 out · cost $0.0048
 *
 * Provider/model/cwd come from session.info; the accumulators refresh on
 * every end-of-turn result (createGatewayEventHandler → ui.sessionStats).
 * Hidden until the backend's init arrives — there is nothing to report
 * while "starting clawcodex…" still owns the status row.
 */
import { Box, Text } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'
import { memo } from 'react'

import { $uiState } from '../app/uiStore.js'
import { buildSessionStatsLine } from '../lib/sessionStats.js'

export const SessionStatsLine = memo(function SessionStatsLine({ cols }: { cols: number }) {
  const ui = useStore($uiState)

  if (!ui.info) {
    return null
  }

  // ComposerPane pads 1 column and this Box pads 2 more per side.
  const line = buildSessionStatsLine({
    cols: Math.max(1, cols - 6),
    cwd: ui.info.cwd ?? '',
    model: ui.info.model ?? '',
    provider: ui.info.profile_name ?? '',
    stats: ui.sessionStats
  })

  return (
    <Box paddingX={2}>
      <Text color={ui.theme.color.muted} wrap="truncate-end">
        {line}
      </Text>
    </Box>
  )
})
