/**
 * Every transition the UI can cause: connect, create/resume a session, submit
 * a prompt, answer an approval, change model or approval mode.
 *
 * Components call these; they never talk to the gateway directly. That keeps
 * the RPC vocabulary in one file — the place to look when the backend contract
 * moves — and makes the socket injectable for tests.
 */

import { GatewayClient, type ConnectionState } from '../gateway/client.ts'
import { apiGet, resolveBackend, type BackendTarget } from '../gateway/boot.ts'
import type {
  ApprovalChoice,
  CommandEntry,
  CommandsCatalogResult,
  ContextUsageResult,
  DelegationPauseResult,
  DelegationStatusResult,
  DirectoryListing,
  EffortOptionsResult,
  FileBytes,
  FilePage,
  FileSearchResult,
  GatewayEvent,
  GeneralSettingsResult,
  ModelOptionsResult,
  ProjectsTreeResult,
  ProviderListResult,
  ProviderMutationResult,
  SessionHistoryResult,
  SessionResumeResult,
  SlashResult,
  SubagentInterruptResult,
  SubagentTranscriptResult,
  WorkspaceFileFailure,
  WorkspaceFileResult,
  WorkspaceLevel,
} from '../gateway/protocol.ts'
import {
  $backendNano,
  $bootError,
  $bootPhase,
  $commands,
  $connection,
  $contextUsage,
  $delegation,
  $effort,
  $generalSettings,
  $detailsNodeId,
  $models,
  $notice,
  $pendingApprovalMode,
  $pendingModel,
  $providers,
  $projects,
  $projectsLoading,
  $queue,
  $sessionId,
  $sessionAttaching,
  $sessionLoading,
  $sessionTitle,
  $storedSessionId,
  $subagentView,
  $trajectory,
  $transcript,
  $workspace,
} from './store.ts'
import {
  applyTrajectoryEvent,
  emptyTrajectory,
  hydrateStoredTrajectory,
  recordPrompt,
} from './trajectory.ts'
import { updatesFor } from '../conversation/PlanReviewPanel.tsx'
import { liveAttachments, placeholderFor, type Attachment } from '../conversation/attachments.ts'
import {
  appendUserMessage,
  applyEvent,
  clearApproval,
  clearQuestion,
  emptyTranscript,
  hydrateStoredMessages,
  markTurnStarted,
  rewindLastTurn,
} from './transcript.ts'

let client: GatewayClient | null = null
// Async session mutations capture this value before their request. Navigation
// advances it immediately, so a late reply from the conversation being left
// cannot overwrite the transcript or status line of the one being opened.
let sessionNavigationEpoch = 0
// The backend owns sending images; keep their bytes here only for the local
// user row, including prompts waiting in the queue. Drained with the prompt.
let pendingImages: Attachment[] = []

export function gateway(): GatewayClient {
  if (client === null) throw new Error('gateway not started')

  return client
}

/** Test seam: install a client with an injected socket factory. */
export function setGatewayClient(next: GatewayClient | null): void {
  client = next
  pendingImages = []
}

function notice(text: string, tone: 'error' | 'info' = 'info'): void {
  $notice.set({ text, tone })
}

