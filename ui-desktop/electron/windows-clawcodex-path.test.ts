// Unit tests for the pure Windows `clawcodex` resolution helpers extracted from
// main.ts's findOnPath(), handOffWindowsBootstrapRecovery(), and
// unwrapWindowsVenvClawCodexCommand(). These pin the two Windows resolution bugs
// that caused desktop reinstall loops:
//   1. buildPathExtCandidates() — PATHEXT extensions must be tried BEFORE the
//      empty extension, or an extensionless Git-Bash `clawcodex` shim shadows
//      the real clawcodex.cmd/clawcodex.exe.
//   2. chooseUpdaterArgs() — must gate on haveRealInstall (any real-install
//      signal), not just the clawcodex.exe console-script shim, or healthy
//      installs get forced into a destructive --repair.
//   3. resolveVenvClawCodexCommand() — must probe the venv python via
//      canImportClawCodexCli() before trusting it, or a broken venv gets
//      re-selected forever instead of falling through to bootstrap.

import assert from 'node:assert/strict'
import path from 'node:path'

import { test } from 'vitest'

import {
  buildPathExtCandidates,
  chooseUpdaterArgs,
  getVenvSitePackagesEntries,
  resolveVenvClawCodexCommand
} from './windows-clawcodex-path'

test('buildPathExtCandidates: Windows tries PATHEXT extensions before the empty extension', () => {
  const extensions = buildPathExtCandidates('.COM;.EXE;.BAT;.CMD', true)

  assert.deepEqual(extensions, ['.COM', '.EXE', '.BAT', '.CMD', ''])
  assert.equal(extensions[extensions.length - 1], '', 'empty extension must be last, not first')
  assert.notEqual(extensions[0], '', 'the buggy empty-extension-first order must not return')
})

test('buildPathExtCandidates: defaults to .COM;.EXE;.BAT;.CMD when PATHEXT is unset on Windows', () => {
  assert.deepEqual(buildPathExtCandidates(undefined, true), ['.COM', '.EXE', '.BAT', '.CMD', ''])
})

test('buildPathExtCandidates: respects a custom PATHEXT, still empty-last', () => {
  assert.deepEqual(buildPathExtCandidates('.EXE;.PS1', true), ['.EXE', '.PS1', ''])
})

test('buildPathExtCandidates: non-Windows only tries the bare name', () => {
  assert.deepEqual(buildPathExtCandidates('.COM;.EXE;.BAT;.CMD', false), [''])
  assert.deepEqual(buildPathExtCandidates(undefined, false), [''])
})

test('chooseUpdaterArgs: gentle --update when a real-install signal is present', () => {
  assert.deepEqual(chooseUpdaterArgs(true, 'main'), ['--update', '--branch', 'main'])
})

test('chooseUpdaterArgs: destructive --repair only when NO real-install signal is present', () => {
  assert.deepEqual(chooseUpdaterArgs(false, 'main'), ['--repair', '--branch', 'main'])
})

test('chooseUpdaterArgs: passes the branch through unchanged in both cases', () => {
  assert.deepEqual(chooseUpdaterArgs(true, 'release/1.2'), ['--update', '--branch', 'release/1.2'])
  assert.deepEqual(chooseUpdaterArgs(false, 'release/1.2'), ['--repair', '--branch', 'release/1.2'])
})

function makeDeps(overrides: Partial<Parameters<typeof resolveVenvClawCodexCommand>[2]> = {}) {
  return {
    isWindows: true,
    isCommandScript: () => false,
    fileExists: () => true,
    directoryExists: () => false,
    canImportClawCodexCli: () => true,
    getVenvPython: (venvRoot: string) => `${venvRoot}/Scripts/python.exe`,
    getVenvSitePackagesEntries: () => [],
    buildDesktopBackendEnv: () => ({ FAKE_ENV: '1' }),
    clawcodexHome: '/fake/clawcodex-home',
    resolvePath: (...segments: string[]) => segments.join('/').replace(/\/+/g, '/'),
    dirname: (p: string) => p.slice(0, p.lastIndexOf('/')) || '/',
    basename: (p: string) => p.slice(p.lastIndexOf('/') + 1),
    rememberLog: () => {},
    ...overrides
  }
}

test('resolveVenvClawCodexCommand: returns null off Windows', () => {
  const deps = makeDeps({ isWindows: false })

  assert.equal(resolveVenvClawCodexCommand('/root/venv/Scripts/clawcodex.exe', [], deps), null)
})

