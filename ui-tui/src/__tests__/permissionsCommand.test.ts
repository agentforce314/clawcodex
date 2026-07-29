import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { findSlashCommand } from '../app/slash/registry.js'
import type { SlashRunCtx } from '../app/slash/types.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import { PERMISSION_LEVELS } from '../lib/permissionLevels.js'

/** Minimal SlashRunCtx: transcript + gateway.rpc + the guarded() wrappers
 *  createSlashHandler builds per invocation (the /logo test's shape). */
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

const cmd = findSlashCommand('permissions')!

describe('/permissions TUI-local command', () => {
  beforeEach(() => {
    resetOverlayState()
    resetUiState()
  })

  it('is registered with the level grammar', () => {
    expect(cmd).toBeDefined()
    expect(cmd.argumentHint).toBe('[ask|approve|full]')
  })

  it('still answers to the old /mode name', () => {
    // The command was renamed; muscle memory and older docs should not 404.
    expect(findSlashCommand('mode')).toBe(cmd)
  })

  it('bare /permissions opens the picker overlay', () => {
    const { ctx, rpc } = makeCtx({ ok: true })
    cmd.run('', ctx, '/permissions')

    expect(getOverlayState().permissionsPicker).toBe(true)
    expect(rpc).not.toHaveBeenCalled()
  })

  it('maps each level key to its engine mode and persists it', async () => {
    for (const level of PERMISSION_LEVELS) {
      const { ctx, rpc, sys } = makeCtx({ mode: level.mode, ok: true, persisted: true })

      cmd.run(level.key, ctx, `/permissions ${level.key}`)

      expect(rpc).toHaveBeenCalledWith('config.set', {
        key: 'permission_mode',
        // Choosing a level is a standing preference — it must survive relaunch,
        // or a user who deliberately dials DOWN is silently returned to Full
        // Access next launch.
        persist: true,
        value: level.mode
      })
      await vi.waitFor(() => expect(sys).toHaveBeenCalledWith(`Permissions: ${level.label}.`))
      expect(getUiState().permissionMode).toBe(level.mode)
    }
  })

  it('persists a raw mode that IS a level, e.g. the settings.json spelling', async () => {
    // `/permissions default` is what a user copies out of settings.json. It
    // lands in the same state as `/permissions ask`, so it must be equally
    // durable — otherwise two spellings of one choice differ only in whether
    // they survive a relaunch.
    const { ctx, rpc } = makeCtx({ mode: 'default', ok: true, persisted: true })

    cmd.run('default', ctx, '/permissions default')

    expect(rpc).toHaveBeenCalledWith('config.set', {
      key: 'permission_mode',
      persist: true,
      value: 'default'
    })
  })

  it('accepts a raw engine mode as a power-user escape hatch, without persisting', async () => {
    // plan / dontAsk are real modes with no picker row. Persisting them would
    // make every future session start in plan mode, which nobody means by it.
    const { ctx, rpc, sys } = makeCtx({ mode: 'plan', ok: true })

    cmd.run('plan', ctx, '/permissions plan')

    expect(rpc).toHaveBeenCalledWith('config.set', {
      key: 'permission_mode',
      persist: false,
      value: 'plan'
    })
    await vi.waitFor(() => expect(sys).toHaveBeenCalledWith('Permission mode: plan.'))
  })

  it('badges the mode the SERVER confirmed, not the one requested', async () => {
    const { ctx, sys } = makeCtx({ mode: 'acceptEdits', ok: true })

    cmd.run('full', ctx, '/permissions full')

    await vi.waitFor(() => expect(sys).toHaveBeenCalledWith('Permissions: Approve for me.'))
    expect(getUiState().permissionMode).toBe('acceptEdits')
  })

  it('surfaces a server refusal and leaves the badge alone', async () => {
    patchUiState({ permissionMode: 'default' })

    const { ctx, sys } = makeCtx({ error: 'Full Access is not available in this session', ok: false })

    cmd.run('full', ctx, '/permissions full')

    await vi.waitFor(() =>
      expect(sys).toHaveBeenCalledWith('Full Access is not available in this session')
    )
    // A rejected set must not flip the composer indicator.
    expect(getUiState().permissionMode).toBe('default')
  })
})
