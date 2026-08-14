/**
 * Application state, as nanostores atoms — the same store library the TUI and
 * the desktop renderer use, so the three surfaces read alike.
 *
 * Everything here is *state*; the transitions live in `actions.ts`. Components
 * subscribe with `useStore` and never mutate an atom directly.
 */

import { atom, map } from 'nanostores'

import type { ConnectionState } from '../gateway/client.ts'
import type {
  CommandEntry,
  ContextUsageResult,
  ModelOptionsResult,
  ProjectNode,
} from '../gateway/protocol.ts'
import { emptyTrajectory, type TrajectoryState } from './trajectory.ts'
import { emptyTranscript, type TranscriptState } from './transcript.ts'

/** Where the shell is in its startup sequence. */
export type BootPhase = 'connecting' | 'ready' | 'failed'

export const $bootPhase = atom<BootPhase>('connecting')
export const $bootError = atom<string>('')
export const $connection = atom<ConnectionState>('idle')

/** The live session this window is driving; null before the first session. */
export const $sessionId = atom<string | null>(null)
/** The saved-session id the sidebar highlights (equals $sessionId when live). */
export const $storedSessionId = atom<string | null>(null)
export const $sessionTitle = atom<string>('')

export const $transcript = atom<TranscriptState>(emptyTranscript())
/**
 * A session is being created or replayed.
 *
 * Held separately from the transcript because it decides which *phase* the
 * conversation column is in: an empty transcript means the hero, unless a
 * session is still landing — in which case showing the hero would flash the
 * empty state over a conversation that is about to appear.
 */
export const $sessionLoading = atom<boolean>(false)

/**
 * The same events as `$transcript`, kept at full resolution and timed.
 *
 * Only the live session has one: the stored transcript a resume returns has no
 * per-request timing, and inventing it would be worse than the empty state the
 * trajectory shows instead.
 */
export const $trajectory = atom<TrajectoryState>(emptyTrajectory())

/** Which view the conversation column is showing. */
export type ConversationTab = 'chat' | 'trajectory'

export const $conversationTab = atom<ConversationTab>('chat')

export const $projects = atom<ProjectNode[]>([])
export const $projectsLoading = atom<boolean>(false)
export const $workspace = atom<string>('')

export const $models = atom<ModelOptionsResult>({})
export const $commands = atom<CommandEntry[]>([])
export const $contextUsage = atom<ContextUsageResult | null>(null)

/** Prompts typed while a turn was running, submitted in order when it ends. */
export const $queue = atom<string[]>([])

/** Transient status line under the composer (command output, errors). */
export const $notice = map<{ text: string; tone: 'error' | 'info' }>({ text: '', tone: 'info' })

/** Right-hand details column: which node it is inspecting, if any. */
export const $detailsNodeId = atom<string | null>(null)