test('resolveVenvClawCodexCommand: returns null for a .cmd/.bat script command', () => {
  const deps = makeDeps({ isCommandScript: () => true })

  assert.equal(resolveVenvClawCodexCommand('/root/venv/Scripts/clawcodex.cmd', [], deps), null)
})

test('resolveVenvClawCodexCommand: returns null when the basename is not clawcodex/clawcodex.exe', () => {
  const deps = makeDeps()

  assert.equal(resolveVenvClawCodexCommand('/root/venv/Scripts/python.exe', [], deps), null)
})

test('resolveVenvClawCodexCommand: returns null when the parent dir is not Scripts', () => {
  const deps = makeDeps()

  assert.equal(resolveVenvClawCodexCommand('/root/venv/bin/clawcodex.exe', [], deps), null)
})

test('resolveVenvClawCodexCommand: returns null when the venv python does not exist on disk', () => {
  const deps = makeDeps({ fileExists: () => false })

  assert.equal(resolveVenvClawCodexCommand('/root/venv/Scripts/clawcodex.exe', [], deps), null)
})

test('resolveVenvClawCodexCommand: probes the venv python before trusting it (returns null on failed probe)', () => {
  let probed = false

  const deps = makeDeps({
    canImportClawCodexCli: (python: string) => {
      probed = true
      assert.equal(python, '/root/venv/Scripts/python.exe')

      return false
    }
  })

  const result = resolveVenvClawCodexCommand('/root/venv/Scripts/clawcodex.exe', ['serve'], deps)

  assert.equal(probed, true, 'must probe the venv interpreter; a broken venv must not be re-selected forever')
  assert.equal(result, null, 'a failed probe must fall through (return null) so the resolver reaches bootstrap')
})

test('resolveVenvClawCodexCommand: returns the resolved python backend descriptor when the probe passes', () => {
  const deps = makeDeps()
  const result = resolveVenvClawCodexCommand('/root/venv/Scripts/clawcodex.exe', ['serve', '--port', '0'], deps)

  assert.ok(result, 'a passing probe must return a backend descriptor, not null')
  assert.equal(result.command, '/root/venv/Scripts/python.exe')
  assert.deepEqual(result.args, ['-m', 'clawcodex_cli.main', 'serve', '--port', '0'])
  assert.equal(result.bootstrap, false)
  assert.equal(result.kind, 'python')
  assert.equal(result.shell, false)
  assert.deepEqual(result.env, { FAKE_ENV: '1' })
})

test('resolveVenvClawCodexCommand: is case-insensitive on clawcodex.exe and the Scripts dir name', () => {
  const deps = makeDeps()

  assert.ok(resolveVenvClawCodexCommand('/root/venv/Scripts/CLAWCODEX.EXE', [], deps))
  assert.ok(resolveVenvClawCodexCommand('/root/venv/SCRIPTS/clawcodex.exe', [], deps))
})

// ── getVenvSitePackagesEntries ─────────────────────────────────────────────

test('getVenvSitePackagesEntries: returns Lib/site-packages on Windows when it exists', () => {
  const expected = path.join('C:\\venv', 'Lib', 'site-packages')

  const result = getVenvSitePackagesEntries('C:\\venv', {
    isWindows: true,
    directoryExists: p => p === expected
  })

  assert.deepEqual(result, [expected])
})

test('getVenvSitePackagesEntries: returns empty on Windows when site-packages does not exist', () => {
  const result = getVenvSitePackagesEntries('C:\\venv', {
    isWindows: true,
    directoryExists: () => false
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: reads pyvenv.cfg version on POSIX and resolves lib/pythonX.Y/site-packages', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: p => p === '/venv/lib/python3.12/site-packages',
    readFile: () => 'version_info = 3.12.1\n'
  })

  assert.deepEqual(result, ['/venv/lib/python3.12/site-packages'])
})

test('getVenvSitePackagesEntries: returns empty on POSIX when pyvenv.cfg is missing', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: () => true,
    readFile: () => undefined
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: returns empty on POSIX when pyvenv.cfg has no version_info', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: () => true,
    readFile: () => 'home = /usr/bin\n'
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: returns empty on POSIX when version is present but site-packages dir is absent', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: () => false,
    readFile: () => 'version_info = 3.11\n'
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: returns empty for a falsy venvRoot', () => {
  assert.deepEqual(getVenvSitePackagesEntries('', { isWindows: true, directoryExists: () => true }), [])
  assert.deepEqual(getVenvSitePackagesEntries(null, { isWindows: true, directoryExists: () => true }), [])
  assert.deepEqual(getVenvSitePackagesEntries(undefined, { isWindows: true, directoryExists: () => true }), [])
})
