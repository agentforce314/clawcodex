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
  EffortOptionsResult,
  FileSearchResult,
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
  $effort,
  $detailsNodeId,
  $models,
  $notice,
  $pendingApprovalMode,
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
import { updatesFor } from '../conversation/PlanReviewPanel.tsx'
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
  cwd?: string
  effort?: string
  model?: string
  provider?: string
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

export async function createSession(options: SessionSpawnOptions = {}): Promise<void> {
  $transcript.set(emptyTranscript())
  $trajectory.set(emptyTrajectory())
  $detailsNodeId.set(null)
  $sessionTitle.set('')
  $sessionId.set(null)
  $storedSessionId.set(null)

  const params: Record<string, unknown> = { capabilities: CAPABILITIES }

  // A model chosen before there was a session lives in $models, because
  // `setModel` has nowhere else to put it. Reading it here is what makes that
  // choice ride the create — without this the picker on the welcome screen
  // changed the chip and nothing else, and the session spawned on the config
  // default provider instead. Explicit options still win: a caller naming a
  // model means it.
  const selected = $models.get()
  const provider = options.provider ?? selected.provider
  const model = options.model ?? selected.model

  if (options.cwd !== undefined && options.cwd !== '') params.cwd = options.cwd
  if (provider !== undefined && provider !== '') params.provider = provider
  if (model !== undefined && model !== '') params.model = model
  if (options.effort !== undefined && options.effort !== '') params.reasoning_effort = options.effort

  try {
    const result = await gateway().request<SessionResumeResult>('session.create', params)

    adoptSession(result)
    await applyPendingApprovalMode()
  } catch (error) {
    notice(`Could not start a session: ${errorText(error)}`, 'error')
  } finally {
    // A create that fails must not leave a "Loading session…" spinner over an
    // empty transcript: the composer is still usable, and the hero is the
    // honest place to land. Resume owns this flag too, so clearing it here
    // covers a create that interleaves with one.
    $sessionLoading.set(false)
  }
}

export async function resumeSession(storedId: string, cwd?: string): Promise<void> {
  $transcript.set(emptyTranscript())
  // A replayed transcript carries no timings, so the ledger starts empty and
  // records only what this window observes from here on.
  $trajectory.set(emptyTrajectory())
  $detailsNodeId.set(null)
  $sessionLoading.set(true)

  const params: Record<string, unknown> = { capabilities: CAPABILITIES, session_id: storedId }

  if (cwd !== undefined && cwd !== '') params.cwd = cwd

  try {
    const result = await gateway().request<SessionResumeResult>('session.resume', params)

    adoptSession(result)

    // Without this the header falls back to its "Untitled session"
    // placeholder while the sidebar row the user just clicked keeps showing
    // the real name.
    if (result.title !== undefined && result.title !== '') $sessionTitle.set(result.title)

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

  if (sessionId === null) {
    notice('Start a session before attaching an image.', 'error')

    return null
  }

  try {
    const data = await blobToBase64(file)
    const result = await gateway().request<{ attached?: boolean; error?: string; id?: number }>(
      'image.attach',
      { data, name, session_id: sessionId },
    )

    if (result.attached !== true || typeof result.id !== 'number') {
      notice(result.error ?? 'Could not attach that image', 'error')

      return null
    }

    return result.id
  } catch (error) {
    notice(errorText(error), 'error')

    return null
  }
}

/** Blob to bare base64 (FileReader hands back a data: URL). */
async function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onerror = () => {
      reject(new Error('could not read the image'))
    }
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.readAsDataURL(blob)
  })
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
    const result = await gateway().request<{ error?: string; ok?: boolean; value?: string }>(
      'config.set',
      { key: 'effort', session_id: sessionId, value: effort },
    )

    if (result.ok === false) notice(result.error ?? 'Could not change effort', 'error')
    else notice(`Effort: ${result.value ?? effort}`)
  } catch (error) {
    notice(errorText(error), 'error')
  }

  await refreshEffort()
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
  // The ladder belongs to the model, so a switch can add, change or remove it.
  await refreshEffort()
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
