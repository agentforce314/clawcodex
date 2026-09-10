import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  clearSession,
  closeSubagent,
  createSession,
  dequeue,
  interrupt,
  openSubagent,
  renameSession,
  respondApproval,
  fetchPlan,
  respondPlan,
  respondQuestion,
  retryLastTurn,
  setApprovalMode,
  setEffort,
  setModel,
  submitPrompt,
} from '../state/actions.ts'
import {
  $backendNano,
  $commands,
  $connection,
  $contextUsage,
  $conversationTab,
  $effort,
  $models,
  $pendingApprovalMode,
  $pendingModel,
  $queue,
  $sessionId,
  $sessionLoading,
  $sessionTitle,
  $subagentView,
  $trajectory,
  $transcript,
  $workspace,
} from '../state/store.ts'
import { AgentsPanel } from '../agents/AgentsPanel.tsx'
import { subagentCatalog } from '../state/subagents.ts'
import { currentTodos } from '../state/todo-progress.ts'
import { trajectoryStats } from '../state/trajectory.ts'
import { WorkspaceChip } from '../workspace/WorkspaceChip.tsx'
import { TrajectoryView } from '../trajectory/TrajectoryView.tsx'
import { $detailsWidth, openDetails } from '../state/layout.ts'
import { AgentIcon, ArrowDownIcon, LayersIcon, MessageIcon, PlusIcon } from '../ui/icons.tsx'
import { ApprovalPanel } from './ApprovalPanel.tsx'
import { ChatView } from './ChatView.tsx'
import { HeroShell } from './HeroShell.tsx'
import { InputBar } from './InputBar.tsx'
import { PlanReviewPanel } from './PlanReviewPanel.tsx'
import { QuestionComposer } from './QuestionComposer.tsx'
import { closeSidebar } from '../sidebar-right/store.ts'
import { QueueDock } from './QueueDock.tsx'
import { StatsLine } from './StatsLine.tsx'
import { SubagentChip } from './SubagentChip.tsx'
import { SubagentView } from './SubagentView.tsx'
import { TodoPanel } from './TodoPanel.tsx'
import css from './ConversationRoot.module.css'

/** Distance from the bottom, in px, still counted as "at the bottom". */
const STICK_THRESHOLD = 96

/**
 * The centre column.
 *
 * It owns exactly one scrollport, holding both the transcript and the sticky
 * composer seat — so a wheel gesture anywhere in the column, the input card
 * included, moves the conversation. It also owns the two phases (centred hero
 * before the first message, docked composer after), which are the same
 * components in two positions rather than two screens.
 */
const TAB_LABELS = { agents: 'Agents', chat: 'Chat', trajectory: 'Trajectory' } as const

