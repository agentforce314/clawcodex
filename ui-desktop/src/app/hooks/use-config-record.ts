import { useQuery } from '@tanstack/react-query'

import { getClawCodexConfigRecord } from '@/clawcodex'
import { queryClient, writeCache } from '@/lib/query-client'
import type { ClawCodexConfigRecord } from '@/types/clawcodex'

// One shared cache for the whole profile config record (`GET /api/config`).
// Every settings surface (MCP, model, config) reads and writes through this key
// so a save in one shows in the others, and revisiting a tab paints the cache
// instead of blanking on a fresh fetch.
//
// Distinct from session/hooks/use-clawcodex-config.ts, which is side-effecting —
// it pushes personality/cwd/voice/… into the session stores for live chat.
export const CLAWCODEX_CONFIG_KEY = ['clawcodex-config-record'] as const

// staleTime 0 → serve cache instantly, background-revalidate on every mount.
export const useClawCodexConfigRecord = () =>
  useQuery({ queryKey: CLAWCODEX_CONFIG_KEY, queryFn: getClawCodexConfigRecord, staleTime: 0 })

export const setClawCodexConfigCache = writeCache<ClawCodexConfigRecord>(CLAWCODEX_CONFIG_KEY)

export const invalidateClawCodexConfig = () => queryClient.invalidateQueries({ queryKey: CLAWCODEX_CONFIG_KEY })
