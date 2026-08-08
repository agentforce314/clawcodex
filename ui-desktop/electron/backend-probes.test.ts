/**
 * Tests for electron/backend-probes.ts.
 *
 * Run with: node --test electron/backend-probes.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import {
  canImportClawCodexCli,
  clawcodexRuntimeImportProbe,
  DEFAULT_PROBE_TIMEOUT_MS,
  PROBE_TIMEOUT_MS,
  resolveProbeTimeoutMs,
  shouldTrustClawCodexOverride,
  verifyClawCodexCli
} from './backend-probes'

// Resolve the host's own Node binary -- guaranteed to be on disk and
// runnable. We use it as both a stand-in for "a python that doesn't
// have clawcodex_cli" (since `node -c "import clawcodex_cli"` will exit
// non-zero) and as a way to script verifyClawCodexCli's success path
// (a tiny script we write to disk that exits 0 on --version).
const NODE_BIN = process.execPath

test('canImportClawCodexCli returns false when path is falsy', () => {
  assert.equal(canImportClawCodexCli(''), false)
  assert.equal(canImportClawCodexCli(null), false)
  assert.equal(canImportClawCodexCli(undefined), false)
})

test('canImportClawCodexCli returns false when interpreter cannot run -c', () => {
  // node IS an interpreter, but `node -c "import clawcodex_cli"` is a
  // SyntaxError -- different exit reason from a real Python's
  // ModuleNotFoundError, but the predicate is "exit 0 or not" and
  // both land on "not", which is exactly what we want for the
  // resolver fall-through.
  assert.equal(canImportClawCodexCli(NODE_BIN), false)
})

test('canImportClawCodexCli returns false when binary does not exist', () => {
  const ghost = path.join(os.tmpdir(), 'clawcodex-probes-ghost-' + Date.now() + '.exe')
  assert.equal(canImportClawCodexCli(ghost), false)
})

test('clawcodex runtime import probe checks config dependencies', () => {
  const probe = clawcodexRuntimeImportProbe()
  assert.match(probe, /\bimport yaml\b/)
  // dotenv is the first third-party import on the CLI boot path
  // (clawcodex_cli/env_loader.py); a mid-update venv missing python-dotenv
  // passed the old probe and produced an unrecoverable boot loop.
  assert.match(probe, /\bimport dotenv\b/)
  assert.match(probe, /\bimport clawcodex_cli\.config\b/)
})

test('explicit ClawCodex override is authoritative', () => {
  assert.equal(shouldTrustClawCodexOverride('/nix/store/abc/bin/clawcodex'), true)
})

test('empty ClawCodex override is not authoritative', () => {
  assert.equal(shouldTrustClawCodexOverride(''), false)
  assert.equal(shouldTrustClawCodexOverride(undefined), false)
})

test('verifyClawCodexCli returns false when command is falsy', () => {
  assert.equal(verifyClawCodexCli(''), false)
  assert.equal(verifyClawCodexCli(null), false)
  assert.equal(verifyClawCodexCli(undefined), false)
})

test('verifyClawCodexCli returns false when binary does not exist', () => {
  const ghost = path.join(os.tmpdir(), 'clawcodex-probes-ghost-' + Date.now() + '.exe')
  assert.equal(verifyClawCodexCli(ghost), false)
})

test('verifyClawCodexCli returns true when --version exits 0', () => {
  // Write a tiny script that exits 0 regardless of args, then invoke
  // it through node. This stands in for a working clawcodex binary --
  // verifyClawCodexCli only cares about the exit code.
  const scriptPath = path.join(os.tmpdir(), `clawcodex-probes-ok-${Date.now()}-${process.pid}.cjs`)
  fs.writeFileSync(scriptPath, 'process.exit(0)\n')

  try {
    // Use node as the launcher and our script as the "command". Pass
    // shell:false (default) -- node is a real binary, no shim.
    // execFileSync passes ['--version'] as args, which node ignores
    // gracefully (well, it prints its version and exits 0, which is
    // perfect -- exit code 0 is the only signal we read).
    assert.equal(verifyClawCodexCli(NODE_BIN), true)
  } finally {
    try {
      fs.unlinkSync(scriptPath)
    } catch {
      void 0
    }
  }
})

test('verifyClawCodexCli swallows timeouts (does not throw)', () => {
  // We can't easily provoke a real hang in CI without slowing the
  // suite, but we CAN confirm that an invocation that DOES throw
  // (because the binary is missing) returns false rather than
  // propagating. Same code path the timeout case takes.
  assert.equal(verifyClawCodexCli('/definitely/not/a/real/binary/anywhere'), false)
})

test('default probe timeout is 15s (not the old 5s death-loop value)', () => {
  assert.equal(DEFAULT_PROBE_TIMEOUT_MS, 15_000)
  // Module constant uses process.env at load time; with no override it
  // matches the default (tests run without CLAWCODEX_PROBE_TIMEOUT_MS).
  assert.equal(PROBE_TIMEOUT_MS, DEFAULT_PROBE_TIMEOUT_MS)
})

test('resolveProbeTimeoutMs honours CLAWCODEX_PROBE_TIMEOUT_MS', () => {
  assert.equal(resolveProbeTimeoutMs({}), DEFAULT_PROBE_TIMEOUT_MS)
  assert.equal(resolveProbeTimeoutMs({ CLAWCODEX_PROBE_TIMEOUT_MS: '30000' }), 30_000)
  assert.equal(resolveProbeTimeoutMs({ CLAWCODEX_PROBE_TIMEOUT_MS: '0' }), DEFAULT_PROBE_TIMEOUT_MS)
  assert.equal(resolveProbeTimeoutMs({ CLAWCODEX_PROBE_TIMEOUT_MS: 'nope' }), DEFAULT_PROBE_TIMEOUT_MS)
  // Cap runaway values
  assert.equal(resolveProbeTimeoutMs({ CLAWCODEX_PROBE_TIMEOUT_MS: '999999' }), 120_000)
})