export function ConversationRoot() {
  const transcript = useStore($transcript)
  const sessionId = useStore($sessionId)
  const sessionTitle = useStore($sessionTitle)
  const workspace = useStore($workspace)
  const models = useStore($models)
  const effort = useStore($effort)
  const usage = useStore($contextUsage)
  const queue = useStore($queue)
  const connection = useStore($connection)
  const commands = useStore($commands)
  const detailsWidth = useStore($detailsWidth)
  const pendingApproval = useStore($pendingApprovalMode)
  const pendingModel = useStore($pendingModel)
  const loading = useStore($sessionLoading)
  const tab = useStore($conversationTab)
  const trajectory = useStore($trajectory)
  const backendNano = useStore($backendNano)
  const subagentView = useStore($subagentView)
  const stats = useMemo(() => trajectoryStats(trajectory), [trajectory])
  const todos = useMemo(() => currentTodos(transcript.nodes), [transcript.nodes])
  const subagents = useMemo(
    () => subagentCatalog(transcript.nodes, transcript.agents),
    [transcript.agents, transcript.nodes],
  )
  // The child on show, if the key still names one — a cleared conversation
  // takes its delegations with it, and the view falls back to the session.
  const child = subagentView === null ? undefined : subagents.find(entry => entry.key === subagentView)

  // The session's own truth once session.info reported it, else the backend's
  // process-wide fact (/api/status) — same shape as the approval mode and
  // model fallbacks in the composer below. The two can only disagree across a
  // backend restart, and the session's word wins.
  const nano = transcript.info.nano ?? backendNano

  const [draft, setDraft] = useState('')
  // null while in flight, so the panel can say "loading" rather than "no plan".
  const [plan, setPlan] = useState<string | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const scroller = useRef<HTMLDivElement | null>(null)
  const seat = useRef<HTMLDivElement | null>(null)
  const flow = useRef<HTMLDivElement | null>(null)

  // A replay in flight is NOT the empty state: showing the hero over a
  // conversation that is about to land reads as "your session is gone".
  const hero = transcript.nodes.length === 0 && !transcript.running && !loading

  // Stick to the bottom while new content arrives, unless the reader scrolled
  // away — reading back through a long turn must not be yanked forward.
  useLayoutEffect(() => {
    if (!atBottom) return

    const element = scroller.current

    if (element === null) return

    element.scrollTop = element.scrollHeight
  }, [atBottom, transcript.nodes, transcript.running])

  const onScroll = useCallback(() => {
    const element = scroller.current

    if (element === null) return

    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    setAtBottom(distance <= STICK_THRESHOLD)
  }, [])

  // Content grows without the node list changing — a highlight lands, an
  // image loads, a card is expanded. While the reader is at the bottom, any
  // such growth re-pins; otherwise it would leak the flow past the fold one
  // async paint at a time.
  useEffect(() => {
    if (!atBottom) return

    const element = flow.current
    const scrollerEl = scroller.current

    if (element === null || scrollerEl === null) return

    const observer = new ResizeObserver(() => {
      scrollerEl.scrollTop = scrollerEl.scrollHeight
    })

    observer.observe(element)

    return () => {
      observer.disconnect()
    }
  }, [atBottom, hero, tab])

  // The back-to-bottom control clears the live composer height, which grows
  // with the draft. Measured rather than assumed so the button never overlaps.
  useEffect(() => {
    const element = seat.current

    if (element === null) return

    const observer = new ResizeObserver(entries => {
      const height = entries[0]?.contentRect.height

      if (height !== undefined) {
        scroller.current?.style.setProperty('--cc-composer-height', `${Math.round(height)}px`)
      }
    })

    observer.observe(element)

    return () => {
      observer.disconnect()
    }
  }, [hero])

  const scrollToBottom = useCallback(() => {
    const element = scroller.current

    if (element === null) return

    element.scrollTo({ behavior: 'smooth', top: element.scrollHeight })
    setAtBottom(true)
  }, [])

  const onSubmit = useCallback(
    (text: string) => {
      void submitPrompt(text, { cwd: workspace })
      setAtBottom(true)
    },
    [workspace],
  )

  // ExitPlanMode's ask carries no plan — the plan is a session FILE — so it is
  // fetched when that ask arrives, and cleared when it goes.
  const planAsk = transcript.approval?.tool_name === 'ExitPlanMode'

  useEffect(() => {
    if (!planAsk) {
      setPlan(null)

      return
    }

    let live = true

    void fetchPlan().then(text => {
      if (live) setPlan(text)
    })

    return () => {
      live = false
    }
  }, [planAsk])

  const composer = (
    <InputBar
      // The session's mode when there is one, otherwise the choice being held
      // for the session that does not exist yet.
      approvalMode={transcript.info.approval_mode ?? pendingApproval ?? undefined}
      draft={draft}
      effort={effort}
      hero={hero}
      models={models}
      nano={nano === true}
      onApprovalModeChange={mode => {
        void setApprovalMode(mode)
      }}
      onDraftChange={setDraft}
      onEffortChange={level => {
        void setEffort(level)
      }}
      onModelChange={(model, provider) => {
        void setModel(model, provider)
      }}
      onStop={() => {
        void interrupt()
      }}
      onSubmit={onSubmit}
      running={transcript.running}
      // The session's model when there is one, else a welcome-screen pick
      // held for the session that does not exist yet — same shape as the
      // approval mode above. With neither, the chip falls back to the
      // catalog, which sessionless reports the config default.
      sessionModel={transcript.info.model ?? pendingModel?.model}
      sessionProvider={transcript.info.provider ?? pendingModel?.provider}
      usage={usage}
      vision={transcript.info.vision}
    />
  )

  /**
   * What sits in the composer seat. Both takeovers block the agent, so at most
   * one can be pending — but the order is stated rather than assumed, and an
   * approval wins: it can be raised by a sub-agent while the main turn is
   * already parked on a question, and the question is still there underneath
   * once the tool is allowed or denied.
   */
  const seatPanel =
    planAsk ? (
      <PlanReviewPanel
        onApprove={approval => {
          void respondPlan(approval)
        }}
        onReject={() => {
          void respondPlan('reject')
        }}
        plan={plan}
      />
    ) : transcript.approval !== undefined ? (
      <ApprovalPanel
        onRespond={choice => {
          void respondApproval(choice)
        }}
        request={transcript.approval}
      />
    ) : transcript.question !== undefined ? (
      <QuestionComposer
        onRespond={(action, answers) => {
          void respondQuestion(action, answers)
        }}
        request={transcript.question}
      />
    ) : (
      composer
    )

  const banner =
    connection === 'reconnecting' || connection === 'error' ? (
      <div className={[css.banner, connection === 'error' ? css.bannerError : ''].join(' ')}>
        {connection === 'error'
          ? 'Lost the connection to the ClawCodex backend. Retrying…'
          : 'Reconnecting to the ClawCodex backend…'}
      </div>
    ) : null

  return (
    <div className={css.root}>
      {!hero && (
        <div className={css.header}>
          <div className={css.titleCluster}>
            {child === undefined ? (
              <>
                <MessageIcon size={16} />
                <input
                  aria-label="Session title"
                  className={css.title}
                  onBlur={event => {
                    const value = event.target.value.trim()

                    if (value !== '' && value !== sessionTitle) void renameSession(value)
                  }}
                  onChange={event => {
                    $sessionTitle.set(event.target.value)
                  }}
                  onKeyDown={event => {
                    if (event.key === 'Enter') event.currentTarget.blur()
                  }}
                  placeholder="Untitled session"
                  value={sessionTitle}
                />
                {/* "N subagents ▾": the delegations this session made, and
                    the way into each. Absent until there is one. */}
                {subagents.length > 0 && (
                  <>
                    <span className={css.crumbSep}>/</span>
                    <SubagentChip entries={subagents} onOpen={openSubagent} variant="count" />
                  </>
                )}
                {workspace !== '' && (
                  <>
                    <span className={css.crumbSep}>/</span>
                    <WorkspaceChip variant="crumb" />
                  </>
                )}
              </>
            ) : (
              <>
                <AgentIcon size={16} />
                {/* The parent's title is the way back; the child's name is
                    a switcher among its siblings, as the reference does it. */}
                <button
                  className={css.crumbParent}
                  onClick={closeSubagent}
                  title="Back to the session"
                  type="button"
                >
                  {sessionTitle === '' ? 'Untitled session' : sessionTitle}
                </button>
                <span className={css.crumbSep}>/</span>
                <SubagentChip
                  currentKey={child.key}
                  entries={subagents}
                  onOpen={openSubagent}
                  variant="switcher"
                />
              </>
            )}
          </div>
          <div className={css.headerActions}>
            <button
              className={css.iconButton}
              disabled={sessionId === null}
              onClick={() => {
                void clearSession()
              }}
              title="Clear this conversation"
              type="button"
            >
              <MessageIcon size={16} />
            </button>
            <button
              className={css.iconButton}
              onClick={() => {
                if (detailsWidth === 0) openDetails()
                else closeSidebar()
              }}
              title="Sidebar (⌘I)"
              type="button"
            >
              <LayersIcon size={16} />
            </button>
            <button
              className={css.iconButton}
              onClick={() => {
                void createSession({ cwd: workspace })
              }}
              title="New session (⇧⌘N)"
              type="button"
            >
              <PlusIcon size={16} />
            </button>
          </div>
        </div>
      )}
      {!hero && child === undefined && (
        <div className={css.tabs} role="tablist">
          {(['chat', 'trajectory', 'agents'] as const).map(id => (
            <button
              aria-selected={tab === id}
              className={[css.tab, tab === id ? css.tabActive : ''].filter(Boolean).join(' ')}
              key={id}
              onClick={() => {
                $conversationTab.set(id)
              }}
              role="tab"
              type="button"
            >
              {TAB_LABELS[id]}
            </button>
          ))}
        </div>
      )}
      {banner}
      {!hero && child !== undefined ? (
        <div className={css.viewBody}>
          <SubagentView entry={child} workspace={workspace} />
        </div>
      ) : !hero && tab === 'agents' ? (
        <div className={css.viewBody}>
          <AgentsPanel />
        </div>
      ) : !hero && tab === 'trajectory' ? (
        <>
          <div className={css.viewBody}>
            <TrajectoryView trajectory={trajectory} />
          </div>
          <div className={css.footer}>
            {seatPanel}
            <StatsLine stats={stats} />
          </div>
        </>
      ) : (
      <div
        className={css.scrollBody}
        data-phase={hero ? 'hero' : 'active'}
        onScroll={onScroll}
        ref={scroller}
      >
        {hero ? (
          <HeroShell
            composer={composer}
            onSuggestion={commands.length === 0 ? undefined : setDraft}
          />
        ) : (
          <>
            <div className={css.flow} ref={flow}>
              {loading && transcript.nodes.length === 0 ? (
                <div className={css.settling}>Loading session…</div>
              ) : (
                <ChatView
                  agents={transcript.agents}
                  nodes={transcript.nodes}
                  onEditPrompt={setDraft}
                  onRetry={() => {
                    void retryLastTurn()
                  }}
                  running={transcript.running}
                  turnStartedAt={transcript.turnStartedAt}
                  workspace={workspace}
                />
              )}
            </div>
            {!atBottom && (
              <div className={css.toBottomSlot}>
                <button
                  aria-label="Scroll to the newest message"
                  className={css.toBottom}
                  onClick={scrollToBottom}
                  type="button"
                >
                  <ArrowDownIcon size={16} />
                </button>
              </div>
            )}
            <div className={css.composerSeat} ref={seat}>
              <TodoPanel todos={todos} />
              <QueueDock items={queue} onRemove={dequeue} />
              {seatPanel}
              <StatsLine stats={stats} />
            </div>
          </>
        )}
      </div>
      )}
    </div>
  )
}