function beginSessionNavigation(): void {
  sessionNavigationEpoch += 1
  pendingImages = []
  notice('')
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/* ── the remembered session ─────────────────────────────────────────────── */

const SESSION_MEMORY_KEY = 'clawcodex.web.session'

/**
 * The session this window was on, kept across a reload.
 *
 * `live` is the runtime the window was attached to and `stored` the row the
 * sidebar shows for it — the same id for a session created here, different
 * for one resumed from a row, because a resume spawns a fresh runtime that
 * replays the stored one and saves its later turns under its own id. A
 * reload resumes `live`: while the backend still has that runtime the reply
 * is the very same session, its running turn included, and once it is gone
 * the runtime's own record — the complete one — is replayed into a new one.
 */
export interface RememberedSession {
  cwd?: string
  live: string
  stored: string
}

export function rememberSession(memory: RememberedSession | null): void {
  try {
    if (memory === null) window.localStorage.removeItem(SESSION_MEMORY_KEY)
    else window.localStorage.setItem(SESSION_MEMORY_KEY, JSON.stringify(memory))
  } catch {
    /* private mode: the session holds for this page load */
  }
}

export function recallSession(): RememberedSession | null {
  try {
    const raw = window.localStorage.getItem(SESSION_MEMORY_KEY)

    if (raw === null) return null

    const parsed = JSON.parse(raw) as Partial<RememberedSession>

    if (typeof parsed.live !== 'string' || parsed.live === '') return null

    return {
      live: parsed.live,
      stored: typeof parsed.stored === 'string' && parsed.stored !== '' ? parsed.stored : parsed.live,
      ...(typeof parsed.cwd === 'string' && parsed.cwd !== '' ? { cwd: parsed.cwd } : {}),
    }
  } catch {
    return null
  }
}

/**
 * Land back on the session this window was on before the reload.
 *
 * Nothing is announced when the backend no longer knows it: the hero is the
 * honest place to land, and the memory is dropped so the next reload does not
 * ask again. A runtime that never saved — nothing was typed after resuming a
 * row — has no record of its own, so the row it came from is replayed
 * instead, and the blank runtime the first attempt spawned is closed.
 */
async function restoreSession(): Promise<void> {
  const remembered = recallSession()

  if (remembered === null || $sessionId.get() !== null) return

  let result = await resumeSessionCore(remembered.live, remembered.cwd, true)

  if (
    (result === null || (result.messages?.length ?? 0) === 0) &&
    remembered.stored !== remembered.live
  ) {
    // The blank runtime the first attempt landed on (if any) is idle, so
    // navigating to the stored row releases it — see releaseIdleRuntime.
    result = await resumeSessionCore(remembered.stored, remembered.cwd, true)
  }

  if (result === null) {
    rememberSession(null)

    return
  }

  // The row the sidebar highlights is the conversation's, not the runtime's:
  // re-attaching to a live runtime reports the runtime as its own stored id.
  $storedSessionId.set(remembered.stored === remembered.live ? $storedSessionId.get() : remembered.stored)
  rememberSession({ ...remembered, live: result.session_id })
}

/* ── boot ────────────────────────────────────────────────────────────────── */

export async function start(): Promise<void> {
  const target = resolveBackend()

  if (client === null) client = new GatewayClient()

  client.onState((state: ConnectionState) => {
    $connection.set(state)

    // A reconnect re-subscribes this socket to every live session (the server
    // does that on open), so the transcript keeps streaming without a reload.
    if (state === 'open' && $bootPhase.get() === 'failed') $bootPhase.set('ready')
  })

  client.onAny(handleEvent)

  try {
    await client.connect(target.wsUrl)
  } catch (error) {
    $bootPhase.set('failed')
    $bootError.set(
      target.token === ''
        ? 'No session token. Open the URL printed by `clawcodex web`, or append ?token=…'
        : errorText(error),
    )

    return
  }

  $bootPhase.set('ready')

  // Catalogs are independent of any session and populate the composer chrome
  // before the first prompt; failures here degrade the chrome, not the app.
  await Promise.allSettled([
    seedWorkspace(target),
    refreshProjects(),
    refreshModels(),
    refreshCommands(),
  ])

  // Last, and after the catalogs: a resumed session's info repaints the chrome
  // the catalogs seeded, and a reload should land where the window was.
  await restoreSession()
}

/**
 * The backend facts the hero needs before any session exists.
 *
 * The workspace is the directory the first session will run in, and nano is
 * whether that session will be a nano one — both have to be named *before*
 * the session exists, and only REST knows them that early (`session.info`
 * carries both, but not until a session is created).
 */
async function seedWorkspace(target: BackendTarget): Promise<void> {
  try {
    const status = await apiGet<{ nano?: boolean; workspace?: string }>(target, '/status')

    if ($workspace.get() === '' && typeof status.workspace === 'string' && status.workspace !== '') {
      $workspace.set(status.workspace)
    }

    // Strict === true: an older backend without the field must stay falsy.
    $backendNano.set(status.nano === true)
  } catch {
    /* the hero simply omits the workspace chip and the nano badge */
  }
}

function handleEvent(event: GatewayEvent): void {
  if (event.type === 'sessions.changed') {
    void refreshProjects()

    return
  }

  // The socket is subscribed to EVERY live session on the backend, so another
  // window's turn — or the session this one just navigated away from, still
  // finishing — arrives here too. A session-scoped event only moves this
  // window's transcript when it belongs to the session this window is on.
  //
  // Strict on purpose, including while no session is adopted yet: the window
  // between `session.create` being sent and its reply landing is exactly when
  // a previous session's turn would leak into the fresh transcript. Nothing is
  // lost by dropping those — the create/resume reply carries the new session's
  // own info, and its first turn cannot start before the composer has an id.
  const active = $sessionId.get()

  if (event.session_id !== undefined && event.session_id !== active) return

  // The backend names an untitled session after its first prompt; the header
  // reads this store, so without handling it the title would only appear on
  // the next sidebar refresh.
  if (event.type === 'session.title') {
    const title = (event.payload as { title?: string } | undefined)?.title

    if (title !== undefined && title !== '') $sessionTitle.set(title)

    return
  }

  $transcript.set(applyEvent($transcript.get(), event))
  $trajectory.set(applyTrajectoryEvent($trajectory.get(), event))

  if (event.type === 'message.complete') {
    void refreshUsage()
    drainQueue()
  }
}

/* ── sessions ────────────────────────────────────────────────────────────── */

export interface SessionSpawnOptions {
  /** Create `cwd` when it does not exist yet — the "new workspace" flow. */
  createDir?: boolean
  cwd?: string
  effort?: string
  model?: string
  provider?: string
  /** Run the session in a fresh git worktree of the repo at `cwd`. */
  worktree?: boolean
}

/**
 * What this client can render, declared at session start.
 *
 * The backend defaults AskUserQuestion to a non-interactive answer because a
 * client that ignored the question would block the agent's worker thread until
 * the ask timeout. Declaring the capability is what makes the agent actually
 * ask — so it belongs to the client that ships the composer, not to the
 * backend.
 */
const CAPABILITIES = { ask_user_question: true }

/**
 * Start a fresh session.
 * @returns null once the session is up, else the backend's reason it is not —
 *   the same text the notice shows, for a dialog that wants to stay open on it.
 */
export async function createSession(options: SessionSpawnOptions = {}): Promise<string | null> {
  releaseIdleRuntime()
  beginSessionNavigation()
  $transcript.set(emptyTranscript())
  $trajectory.set(emptyTrajectory())
  $detailsNodeId.set(null)
  $subagentView.set(null)
  $sessionTitle.set('')
  $sessionId.set(null)
  $storedSessionId.set(null)
  $sessionAttaching.set(false)

  const params: Record<string, unknown> = { capabilities: CAPABILITIES }

  // A model is SESSION state. Only two things may name one on a create: an
  // explicit option (a caller naming a model means it) and a pick made on the
  // welcome screen for this very session ($pendingModel). Never $models —
  // that mirrors whatever session the picker last asked about, and seeding
  // from it made "New session" inherit the previous session's model. With
  // neither, the create names no model and the backend reads the global
  // default (default_provider + its default_model) into the session's own
  // config, which is what "New sessions start on X." promises.
  const pending = $pendingModel.get()
  const provider = options.provider ?? pending?.provider
  const model = options.model ?? pending?.model

  if (options.cwd !== undefined && options.cwd !== '') params.cwd = options.cwd
  if (options.createDir === true) params.create_dir = true
  if (options.worktree === true) params.worktree = true
  if (provider !== undefined && provider !== '') params.provider = provider
  if (model !== undefined && model !== '') params.model = model
  if (options.effort !== undefined && options.effort !== '') params.reasoning_effort = options.effort

  try {
    const result = await gateway().request<SessionResumeResult>('session.create', params)

    adoptSession(result)
    await applyPendingApprovalMode()

    return null
  } catch (error) {
    notice(`Could not start a session: ${errorText(error)}`, 'error')

    return errorText(error)
  } finally {
    // A create that fails must not leave a "Loading session…" spinner over an
    // empty transcript: the composer is still usable, and the hero is the
    // honest place to land. Resume owns this flag too, so clearing it here
    // covers a create that interleaves with one.
    $sessionLoading.set(false)
  }
}

export async function resumeSession(storedId: string, cwd?: string): Promise<void> {
  // Already on this conversation, or landing on it: a second click has
  // nothing to load and must not spawn anything.
  if (storedId === $storedSessionId.get() && ($sessionId.get() !== null || attachInFlight !== null)) return

  await resumeSessionCore(storedId, cwd, false)
}

/**
 * The runtime attach in flight for the conversation on screen, if any, so a
 * prompt typed while it lands waits for it instead of creating a session.
 */
let attachInFlight: Promise<SessionResumeResult | null> | null = null

/**
 * Let go of the runtime this window is leaving, when nothing is happening in it.
 *
 * Every resume spawns a runtime, and a window that browsed twenty saved
 * sessions would otherwise leave twenty agents (threads, MCP servers) running
 * behind it. A runtime mid-turn, holding an approval or a question, or with
 * prompts queued stays up: closing it would lose work, and its events keep
 * arriving on this socket. The conversation itself is not lost — the backend
 * saves it under the runtime's id at every turn end, so the row replays from
 * there. Nothing is awaited: the navigation must not wait on a teardown.
 */
function releaseIdleRuntime(): void {
  const previous = $sessionId.get()

  if (previous === null) return

  const transcript = $transcript.get()

  if (
    transcript.running ||
    transcript.approval !== undefined ||
    transcript.question !== undefined ||
    $queue.get().length > 0
  ) {
    return
  }

  gateway()
    .request('session.close', { session_id: previous })
    .catch(() => {
      /* a runtime nothing is waiting on; the backend reaps it either way */
    })
}

/** An older backend without the method: fall back to the one-call resume. */
function isMethodMissing(error: unknown): boolean {
  return errorText(error).includes('method not found')
}

/** Put a stored transcript on screen: nodes, timings, title and session facts. */
function showStoredTranscript(stored: SessionHistoryResult | SessionResumeResult): void {
  if (stored.title !== undefined && stored.title !== '') $sessionTitle.set(stored.title)

  if (stored.info !== undefined) {
    $transcript.set({ ...$transcript.get(), info: { ...$transcript.get().info, ...stored.info } })

    if (stored.info.cwd !== undefined && stored.info.cwd !== '') $workspace.set(stored.info.cwd)
  }

  if (stored.messages === undefined || stored.messages.length === 0) return

  $transcript.set({ ...$transcript.get(), nodes: hydrateStoredMessages(stored.messages) })
  // The same stored messages carry wall-clock timestamps, which is enough for
  // the Trajectory tab to show the run's shape and its tool timings instead
  // of "nothing recorded".
  $trajectory.set(hydrateStoredTrajectory(stored.messages))
}

/**
 * Open a saved session: the transcript first, the runtime behind it.
 *
 * Two round-trips on purpose, the way the reference opens a session. The
 * stored transcript is a file read (`session.history`) and lands in tens of
 * milliseconds; the runtime that will answer the next prompt has a provider,
 * a tool registry and a system prompt to build, and the reader should not
 * look at a spinner for that — they came to read. While it attaches, the
 * composer says so and a prompt sent meanwhile waits for it.
 *
 * `quiet` skips the failure notice, for a restore on boot where "could not
 * resume" would be about a session the reader never asked for.
 * @returns the backend's resume reply, carrying the stored messages, or null
 *   when the session could not be opened.
 */
async function resumeSessionCore(
  storedId: string,
  cwd: string | undefined,
  quiet: boolean,
): Promise<SessionResumeResult | null> {
  releaseIdleRuntime()
  beginSessionNavigation()

  const epoch = sessionNavigationEpoch

  $transcript.set(emptyTranscript())
  // Cleared while the replay is in flight; the stored messages rebuild it
  // below, timestamps included.
  $trajectory.set(emptyTrajectory())
  $detailsNodeId.set(null)
  $subagentView.set(null)
  $sessionTitle.set('')
  $sessionId.set(null)
  // The row is highlighted from the click, not from a reply: the sidebar
  // should not wait on anything to show which conversation was chosen.
  $storedSessionId.set(storedId)
  $sessionAttaching.set(false)
  $sessionLoading.set(true)

  const params: Record<string, unknown> = { capabilities: CAPABILITIES, session_id: storedId }

  if (cwd !== undefined && cwd !== '') params.cwd = cwd

  // 1. The stored transcript, cold.
  let history: SessionHistoryResult | null = null

  try {
    history = await gateway().request<SessionHistoryResult>('session.history', { session_id: storedId })
  } catch (error) {
    if (epoch !== sessionNavigationEpoch) return null

    if (!isMethodMissing(error)) {
      $sessionLoading.set(false)

      if (!quiet) notice(`Could not open that session: ${errorText(error)}`, 'error')

      return null
    }
  }

  if (epoch !== sessionNavigationEpoch) return null

  if (history !== null) {
    showStoredTranscript(history)
    $sessionLoading.set(false)
    // The attach need not carry the transcript again.
    params.omit_messages = true
  }

  // 2. The runtime, attached behind the transcript.
  $sessionAttaching.set(true)

  let attach: Promise<SessionResumeResult | null> | null = null

  attach = (async (): Promise<SessionResumeResult | null> => {
    try {
      const result = await gateway().request<SessionResumeResult>('session.resume', params)

      // Navigated on meanwhile: the runtime is the backend's to keep for the
      // next visit, and none of this belongs to the conversation now on screen.
      if (epoch !== sessionNavigationEpoch) return null

      adoptSession(result)
      // A backend that answered with the transcript anyway (no cold read
      // happened, or it holds a fuller record) — that is the one to show.
      showStoredTranscript(result)

      return history === null
        ? result
        : { ...result, message_count: history.message_count, messages: history.messages }
    } catch (error) {
      if (epoch === sessionNavigationEpoch && !quiet) {
        notice(`Could not resume that session: ${errorText(error)}`, 'error')
      }

      return null
    } finally {
      if (epoch === sessionNavigationEpoch) {
        $sessionLoading.set(false)
        $sessionAttaching.set(false)
      }

      if (attachInFlight === attach) attachInFlight = null
    }
  })()

  attachInFlight = attach

  return attach
}

function adoptSession(result: SessionResumeResult): void {
  $sessionId.set(result.session_id)
  $storedSessionId.set(result.stored_session_id ?? result.session_id)
  rememberSession({
    live: result.session_id,
    stored: result.stored_session_id ?? result.session_id,
    ...(result.info?.cwd === undefined || result.info.cwd === '' ? {} : { cwd: result.info.cwd }),
  })
  // The welcome-screen pick was for the session that now exists — created
  // with it, or superseded by a resume. Left standing, it would ride every
  // LATER create too, which is the previous-session inheritance again.
  $pendingModel.set(null)

  if (result.info !== undefined) {
    $transcript.set({ ...$transcript.get(), info: { ...$transcript.get().info, ...result.info } })

    if (result.info.cwd !== undefined) $workspace.set(result.info.cwd)
  }

  void refreshProjects()
  void refreshUsage()
  // The catalog belongs to the session that just started, not to whatever was
  // read before it: a cross-provider fallback can land somewhere else
  // entirely. It is also the picker's only repair point — a `model.options`
  // that failed at boot would otherwise leave "No configured providers yet"
  // on screen for the life of the page.
  void refreshModels()
  // The effort ladder is a property of the model this session ended up on,
  // which is only known now — before a session there is nothing to ask.
  void refreshEffort()
}

/**
 * Re-run the last prompt: rewind the turn on the agent, drop it here, resubmit.
 *
 * `Edit` puts the prompt back in the composer but leaves the old turn in the
 * conversation, so sending it again APPENDS a second attempt the model then
 * reads as a follow-up. Retry replaces the turn instead, which is what asking
 * for a different answer to the same question actually means.
 *
 * The agent refuses to rewind during an active turn (it mutates the
 * conversation the worker is reading), so nothing is trimmed locally until the
 * backend confirms — otherwise a refused rewind would leave the client showing
 * a conversation the agent still has.
 */
export async function retryLastTurn(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  const rewound = rewindLastTurn($transcript.get())

  if (rewound === null) return

  try {
    const result = await gateway().request<{ error?: string; ok?: boolean; removed?: number }>(
      'session.rewind',
      { session_id: sessionId, turns: 1 },
    )

    if (result.ok === false) {
      notice(result.error === undefined || result.error === '' ? 'Could not retry' : result.error, 'error')

      return
    }
  } catch (error) {
    notice(errorText(error), 'error')

    return
  }

  $transcript.set(rewound.state)
  await submitPrompt(rewound.prompt)
}

export async function interrupt(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    await gateway().request('session.interrupt', { session_id: sessionId })
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

export async function clearSession(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  const navigationEpoch = sessionNavigationEpoch
  const isStillCurrent = () =>
    sessionNavigationEpoch === navigationEpoch && $sessionId.get() === sessionId

  try {
    await gateway().request('session.clear', { session_id: sessionId })

    if (!isStillCurrent()) return

    pendingImages = []
    $transcript.set({ ...emptyTranscript(), info: $transcript.get().info })
    $trajectory.set(emptyTrajectory())
    $subagentView.set(null)
    notice('Conversation cleared.')
  } catch (error) {
    if (isStillCurrent()) notice(errorText(error), 'error')
  }
}

export async function renameSession(title: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null || title.trim() === '') return

  try {
    await gateway().request('session.title', { session_id: sessionId, title: title.trim() })
    $sessionTitle.set(title.trim())
    void refreshProjects()
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

/**
 * Push a pre-session approval-mode choice onto the session that just started.
 *
 * `session.create` takes provider/model/effort but not a permission mode, so
 * this is a second call rather than a spawn parameter — which also keeps the
 * backend's create contract unchanged.
 */
async function applyPendingApprovalMode(): Promise<void> {
  const pending = $pendingApprovalMode.get()

  if (pending === null) return

  await setApprovalMode(pending)
}

/* ── prompting ───────────────────────────────────────────────────────────── */

/**
 * Submit a prompt, creating the session on first use.
 *
 * A prompt typed while a turn is running is queued rather than rejected: the
 * agent takes one turn at a time, and silently dropping the draft is the one
 * outcome a user cannot recover from.
 */
export async function submitPrompt(text: string, spawn: SessionSpawnOptions = {}): Promise<void> {
  const trimmed = text.trim()

  if (trimmed === '') return

  if ($transcript.get().running) {
    $queue.set([...$queue.get(), trimmed])

    return
  }

  // A saved session whose runtime is still attaching: the prompt is for THAT
  // conversation, so wait for it rather than starting a session of its own.
  if (attachInFlight !== null) await attachInFlight

  if ($sessionId.get() === null) {
    await createSession(spawn)

    if ($sessionId.get() === null) return
  }

  if (trimmed.startsWith('/')) {
    const handled = await runSlashCommand(trimmed)

    if (handled) return
  }

  await send(trimmed)
}

async function send(text: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  const images = liveAttachments(text, pendingImages).map(({ id, name, url }) => ({
    name, placeholder: placeholderFor(id), url,
  }))
  pendingImages = []
  $transcript.set(markTurnStarted(appendUserMessage($transcript.get(), text, images)))
  $trajectory.set(recordPrompt($trajectory.get(), text))
  notice('')

  try {
    await gateway().request('prompt.submit', { session_id: sessionId, text })
  } catch (error) {
    notice(errorText(error), 'error')
    $transcript.set({ ...$transcript.get(), running: false })
  }
}

function drainQueue(): void {
  const queued = $queue.get()

  if (queued.length === 0) return

  const [next, ...rest] = queued
  $queue.set(rest)

  if (next !== undefined) void send(next)
}

export function dequeue(index: number): void {
  $queue.set($queue.get().filter((_, i) => i !== index))
}

/**
 * Run a slash command server-side. Returns true when the command was fully
 * handled (its output is the answer); false when the caller should fall
 * through to a normal prompt.
 */
async function runSlashCommand(input: string): Promise<boolean> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return false

  try {
    const result = await gateway().request<SlashResult>('slash.exec', {
      session_id: sessionId,
      command: input.slice(1),
    })

    if (result.type === 'skill') {
      // A skill expands to a prompt: submit the expansion as this turn, with
      // the command the user typed shown as the user bubble.
      $transcript.set(markTurnStarted(appendUserMessage($transcript.get(), input)))
      await gateway().request('prompt.submit', { session_id: sessionId, text: result.message })

      return true
    }

    $transcript.set(appendUserMessage($transcript.get(), input))
    notice(result.output)
    void refreshUsage()

    // Model / permission commands change session state the chrome reads.
    void refreshModels()
    void refreshEffort()

    return true
  } catch (error) {
    notice(errorText(error), 'error')

    return true
  }
}

/* ── approvals ───────────────────────────────────────────────────────────── */

export async function respondApproval(choice: ApprovalChoice): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  $transcript.set(clearApproval($transcript.get()))

  try {
    await gateway().request('approval.respond', { session_id: sessionId, choice })
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

/**
 * Answer or decline the parked AskUserQuestion.
 *
 * Keys are the question texts verbatim: that is what the tool filters its
 * answers on, so a re-derived label or an index would be silently dropped on
 * the far side.
 */
export async function respondQuestion(
  action: 'decline' | 'submit',
  answers: Record<string, string> = {},
): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  // Seat the composer back immediately. The agent is unblocked either way, and
  // leaving the takeover up until the round-trip returns reads as a hang.
  $transcript.set(clearQuestion($transcript.get()))

  try {
    await gateway().request('question.respond', { action, answers, session_id: sessionId })
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

// ── delegation control plane ────────────────────────────────────────────────

/**
 * Refresh the live-agent snapshot.
 *
 * Failures leave the last good snapshot in place rather than blanking the
 * panel: a dropped poll during a reconnect is not evidence that the agents
 * stopped, and flashing an empty list would say it was.
 */
export async function fetchDelegationStatus(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const result = await gateway().request<DelegationStatusResult>('delegation.status', {
      session_id: sessionId,
    })

    $delegation.set(result ?? { active: [] })
  } catch {
    // Keep the previous snapshot; the panel shows its own staleness.
  }
}

/**
 * Stop or resume admission of new subagents.
 *
 * Sends the value it wants rather than asking the backend to flip, so two
 * clicks racing from two tabs converge on one state instead of toggling past
 * each other.
 */
export async function setDelegationPaused(paused: boolean): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const result = await gateway().request<DelegationPauseResult>('delegation.pause', {
      paused,
      session_id: sessionId,
    })

    $delegation.set({ ...($delegation.get() ?? {}), paused: result?.paused ?? paused })
  } catch {
    notice('Could not change delegation state', 'error')
  }
}

/**
 * Abort one live subagent.
 *
 * Reports a miss rather than an interruption when the agent had already
 * finished — the panel can be a poll behind the truth.
 */
export async function interruptSubagent(subagentId: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const result = await gateway().request<SubagentInterruptResult>('subagent.interrupt', {
      session_id: sessionId,
      subagent_id: subagentId,
    })

    if (result?.found !== true) {
      notice('That agent had already finished', 'info')
    }
  } catch {
    notice('Could not interrupt the agent', 'error')
  }

  await fetchDelegationStatus()
}

