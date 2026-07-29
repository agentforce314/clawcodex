/**
 * The three user-facing permission levels behind `/permissions`.
 *
 * Mirror of the Python twin `src/permissions/levels.py`, which is the source of
 * truth; `tests/test_permission_levels.py` pins the two equal (keys, labels,
 * modes and descriptions), so edit both together.
 *
 * Each level is a label over an existing engine permission mode — the
 * permission engine is untouched. `plan` and `dontAsk` deliberately have no row:
 * they are workflow / strict-deny modes, not points on a looseness ladder, and
 * stay reachable via `/plan`, `--permission-mode`, and `/permissions <raw-mode>`.
 *
 * Level keys are chosen not to collide with any engine mode name so
 * `/permissions <arg>` can accept either a key or a raw mode unambiguously —
 * note the middle level is `approve`, not `auto` (`auto` is a real mode).
 */

export interface PermissionLevel {
  /** One-line explanation of what the level actually permits. */
  description: string
  /** Stable identifier used by `/permissions <key>` and the picker overlay. */
  key: string
  /** Title-case label shown in the picker. */
  label: string
  /** The engine mode this level selects. */
  mode: string
}

/** Ordered loosest-last, matching the picker's 1/2/3 ordering. */
export const PERMISSION_LEVELS: readonly PermissionLevel[] = [
  {
    description:
      'Read files and run read-only commands freely. Editing files, running other commands, or touching anything outside this workspace asks first.',
    key: 'ask',
    label: 'Ask for approval',
    mode: 'default'
  },
  {
    description:
      'Auto-accept file edits in this workspace and a small set of safe shell commands. Everything else still asks.',
    key: 'approve',
    label: 'Approve for me',
    mode: 'acceptEdits'
  },
  {
    description:
      'Edit any file and run any command without asking, except where you have configured a deny or ask rule. Exercise caution when using.',
    key: 'full',
    label: 'Full Access',
    mode: 'bypassPermissions'
  }
]

export const PERMISSION_LEVEL_KEYS: readonly string[] = PERMISSION_LEVELS.map(l => l.key)

/** Look up a level by key (case-insensitive); undefined when not a level. */
export const levelForKey = (key: string): PermissionLevel | undefined =>
  PERMISSION_LEVELS.find(l => l.key === key.trim().toLowerCase())

/**
 * The level a mode belongs to, or undefined for off-ladder modes.
 *
 * `plan` / `dontAsk` / `auto` / `bubble` return undefined — real modes with no
 * picker row. Callers must render those as "no level selected" rather than
 * silently showing one of the three.
 */
export const levelForMode = (mode: null | string | undefined): PermissionLevel | undefined =>
  PERMISSION_LEVELS.find(l => l.mode === mode)

/** `/permissions <key>` → engine mode; undefined when key is not a level. */
export const modeForKey = (key: string): string | undefined => levelForKey(key)?.mode
