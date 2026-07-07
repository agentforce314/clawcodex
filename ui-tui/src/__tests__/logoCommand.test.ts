import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { findSlashCommand } from '../app/slash/registry.js'
import type { SlashRunCtx } from '../app/slash/types.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'

/** Minimal SlashRunCtx for the /logo command: transcript + gateway.rpc +
 *  the guarded() wrappers createSlashHandler builds per invocation. */
const makeCtx = (rpcResult: unknown) => {
  const sys = vi.fn()
  const rpc = vi.fn().mockResolvedValue(rpcResult)

  const ctx = {
    gateway: { rpc },
    guarded:
      <T>(fn: (r: T) => void) =>
      (r: null | T) => {
        if (r) {
          fn(r)
        }
      },
    guardedErr: vi.fn(),
    transcript: { sys }
  } as unknown as SlashRunCtx

  return { ctx, rpc, sys }
}

const logoCmd = findSlashCommand('logo')!

describe('/logo TUI-local command', () => {
  beforeEach(() => {
    resetOverlayState()
    resetUiState()
    // The store seeds logoPalette from the developer's real config at module
    // init — pin it for determinism.
    patchUiState({ logoPalette: '' })
  })

  it('is registered with the palette grammar', () => {
    expect(logoCmd).toBeDefined()
    expect(logoCmd.argumentHint).toBe('[sunset|forest|ocean|monochrome]')
  })

  it('bare /logo opens the picker overlay (the original local-jsx LogoPicker)', () => {
    const { ctx, rpc } = makeCtx({ ok: true })
    logoCmd.run('', ctx, '/logo')

    expect(getOverlayState().logoPicker).toBe(true)
    expect(rpc).not.toHaveBeenCalled()
  })

  it('/logo <name> persists via config.set logoColor and confirms next-launch visibility', async () => {
    const { ctx, rpc, sys } = makeCtx({ ok: true, value: 'forest' })
    logoCmd.run('forest', ctx, '/logo forest')

    expect(rpc).toHaveBeenCalledWith('config.set', { key: 'logoColor', value: 'forest' })

    // TS-verbatim confirmation: the intro banner is a committed transcript
    // row the renderer never re-emits, so like the original the change is
    // next-launch-only. (The screen repaint is covered by the live pyte e2e's
    // relaunch phase, not by this store-level test.)
    await vi.waitFor(() => expect(sys).toHaveBeenCalledWith('Startup logo set to Forest green. Visible on next launch.'))
    // Patched on success so the picker's "· current" marker tracks what was
    // actually persisted.
    expect(getUiState().logoPalette).toBe('forest')
  })

  it('reports failure without claiming success when the backend could not persist', async () => {
    const { ctx, sys } = makeCtx({ ok: false })
    logoCmd.run('ocean', ctx, '/logo ocean')

    await vi.waitFor(() =>
      expect(sys).toHaveBeenCalledWith('Could not persist the startup logo (backend not ready) — try again shortly.')
    )
    // Not persisted → the picker's current marker must not move.
    expect(getUiState().logoPalette).toBe('')
  })

  it('rejects unknown palettes with usage and touches nothing', () => {
    const { ctx, rpc, sys } = makeCtx({ ok: true })
    logoCmd.run('lava', ctx, '/logo lava')

    expect(sys).toHaveBeenCalledWith('usage: /logo [sunset|forest|ocean|monochrome]')
    expect(getUiState().logoPalette).toBe('')
    expect(getOverlayState().logoPicker).toBe(false)
    expect(rpc).not.toHaveBeenCalled()
  })
})