/** Show one subagent (a catalog entry's key) in the conversation column. */
export function openSubagent(key: string): void {
  $subagentView.set(key)
}

/** Back to the session the subagent belongs to. */
export function closeSubagent(): void {
  $subagentView.set(null)
}

/**
 * A subagent's own record, for the child view.
 *
 * `found: false` on any failure as well as on a genuine miss: the view has
 * the prompt and the report either way, and a banner over them would say
 * less than the rows it hides.
 */
export async function fetchSubagentTranscript(agentId: string): Promise<SubagentTranscriptResult> {
  const missing: SubagentTranscriptResult = { agent_id: agentId, found: false, messages: [] }

  if (agentId === '') return missing

  try {
    const result = await gateway().request<SubagentTranscriptResult>('subagent.transcript', {
      agent_id: agentId,
      session_id: $sessionId.get(),
    })

    return result.found === true ? result : missing
  } catch {
    return missing
  }
}

/** The session's plan text, for the plan-review takeover. */
export async function fetchPlan(): Promise<string> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return ''

  try {
    const result = await gateway().request<{ plan?: string }>('plan.get', {
      session_id: sessionId,
    })

    return result.plan ?? ''
  } catch {
    // The panel shows its empty state; the decision is still available.
    return ''
  }
}

/**
 * Approve the plan-mode exit, choosing what happens to the permission mode.
 *
 * The mode rides as an explicit `setMode` update rather than one of the ask's
 * suggestions, because ExitPlanMode's ask carries none — the dialog is
 * expected to compose it (`_exit_plan_mode_call`: "The dialog path already
 * applied its setMode via chosen_updates BEFORE this runs").
 */
