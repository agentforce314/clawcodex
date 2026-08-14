import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  clearSession,
  createSession,
  dequeue,
  interrupt,
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
  $commands,
  $connection,
  $contextUsage,
  $conversationTab,
  $effort,
  $models,
  $pendingApprovalMode,
  $queue,
  $sessionId,
  $sessionLoading,
  $sessionTitle,
  $trajectory,
  $transcript,
  $workspace,
} from '../state/store.ts'
import { trajectoryStats } from '../state/trajectory.ts'
import { RunStatsBar } from '../trajectory/RunStatsBar.tsx'
import { WorkspaceChip } from '../workspace/WorkspaceChip.tsx'
import { TrajectoryView } from '../trajectory/TrajectoryView.tsx'
import { $detailsWidth, closeDetails, openDetails } from '../state/layout.ts'
import { ArrowDownIcon, LayersIcon, MessageIcon, PlusIcon } from '../ui/icons.tsx'
import { ApprovalPanel } from './ApprovalPanel.tsx'
import { ChatView } from './ChatView.tsx'
import { HeroShell } from './HeroShell.tsx'
import { InputBar } from './InputBar.tsx'
import { PlanReviewPanel } from './PlanReviewPanel.tsx'
import { QuestionComposer } from './QuestionComposer.tsx'
import { QueueDock } from './QueueDock.tsx'
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
  const loading = useStore($sessionLoading)
  const tab = useStore($conversationTab)
  const trajectory = useStore($trajectory)
  const stats = useMemo(() => trajectoryStats(trajectory), [trajectory])

  const [draft, setDraft] = useState('')
  // null while in flight, so the panel can say "loading" rather than "no plan".
  const [plan, setPlan] = useState<string | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const scroller = useRef<HTMLDivElement | null>(null)
  const seat = useRef<HTMLDivElement | null>(null)

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
      sessionModel={transcript.info.model}
      sessionProvider={transcript.info.provider}
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
            {workspace !== '' && (
              <>
                <span className={css.crumbSep}>/</span>
                <WorkspaceChip variant="crumb" />
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
                else closeDetails()
              }}
              title="Session details (⌘I)"
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
      {!hero && (
        <div className={css.tabs} role="tablist">
          {(['chat', 'trajectory'] as const).map(id => (
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
              {id === 'chat' ? 'Chat' : 'Trajectory'}
            </button>
          ))}
        </div>
      )}
      {banner}
      {!hero && tab === 'trajectory' ? (
        <>
          <div className={css.viewBody}>
            <TrajectoryView trajectory={trajectory} />
          </div>
          <div className={css.footer}>
            {seatPanel}
            <RunStatsBar
              model={transcript.info.model}
              provider={transcript.info.provider}
              stats={stats}
            />
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
            <div className={css.flow}>
              {loading && transcript.nodes.length === 0 ? (
                <div className={css.settling}>Loading session…</div>
              ) : (
                <ChatView
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
              <QueueDock items={queue} onRemove={dequeue} />
              {seatPanel}
              <RunStatsBar
                model={transcript.info.model}
                provider={transcript.info.provider}
                stats={stats}
              />
            </div>
          </>
        )}
      </div>
      )}
    </div>
  )
}
