import assert from 'node:assert/strict'

import { test } from 'vitest'

import { expandWindowsEnvRefs, parseRegQueryValue, readWindowsUserEnvVar } from './windows-user-env'

// ── parseRegQueryValue ─────────────────────────────────────────────────────

test('parseRegQueryValue extracts a REG_SZ value', () => {
  const out = ['', 'HKEY_CURRENT_USER\\Environment', '    CLAWCODEX_CONFIG_DIR    REG_SZ    F:\\ClawCodex\\data', ''].join('\r\n')
  assert.equal(parseRegQueryValue(out, 'CLAWCODEX_CONFIG_DIR'), 'F:\\ClawCodex\\data')
})

test('parseRegQueryValue matches the name case-insensitively', () => {
  const out = 'HKEY_CURRENT_USER\\Environment\r\n    ClawCodex_Config_Dir    REG_EXPAND_SZ    %USERPROFILE%\\h\r\n'
  assert.equal(parseRegQueryValue(out, 'CLAWCODEX_CONFIG_DIR'), '%USERPROFILE%\\h')
})

test('parseRegQueryValue preserves spaces inside the value', () => {
  const out = '    CLAWCODEX_CONFIG_DIR    REG_SZ    C:\\Program Files\\ClawCodex\r\n'
  assert.equal(parseRegQueryValue(out, 'CLAWCODEX_CONFIG_DIR'), 'C:\\Program Files\\ClawCodex')
})

test('parseRegQueryValue returns null when the value line is absent', () => {
  const out = 'HKEY_CURRENT_USER\\Environment\r\n    Path    REG_SZ    C:\\x\r\n'
  assert.equal(parseRegQueryValue(out, 'CLAWCODEX_CONFIG_DIR'), null)
  assert.equal(parseRegQueryValue('', 'CLAWCODEX_CONFIG_DIR'), null)
  assert.equal(parseRegQueryValue('garbage', 'CLAWCODEX_CONFIG_DIR'), null)
})

// ── expandWindowsEnvRefs ───────────────────────────────────────────────────

test('expandWindowsEnvRefs expands %VAR% case-insensitively', () => {
  assert.equal(expandWindowsEnvRefs('%UserProfile%\\h', { USERPROFILE: 'C:\\Users\\jeff' }), 'C:\\Users\\jeff\\h')
})

test('expandWindowsEnvRefs leaves literal paths and unknown refs intact', () => {
  assert.equal(expandWindowsEnvRefs('F:\\ClawCodex\\data', {}), 'F:\\ClawCodex\\data')
  assert.equal(expandWindowsEnvRefs('%NOPE%\\x', {}), '%NOPE%\\x')
})

// ── readWindowsUserEnvVar ──────────────────────────────────────────────────

test('readWindowsUserEnvVar returns null off Windows without spawning', () => {
  let spawned = false

  const exec = () => {
    spawned = true

    return ''
  }

  assert.equal(readWindowsUserEnvVar('CLAWCODEX_CONFIG_DIR', { platform: 'linux', exec }), null)
  assert.equal(spawned, false)
})

test('readWindowsUserEnvVar queries HKCU\\Environment and expands the value', () => {
  const calls = []

  const exec = (cmd, args) => {
    calls.push([cmd, args])

    return 'HKEY_CURRENT_USER\\Environment\r\n    CLAWCODEX_CONFIG_DIR    REG_EXPAND_SZ    %DRIVE%\\ClawCodex\r\n'
  }

  const value = readWindowsUserEnvVar('CLAWCODEX_CONFIG_DIR', {
    platform: 'win32',
    env: { DRIVE: 'F:' },
    exec
  })

  assert.equal(value, 'F:\\ClawCodex')
  assert.deepEqual(calls, [['reg', ['query', 'HKCU\\Environment', '/v', 'CLAWCODEX_CONFIG_DIR']]])
})

test('readWindowsUserEnvVar returns null when reg exits non-zero (value missing)', () => {
  const exec = () => {
    throw new Error('reg exited 1')
  }

  assert.equal(readWindowsUserEnvVar('CLAWCODEX_CONFIG_DIR', { platform: 'win32', exec }), null)
})

test('readWindowsUserEnvVar returns null for an empty value', () => {
  const exec = () => '    CLAWCODEX_CONFIG_DIR    REG_SZ    \r\n'
  assert.equal(readWindowsUserEnvVar('CLAWCODEX_CONFIG_DIR', { platform: 'win32', exec }), null)
})