export async function respondPlan(
  approval: 'auto' | 'manual' | 'reject',
): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  $transcript.set(clearApproval($transcript.get()))

  try {
    await gateway().request('approval.respond', {
      choice: approval === 'reject' ? 'deny' : 'allow',
      session_id: sessionId,
      ...(approval === 'reject' ? {} : { updates: updatesFor(approval) }),
    })
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

export async function setApprovalMode(mode: 'manual' | 'off' | 'smart'): Promise<void> {
  const sessionId = $sessionId.get()

  // No session to configure yet: hold the choice so the control is not a
  // switch that does nothing, and apply it when the session appears.
  if (sessionId === null) {
    $pendingApprovalMode.set(mode)

    return
  }

  $pendingApprovalMode.set(null)

  try {
    await gateway().request('config.set', {
      session_id: sessionId,
      key: 'approvals.mode',
      value: mode,
    })
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

/* ── workspace ───────────────────────────────────────────────────────────── */

/**
 * List one directory level for the workspace picker.
 *
 * Errors propagate: an unreadable directory must reach the picker as a message,
 * not as an empty folder the user would read as "nothing here".
 */
export async function listDirectory(path?: string): Promise<DirectoryListing> {
  return gateway().request<DirectoryListing>(
    'fs.list_directory',
    path === undefined ? {} : { path },
  )
}

/**
 * A failure the sidebar can render when the socket itself is the problem.
 *
 * The backend answers a refused read inside the result; a rejected *request* —
 * the socket down, the call timed out — has no code of its own, so it arrives
 * as the generic one and the reader sees the carrier's message rather than a
 * blank panel.
 */
function unavailable(error: unknown): { error: WorkspaceFileFailure; ok: false } {
  return {
    error: {
      code: 'workspace-file/unavailable',
      message: error instanceof Error ? error.message : String(error),
    },
    ok: false,
  }
}

/**
 * One page of a workspace file's lines, for the sidebar's text preview.
 *
 * `offset` is 1-based, and the page length is the backend's own cap: a file has
 * no bound and a page does, so the reader asks for the next one when it reaches
 * the end of the loaded text.
 *
 * The session is named, never the root: the backend derives the workspace these
 * reads are confined to, because a boundary the client can move is not one.
 */
export async function readWorkspaceFile(
  path: string,
  offset: number,
): Promise<WorkspaceFileResult<FilePage>> {
  try {
    return await gateway().request<WorkspaceFileResult<FilePage>>('fs.read_file', {
      offset,
      path,
      session_id: $sessionId.get(),
    })
  } catch (error) {
    return unavailable(error)
  }
}

/**
 * A whole workspace file as bytes: what the image, PDF and HTML previews need,
 * since a picture has no page to read it by.
 */
export async function readWorkspaceBytes(path: string): Promise<WorkspaceFileResult<FileBytes>> {
  try {
    return await gateway().request<WorkspaceFileResult<FileBytes>>('fs.read_bytes', {
      path,
      session_id: $sessionId.get(),
    })
  } catch (error) {
    return unavailable(error)
  }
}

/**
 * A file named relative to another — the stylesheet or script an HTML document
 * declares beside itself. The backend joins the document's directory, so the
 * client never names the asset by an absolute path.
 */
export async function readWorkspaceRelated(
  path: string,
  relativePath: string,
): Promise<WorkspaceFileResult<FileBytes>> {
  try {
    return await gateway().request<WorkspaceFileResult<FileBytes>>('fs.read_related', {
      path,
      relative_path: relativePath,
      session_id: $sessionId.get(),
    })
  } catch (error) {
    return unavailable(error)
  }
}

/** One directory level under the session's workspace root, for the file tree. */
export async function listWorkspaceDir(
  path?: string,
): Promise<WorkspaceFileResult<WorkspaceLevel>> {
  try {
    return await gateway().request<WorkspaceFileResult<WorkspaceLevel>>('fs.list_dir', {
      session_id: $sessionId.get(),
      ...(path === undefined ? {} : { path }),
    })
  } catch (error) {
    return unavailable(error)
  }
}

/**
 * Point the next session at `path`.
 *
 * A live session's working directory is fixed at spawn, so choosing a new
 * folder while one is running starts a new session there rather than silently
 * leaving the choice to take effect at some unclear later point.
 */
export async function chooseWorkspace(path: string): Promise<void> {
  $workspace.set(path)

  if ($sessionId.get() !== null) await createSession({ cwd: path })
}

/**
 * Workspace paths matching `query`, for the composer's @ mentions.
 *
 * Ranked server-side: that is the same fuzzy scorer the TUI's quick-open uses,
 * and a second implementation here would rank the same query differently on
 * two surfaces of the same app.
 */
export async function searchFiles(query: string, limit = 12): Promise<string[]> {
  try {
    const result = await gateway().request<FileSearchResult>('fs.search_files', {
      cwd: $workspace.get(),
      limit,
      query,
    })

    return result.files ?? []
  } catch {
    // The menu simply does not open; a failed lookup is not worth a banner
    // over a draft the user is still typing.
    return []
  }
}

/**
 * Send an image to the session; the reply's id becomes its `[Image #N]` chip.
 *
 * Returns null on any failure, having already said why — the composer has
 * nothing useful to do with the error beyond not inserting a chip for an image
 * that is not there.
 */
export async function attachImage(file: Blob, name: string): Promise<number | null> {
  const sessionId = $sessionId.get()
  const navigationEpoch = sessionNavigationEpoch

  if (sessionId === null) {
    notice('Start a session before attaching an image.', 'error')

    return null
  }

  try {
    const url = await blobToDataUrl(file)
    if (sessionNavigationEpoch !== navigationEpoch || $sessionId.get() !== sessionId) return null
    const data = url.slice(url.indexOf(',') + 1)
    const result = await gateway().request<{ attached?: boolean; error?: string; id?: number }>(
      'image.attach',
      { data, name, session_id: sessionId },
    )

    if (sessionNavigationEpoch !== navigationEpoch || $sessionId.get() !== sessionId) return null

    if (result.attached !== true || typeof result.id !== 'number') {
      notice(result.error ?? 'Could not attach that image', 'error')

      return null
    }

    pendingImages.push({ id: result.id, name, url })
    return result.id
  } catch (error) {
    notice(errorText(error), 'error')

    return null
  }
}

/** A durable preview URL, also used to supply the backend's bare base64. */
async function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onerror = () => {
      reject(new Error('could not read the image'))
    }
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      resolve(result)
    }
    reader.readAsDataURL(blob)
  })
}

