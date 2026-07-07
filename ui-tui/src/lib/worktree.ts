/**
 * Session worktree (--worktree) client support.
 *
 * The Python launcher creates/resumes the worktree BEFORE spawning this TUI
 * and advertises it via CLAWCODEX_WORKTREE_* env vars (the same block the
 * agent-server child inherits to service the exit-time git ops). Keep the
 * var names in sync with src/utils/worktree_session.py.
 *
 * Ownership gate: the launcher stamps CLAWCODEX_WORKTREE_OWNER_PID with its
 * own pid, and this TUI is spawned as its DIRECT child — so a session is only
 * honored when process.ppid matches. A dev-mode `node dist/entry.js` run
 * inside someone else's worktree session (stale/leaked env) fails the gate
 * and behaves like a plain session instead of adopting — and potentially
 * deleting — a worktree it doesn't own.
 */

export interface WorktreeSessionInfo {
  branch: string
  name: string
  originalCwd: string
  path: string
  repoRoot: string
}

/** Backend `worktree_status` reply (src/server/agent_server.py). */
export interface WorktreeStatusResponse {
  active?: boolean
  branch?: string
  commits?: number
  dirty_files?: number
  /** false → counts are placeholders; fail closed (dialog, generic subtitle). */
  git_ok?: boolean
  name?: string
  ok?: boolean
  original_cwd?: string
  path?: string
}

/** Pure parser — exported for tests; getWorktreeSession() feeds it process env. */
export function parseWorktreeSession(
  env: Record<string, string | undefined>,
  ppid: number
): null | WorktreeSessionInfo {
  const name = env.CLAWCODEX_WORKTREE_NAME
  const path = env.CLAWCODEX_WORKTREE_PATH
  const branch = env.CLAWCODEX_WORKTREE_BRANCH
  const originalCwd = env.CLAWCODEX_WORKTREE_ORIGINAL_CWD
  const repoRoot = env.CLAWCODEX_WORKTREE_REPO_ROOT

  if (!name || !path || !branch || !originalCwd || !repoRoot) {
    return null
  }

  const ownerPid = Number(env.CLAWCODEX_WORKTREE_OWNER_PID ?? '')

  if (!Number.isInteger(ownerPid) || ownerPid <= 0 || ownerPid !== ppid) {
    return null
  }

  return { branch, name, originalCwd, path, repoRoot }
}

let cached: null | undefined | WorktreeSessionInfo

/** The worktree session this TUI runs inside, or null. Memoized (env is fixed). */
export function getWorktreeSession(): null | WorktreeSessionInfo {
  if (cached === undefined) {
    cached = parseWorktreeSession(process.env, process.ppid)
  }

  return cached
}

/** Test hook: reset the memo so parseWorktreeSession runs again. */
export function resetWorktreeSessionCacheForTesting(): void {
  cached = undefined
}

export type WorktreeExitPlan =
  | { kind: 'dialog'; removeIsDanger: boolean; subtitle: string }
  | { kind: 'silent-remove' }

/**
 * Decide the exit flow from a `worktree_status` reply — the TS reference's
 * WorktreeExitDialog effect: clean (0 dirty, 0 lost commits, git healthy) →
 * remove silently; anything else → ask, with the reference's subtitle matrix.
 * `git_ok: false` fails closed: generic subtitle, no fabricated counts.
 */
export function planWorktreeExit(status: WorktreeStatusResponse, branch: string): WorktreeExitPlan {
  const gitOk = status.git_ok !== false
  const dirty = gitOk ? (status.dirty_files ?? 0) : 0
  const commits = gitOk ? (status.commits ?? 0) : 0

  if (gitOk && dirty === 0 && commits === 0) {
    return { kind: 'silent-remove' }
  }

  let subtitle: string

  if (!gitOk) {
    subtitle =
      'Could not inspect the worktree (git failed). Keep it to be safe, or remove it if you are sure.'
  } else if (dirty > 0 && commits > 0) {
    subtitle =
      `You have ${dirty} uncommitted ${dirty === 1 ? 'file' : 'files'} and ` +
      `${commits} ${commits === 1 ? 'commit' : 'commits'} on ${branch}. All will be lost if you remove.`
  } else if (dirty > 0) {
    subtitle = `You have ${dirty} uncommitted ${dirty === 1 ? 'file' : 'files'}. These will be lost if you remove the worktree.`
  } else {
    subtitle = `You have ${commits} ${commits === 1 ? 'commit' : 'commits'} on ${branch}. The branch will be deleted if you remove the worktree.`
  }

  return { kind: 'dialog', removeIsDanger: true, subtitle }
}

// ── exit note ────────────────────────────────────────────────────────────────
// The keep/remove result prints AFTER Ink unmounts (same pattern as the cost
// summary): stash it here, and a process 'exit' hook writes it synchronously.

let exitNote: null | string = null

export function setWorktreeExitNote(note: string): void {
  exitNote = note
}

export function getWorktreeExitNoteForTesting(): null | string {
  return exitNote
}

/**
 * Print the keep/remove result under the final frame. Register AFTER
 * entry.tsx's terminal-mode reset backstop (exit listeners run in order) so
 * the note lands on a sane terminal, and BEFORE the cost summary so it reads
 * as the session's closing status line.
 */
export function registerWorktreeNoteOnExit(): void {
  process.on('exit', () => {
    if (exitNote) {
      process.stdout.write(`\n${exitNote}\n`)
    }
  })
}
