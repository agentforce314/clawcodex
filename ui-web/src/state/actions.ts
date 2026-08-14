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
  DirectoryListing,
  GatewayEvent,
  ModelOptionsResult,
  ProjectsTreeResult,
  SessionResumeResult,
  SlashResult,
} from '../gateway/protocol.ts'
import {
  $bootError,
  $bootPhase,
  $commands,
  $connection,
  $contextUsage,
  $detailsNodeId,
  $models,
  $notice,
  $projects,
  $projectsLoading,
  $queue,
  $sessionId,
  $sessionLoading,
  $sessionTitle,
  $storedSessionId,
  $trajectory,
  $transcript,
  $workspace,
} from './store.ts'
import {
  applyTrajectoryEvent,
  emptyTrajectory,
  recordPrompt,
} from './trajectory.ts'
import {
  appendUserMessage,
  applyEvent,
  clearApproval,
  emptyTranscript,
  hydrateStoredMessages,
  markTurnStarted,
} from './transcript.ts'

let client: GatewayClient | null = null

export function gateway(): GatewayClient {
  if (client === null) throw new Error('gateway not started')

  return client
}

/** Test seam: install a client with an injected socket factory. */
export function setGatewayClient(next: GatewayClient | null): void {
  client = next
}

function notice(text: string, tone: 'error' | 'info' = 'info'): void {
  $notice.set({ text, tone })
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
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
}

/**
 * The workspace the backend was started in.
 *
 * It is the directory the first session will run in, so the hero has to name
 * it *before* that session exists — and only REST knows it that early
 * (`session.info` carries a cwd, but not until a session is created).
 */
async function seedWorkspace(target: BackendTarget): Promise<void> {
  if ($workspace.get() !== '') return

  try {
    const status = await apiGet<{ workspace?: string }>(target, '/status')

    if (typeof status.workspace === 'string' && status.workspace !== '') {
      $workspace.set(status.workspace)
    }
  } catch {
    /* the hero simply omits the workspace chip */
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

  $transcript.set(applyEvent($transcript.get(), event))
  $trajectory.set(applyTrajectoryEvent($trajectory.get(), event))

  if (event.type === 'message.complete') {
    void refreshUsage()
    drainQueue()
  }
}

/* ── sessions ────────────────────────────────────────────────────────────── */

export interface SessionSpawnOptions {
  cwd?: string
  effort?: string
  model?: string
  provider?: string
}

export async function createSession(options: SessionSpawnOptions = {}): Promise<void> {
  $transcript.set(emptyTranscript())
  $trajectory.set(emptyTrajectory())
  $detailsNodeId.set(null)
  $sessionTitle.set('')
  $sessionId.set(null)
  $storedSessionId.set(null)

  const params: Record<string, unknown> = {}

  if (options.cwd !== undefined && options.cwd !== '') params.cwd = options.cwd
  if (options.provider !== undefined && options.provider !== '') params.provider = options.provider
  if (options.model !== undefined && options.model !== '') params.model = options.model
  if (options.effort !== undefined && options.effort !== '') params.reasoning_effort = options.effort

  try {
    const result = await gateway().request<SessionResumeResult>('session.create', params)

    adoptSession(result)
  } catch (error) {
    notice(`Could not start a session: ${errorText(error)}`, 'error')
  }
}

export async function resumeSession(storedId: string, cwd?: string): Promise<void> {
  $transcript.set(emptyTranscript())
  // A replayed transcript carries no timings, so the ledger starts empty and
  // records only what this window observes from here on.
  $trajectory.set(emptyTrajectory())
  $detailsNodeId.set(null)
  $sessionLoading.set(true)

  const params: Record<string, unknown> = { session_id: storedId }

  if (cwd !== undefined && cwd !== '') params.cwd = cwd

  try {
    const result = await gateway().request<SessionResumeResult>('session.resume', params)

    adoptSession(result)

    if (result.messages !== undefined && result.messages.length > 0) {
      $transcript.set({
        ...$transcript.get(),
        nodes: hydrateStoredMessages(result.messages),
      })
    }
  } catch (error) {
    notice(`Could not resume that session: ${errorText(error)}`, 'error')
  } finally {
    $sessionLoading.set(false)
  }
}

function adoptSession(result: SessionResumeResult): void {
  $sessionId.set(result.session_id)
  $storedSessionId.set(result.stored_session_id ?? result.session_id)

  if (result.info !== undefined) {
    $transcript.set({ ...$transcript.get(), info: { ...$transcript.get().info, ...result.info } })

    if (result.info.cwd !== undefined) $workspace.set(result.info.cwd)
  }

  void refreshProjects()
  void refreshUsage()
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

  try {
    await gateway().request('session.clear', { session_id: sessionId })
    $transcript.set({ ...emptyTranscript(), info: $transcript.get().info })
    $trajectory.set(emptyTrajectory())
    notice('Conversation cleared.')
  } catch (error) {
    notice(errorText(error), 'error')
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

  $transcript.set(markTurnStarted(appendUserMessage($transcript.get(), text)))
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

export async function setApprovalMode(mode: 'manual' | 'smart' | 'off'): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) return

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

export async function setModel(model: string, provider?: string): Promise<void> {
  const sessionId = $sessionId.get()

  if (sessionId === null) {
    // No live session yet: the selection rides the next session.create.
    $models.set({ ...$models.get(), model, provider: provider ?? $models.get().provider })

    return
  }

  const value = provider === undefined ? model : `${model} --provider ${provider}`

  try {
    const result = await gateway().request<{ error?: string; ok?: boolean; value?: string }>(
      'config.set',
      { session_id: sessionId, key: 'model', value },
    )

    if (result.ok === false) notice(result.error ?? 'Could not switch model', 'error')
    else notice(`Model: ${result.value ?? model}`)
  } catch (error) {
    notice(errorText(error), 'error')
  }

  await refreshModels()
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