/* ── general settings ────────────────────────────────────────────────────── */

export async function refreshGeneralSettings(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) {
    $generalSettings.set({})

    return
  }

  try {
    $generalSettings.set(
      await gateway().request<GeneralSettingsResult>('settings.general', {
        session_id: sessionId,
      }),
    )
  } catch {
    // The section renders its needs-a-session state; nothing to shout about.
    $generalSettings.set({})
  }
}

export async function setOutputStyle(style: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const result = await gateway().request<{ error?: string; ok?: boolean }>(
      'settings.set_output_style',
      { session_id: sessionId, style },
    )

    // The agent refuses mid-turn and rejects unknown names; its wording is
    // the useful part, so it is shown rather than summarized.
    if (result.ok === false) notice(result.error === undefined || result.error === '' ? 'Could not change the output style' : result.error, 'error')
    else notice(`Output style: ${style}`)
  } catch (error) {
    notice(errorText(error), 'error')
  }

  await refreshGeneralSettings()
}

/**
 * Flip the end-of-turn recap.
 *
 * The write is a GLOBAL preference (the TUI's /recap), but a project or local
 * settings override wins at merge — the agent answers with the EFFECTIVE
 * post-write state and says why when they disagree, so its `note` is shown
 * rather than pretending the flip took.
 */
