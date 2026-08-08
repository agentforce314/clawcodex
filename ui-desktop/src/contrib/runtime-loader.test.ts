import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as ClawCodexModule from '@/clawcodex'
import type { ClawCodexReadDirResult } from '@/global'

import { discoverRuntimePlugins, watchRuntimePlugins } from './runtime-loader'

// getStatus would supply the connected backend's clawcodex_home — a REMOTE path in
// remote mode. The disk scanner must NOT derive the plugin root from it (#66899).
const getStatus = vi.fn(async () => ({ clawcodex_home: '/remote/box/.clawcodex' }))

vi.mock('@/clawcodex', async importActual => ({
  ...(await importActual<typeof ClawCodexModule>()),
  getStatus: () => getStatus()
}))

const desktopPluginsRoot = vi.fn<() => Promise<string>>()
const readDir = vi.fn<(path: string) => Promise<ClawCodexReadDirResult>>()
const watchDirectory = vi.fn<(path: string) => Promise<{ id: string }>>()
const onPreviewFileChanged = vi.fn()

beforeEach(() => {
  desktopPluginsRoot.mockReset()
  readDir.mockReset()
  watchDirectory.mockReset()
  onPreviewFileChanged.mockReset()
  getStatus.mockClear()
  ;(window as unknown as { clawcodexDesktop: unknown }).clawcodexDesktop = {
    desktopPluginsRoot,
    onPreviewFileChanged,
    readDir,
    watchDirectory
  }
})

afterEach(() => {
  delete (window as unknown as { clawcodexDesktop?: unknown }).clawcodexDesktop
})

describe('scanDiskPlugins (#66899)', () => {
  it('scans the Electron-resolved local root, never the backend clawcodex_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.clawcodex/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })

    await discoverRuntimePlugins()

    expect(desktopPluginsRoot).toHaveBeenCalled()
    expect(readDir).toHaveBeenCalledWith('/local/.clawcodex/desktop-plugins')
    // The remote backend's clawcodex_home must never feed the local plugin scan.
    expect(getStatus).not.toHaveBeenCalled()
    expect(readDir).not.toHaveBeenCalledWith('/remote/box/.clawcodex/desktop-plugins')
  })

  it('no-ops when the resolver yields no local root', async () => {
    desktopPluginsRoot.mockResolvedValue('')

    await discoverRuntimePlugins()

    expect(readDir).not.toHaveBeenCalled()
  })
})

describe('watchRuntimePlugins dir watch (#66899)', () => {
  it('watches the Electron-resolved local root, never the backend clawcodex_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.clawcodex/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })
    watchDirectory.mockResolvedValue({ id: 'watch-1' })

    watchRuntimePlugins()
    // Drain the async scan + startDirWatch chains.
    await vi.waitFor(() => expect(watchDirectory).toHaveBeenCalled())

    expect(watchDirectory).toHaveBeenCalledWith('/local/.clawcodex/desktop-plugins')
    expect(watchDirectory).not.toHaveBeenCalledWith('/remote/box/.clawcodex/desktop-plugins')
    expect(getStatus).not.toHaveBeenCalled()
  })
})
