import { describe, expect, it } from 'vitest'

import { parseWorktreeSession, planWorktreeExit } from '../lib/worktree.js'

const OWNER_PID = 4242

const fullEnv = (overrides: Record<string, string | undefined> = {}) => ({
  CLAWCODEX_WORKTREE_BRANCH: 'worktree-fix-auth',
  CLAWCODEX_WORKTREE_NAME: 'fix-auth',
  CLAWCODEX_WORKTREE_ORIGINAL_CWD: '/repo',
  CLAWCODEX_WORKTREE_OWNER_PID: String(OWNER_PID),
  CLAWCODEX_WORKTREE_PATH: '/repo/.claude/worktrees/fix-auth',
  CLAWCODEX_WORKTREE_REPO_ROOT: '/repo',
  ...overrides
})

describe('parseWorktreeSession', () => {
  it('parses a complete env block when ppid matches the owner', () => {
    expect(parseWorktreeSession(fullEnv(), OWNER_PID)).toEqual({
      branch: 'worktree-fix-auth',
      name: 'fix-auth',
      originalCwd: '/repo',
      path: '/repo/.claude/worktrees/fix-auth',
      repoRoot: '/repo'
    })
  })

  it('returns null when any var is missing', () => {
    for (const key of [
      'CLAWCODEX_WORKTREE_NAME',
      'CLAWCODEX_WORKTREE_PATH',
      'CLAWCODEX_WORKTREE_BRANCH',
      'CLAWCODEX_WORKTREE_ORIGINAL_CWD',
      'CLAWCODEX_WORKTREE_REPO_ROOT'
    ]) {
      expect(parseWorktreeSession(fullEnv({ [key]: undefined }), OWNER_PID)).toBeNull()
    }
  })

  it('rejects a stale/leaked env block via the OWNER_PID ppid gate', () => {
    // A dev-mode `node dist/entry.js` inside someone else's worktree session
    // inherits the vars but is NOT a child of that launcher — it must not
    // adopt (and at clean exit delete) a worktree it doesn't own.
    expect(parseWorktreeSession(fullEnv(), OWNER_PID + 1)).toBeNull()
    expect(parseWorktreeSession(fullEnv({ CLAWCODEX_WORKTREE_OWNER_PID: undefined }), OWNER_PID)).toBeNull()
    expect(parseWorktreeSession(fullEnv({ CLAWCODEX_WORKTREE_OWNER_PID: 'nope' }), OWNER_PID)).toBeNull()
    expect(parseWorktreeSession(fullEnv({ CLAWCODEX_WORKTREE_OWNER_PID: '0' }), 0)).toBeNull()
  })
})

describe('planWorktreeExit', () => {
  const BR = 'worktree-fix-auth'

  it('silently removes an untouched worktree (TS WorktreeExitDialog parity)', () => {
    expect(planWorktreeExit({ active: true, commits: 0, dirty_files: 0, git_ok: true, ok: true }, BR)).toEqual({
      kind: 'silent-remove'
    })
  })

  it('asks with both counts when dirty files and commits exist', () => {
    const plan = planWorktreeExit({ active: true, commits: 2, dirty_files: 3, git_ok: true, ok: true }, BR)

    expect(plan.kind).toBe('dialog')

    if (plan.kind === 'dialog') {
      expect(plan.removeIsDanger).toBe(true)
      expect(plan.subtitle).toBe(
        `You have 3 uncommitted files and 2 commits on ${BR}. All will be lost if you remove.`
      )
    }
  })

  it('uses singular wording for one file / one commit', () => {
    const files = planWorktreeExit({ active: true, commits: 0, dirty_files: 1, git_ok: true, ok: true }, BR)
    const commits = planWorktreeExit({ active: true, commits: 1, dirty_files: 0, git_ok: true, ok: true }, BR)

    expect(files).toEqual({
      kind: 'dialog',
      removeIsDanger: true,
      subtitle: 'You have 1 uncommitted file. These will be lost if you remove the worktree.'
    })
    expect(commits).toEqual({
      kind: 'dialog',
      removeIsDanger: true,
      subtitle: `You have 1 commit on ${BR}. The branch will be deleted if you remove the worktree.`
    })
  })

  it('fails closed on git_ok:false — dialog with generic subtitle, no fabricated counts', () => {
    const plan = planWorktreeExit(
      { active: true, commits: 7, dirty_files: 9, git_ok: false, ok: true },
      BR
    )

    expect(plan.kind).toBe('dialog')

    if (plan.kind === 'dialog') {
      expect(plan.subtitle).not.toMatch(/[79]/)
      expect(plan.subtitle).toContain('Could not inspect the worktree')
    }
  })

  it('never silent-removes when counts are missing but git_ok is false', () => {
    expect(planWorktreeExit({ active: true, git_ok: false, ok: true }, BR).kind).toBe('dialog')
  })
})