export async function setRecap(enabled: boolean): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const result = await gateway().request<{
      error?: string
      note?: string
      ok?: boolean
      value?: string
    }>('settings.set_recap', { session_id: sessionId, value: enabled ? 'on' : 'off' })

    if (result.ok === false) {
      notice(result.error === undefined || result.error === '' ? 'Could not change the recap setting' : result.error, 'error')
    } else if (result.note !== undefined && result.note !== '') {
      notice(`Recap ${result.value ?? (enabled ? 'on' : 'off')} — ${result.note}`)
    } else {
      notice(`Recap ${result.value ?? (enabled ? 'on' : 'off')}.`)
    }
  } catch (error) {
    notice(errorText(error), 'error')
  }

  await refreshGeneralSettings()
}

export async function setResponseLanguage(language: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const result = await gateway().request<{ language?: string; ok?: boolean }>(
      'settings.set_language',
      { language, session_id: sessionId },
    )

    if (result.ok === false) notice('Could not set the language', 'error')
    else notice(language.trim() === '' ? 'Response language cleared.' : `Responses in ${language}.`)
  } catch (error) {
    notice(errorText(error), 'error')
  }

  await refreshGeneralSettings()
}

/* ── providers (settings) ────────────────────────────────────────────────── */

export async function refreshProviders(): Promise<void> {
  const sessionId = $sessionId.get()

  try {
    $providers.set(
      await gateway().request<ProviderListResult>(
        'provider.list',
        sessionId === null ? {} : { session_id: sessionId },
      ),
    )
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

/**
 * Store an API key for a provider.
 *
 * The key goes one way. Nothing echoes it back — the reply is the provider's
 * refreshed catalog row, which reports `authenticated` and never the secret —
 * so the caller can clear its field the moment this returns.
 */
export async function saveProviderKey(slug: string, apiKey: string): Promise<boolean> {
  const sessionId = $sessionId.get()

  if (sessionId === null) {
    notice('Start a session before changing providers.', 'error')

    return false
  }

  try {
    const result = await gateway().request<ProviderMutationResult>('provider.save_key', {
      api_key: apiKey,
      session_id: sessionId,
      slug,
    })

    if (result.ok === false) {
      notice(result.error === undefined || result.error === '' ? 'Could not save that key' : result.error, 'error')

      return false
    }

    notice(`Saved the ${slug} key.`)
    await refreshProviders()
    await refreshModels()

    return true
  } catch (error) {
    notice(errorText(error), 'error')

    return false
  }
}

/**
 * Persist `default_provider` — what new sessions start on.
 *
 * No session required: changing the default is exactly what someone does when
 * the current default cannot even start one. A live session keeps its
 * provider; the refreshes below re-seat the settings badge and, when no
 * session exists, the composer's picker — which is what makes the NEXT
 * `session.create` actually ride the new default instead of the stale
 * pre-seeded selection.
 */
export async function setDefaultProvider(slug: string): Promise<void> {
  let applied = slug
  let model = ''

  try {
    const result = await gateway().request<{
      default?: string
      error?: string
      model?: string
      ok?: boolean
    }>('provider.set_default', { slug })

    if (result.ok === false) {
      notice(result.error === undefined || result.error === '' ? 'Could not set the default provider' : result.error, 'error')

      return
    }

    applied = result.default ?? slug
    model = result.model ?? ''
  } catch (error) {
    notice(errorText(error), 'error')

    return
  }

  // Move an UNUSED session onto the new default. "New session" creates its
  // session eagerly, so the welcome screen usually has one already — and a
  // live session's model outranks the catalog on the composer chip, so
  // without this the chip keeps naming the provider just replaced, directly
  // under a notice announcing the new one, and the next prompt runs on the
  // old provider.
  //
  // The test is only "nothing has happened in it yet": no messages, no turn
  // in flight. An earlier version also required the session to be running the
  // OUTGOING default, on the theory that anything else was a deliberate pick
  // worth preserving — but that read state this action cannot rely on
  // (`$providers.default` and `info.provider` are populated by different
  // round-trips at different times), so in the field it silently evaluated
  // false and the chip stayed wrong. A session with nothing in it has nothing
  // to preserve, and the user just said which provider they want.
  const transcript = $transcript.get()
  const unused =
    $sessionId.get() !== null && transcript.nodes.length === 0 && !transcript.running

  const switched = unused && model !== '' ? await setModel(model, applied) : true

  await refreshProviders()
  await refreshModels()

  // Only over a SUCCESSFUL switch: a refusal leaves its own message, and
  // announcing the new default over it would hide that this session is still
  // on the old one.
  if (switched) notice(`New sessions start on ${applied}.`)
}

/**
 * Clear a provider's stored credentials.
 *
 * The agent refuses the provider this session is running on, and says so when
 * the key lives in the shell environment where nothing here can remove it.
 * Both come back as their own message rather than a generic failure.
 */
export async function disconnectProvider(slug: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) {
    notice('Start a session before changing providers.', 'error')

    return
  }

  try {
    const result = await gateway().request<ProviderMutationResult>('provider.disconnect', {
      session_id: sessionId,
      slug,
    })

    if (result.ok === false) {
      notice(result.error === undefined || result.error === '' ? 'Could not disconnect' : result.error, 'error')

      return
    }

    notice(result.message === undefined || result.message === '' ? `Disconnected ${slug}.` : result.message)
    await refreshProviders()
    await refreshModels()
  } catch (error) {
    notice(errorText(error), 'error')
  }
}

/* ── model + catalogs ────────────────────────────────────────────────────── */

export async function refreshModels(): Promise<void> {
  try {
    const sessionId = $sessionId.get()
    const result = await gateway().request<ModelOptionsResult>(
      'model.options',
      sessionId === null ? {} : { session_id: sessionId },
    )

    $models.set(result)
  } catch {
    /* the picker degrades to the session's own model chip */
  }
}

/**
 * Re-read the effort ladder for whatever model the session is on.
 *
 * Called after every model switch, not once at boot: the ladder belongs to the
 * model, so a model with no effort parameter must take the chip away rather
 * than leave the previous model's levels on screen.
 */
export async function refreshEffort(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) {
    // Nothing to ask and nowhere to apply a choice — say so rather than
    // offering a control that would silently do nothing.
    $effort.set({ supported: false })

    return
  }

  try {
    $effort.set(
      await gateway().request<EffortOptionsResult>('effort.options', { session_id: sessionId }),
    )
  } catch {
    // Losing the ladder hides the chip; it does not disturb the session.
    $effort.set({ supported: false })
  }
}

export async function setEffort(effort: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  // Optimistic: the menu should mark the new level immediately, and the
  // refresh below replaces this with whatever the session really took.
  $effort.set({ ...$effort.get(), current: effort })

  try {
    const result = await gateway().request<ConfigSetResult>('config.set', {
      key: 'effort',
      session_id: sessionId,
      value: effort,
    })

    if (result.ok === false) notice(result.error ?? 'Could not change effort', 'error')
    else notice(effortChangeNotice(result.value ?? effort, result.persisted))
  } catch (error) {
    notice(errorText(error), 'error')
  }

  await refreshEffort()
}

/** What `config.set` answers for a model or effort write. */
interface ConfigSetResult {
  error?: string
  ok?: boolean
  /**
   * Whether the pick was also saved as the default for new sessions. The
   * gateway echoes it only when the agent said, so an older backend that
   * never did reads as "unknown" — and the notice then claims neither.
   */
  persisted?: boolean
  value?: string
}

/**
 * The notice for a completed model switch, worded on the backend's verdict:
 * a pick is written to the user's settings as the default for new sessions —
 * the TUI's and the desktop's too, which read the same file — so the chip has
 * to say so, and must not say so over a switch the transport could not save.
 */
export function modelSwitchNotice(model: string, persisted?: boolean): string {
  if (persisted === true) return `Set model to ${model} and saved as your default for new sessions`

  if (persisted === false) return `Set model to ${model} for this session`

  return `Model: ${model}`
}

/** Same three-way wording for an effort change; `level` is the rung as the chip spells it. */
export function effortChangeNotice(level: string, persisted?: boolean): string {
  if (persisted === true) return `Set effort level to ${level} and saved as your default for new sessions`

  if (persisted === false) return `Set effort level to ${level} for this session`

  return `Effort: ${level}`
}

/** Switch the session's model; false when the agent refused it. */
export async function setModel(model: string, provider?: string): Promise<boolean> {
  const sessionId = $sessionId.get()

  if (sessionId === null) {
    // No live session yet: hold the pick for the session it is FOR. Not in
    // $models — that is the catalog, and a selection written there outlives
    // its session and leaks into later creates.
    $pendingModel.set({ model, ...(provider === undefined ? {} : { provider }) })

    return true
  }

  // No scope flag: the gateway saves the pick as the default for new sessions
  // and reports `persisted`, which is what the notice is worded on.
  const value = provider === undefined ? model : `${model} --provider ${provider}`
  let ok = true

  try {
    const result = await gateway().request<ConfigSetResult>('config.set', {
      session_id: sessionId,
      key: 'model',
      value,
    })

    if (result.ok === false) {
      ok = false
      notice(result.error ?? 'Could not switch model', 'error')
    } else notice(modelSwitchNotice(result.value ?? model, result.persisted))
  } catch (error) {
    ok = false
    notice(errorText(error), 'error')
  }

  await refreshModels()
  // The ladder belongs to the model, so a switch can add, change or remove it.
  await refreshEffort()

  return ok
}

export async function refreshCommands(): Promise<void> {
  try {
    const sessionId = $sessionId.get()
    const catalog = await gateway().request<CommandsCatalogResult>(
      'commands.catalog',
      sessionId === null ? {} : { session_id: sessionId },
    )

    const hints = catalog.hints ?? {}
    const skills = catalog.skills ?? {}
    const entries: CommandEntry[] = (catalog.pairs ?? []).map(([name, description]) => ({
      description,
      hint: hints[name],
      name,
      origin: skills[name]?.origin,
    }))

    $commands.set(entries)
  } catch {
    /* the popover simply has nothing to offer */
  }
}

export async function refreshProjects(): Promise<void> {
  if ($projectsLoading.get()) return

  $projectsLoading.set(true)

  try {
    const tree = await gateway().request<ProjectsTreeResult>('projects.tree', { preview_limit: 5 })

    $projects.set(tree.projects ?? [])
  } catch {
    /* keep whatever the sidebar last showed */
  } finally {
    $projectsLoading.set(false)
  }
}

export async function refreshUsage(): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

  try {
    const usage = await gateway().request<ContextUsageResult>('session.usage', {
      session_id: sessionId,
    })

    $contextUsage.set(usage)
  } catch {
    /* the meter hides itself when it has no reading */
  }
}
