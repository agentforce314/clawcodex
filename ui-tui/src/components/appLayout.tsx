import { AlternateScreen, Box, NoSelect, ScrollBox, Text } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'
import { Fragment, memo, useMemo, useRef } from 'react'

import { useGateway } from '../app/gatewayContext.js'
import type { AppLayoutProps } from '../app/interfaces.js'
import { $isBlocked, $overlayState, patchOverlayState } from '../app/overlayStore.js'
import { $uiState } from '../app/uiStore.js'
import { usePet } from '../app/usePet.js'
import { INLINE_MODE, SHOW_FPS, TERMUX_TUI_MODE } from '../config/env.js'
import { PLACEHOLDER } from '../content/placeholders.js'
import { prevRenderedMsg } from '../domain/blockLayout.js'
import {
  COMPOSER_PROMPT_GAP_WIDTH,
  composerPromptWidth,
  inputVisualHeight,
  stableComposerColumns
} from '../lib/inputMetrics.js'
import { PerfPane } from '../lib/perfPane.js'
import { composerPromptText } from '../lib/prompt.js'
import type { Theme } from '../theme.js'

import { AgentsOverlay } from './agentsOverlay.js'
import { GoodVibesHeart, StatusRule, StickyPromptTracker, TranscriptScrollbar } from './appChrome.js'
import { FloatingOverlays, PromptZone } from './appOverlays.js'
import { Banner, Panel, SessionPanel } from './branding.js'
import { BusyLine } from './busyLine.js'
import { ComposerFooter } from './composerFooter.js'
import { FpsOverlay } from './fpsOverlay.js'
import { GoalIndicator } from './goalIndicator.js'
import { HelpHint } from './helpHint.js'
import { MessageLine } from './messageLine.js'
import { PetKitty, PetSprite } from './petSprite.js'
import { QueuedMessages } from './queuedMessages.js'
import { SessionStatsLine } from './sessionStatsLine.js'
import { LiveTodoPanel, StreamingAssistant } from './streamingAssistant.js'
import { TextInput, type TextInputMouseApi } from './textInput.js'

// Petdex mascot — sits just above the composer, right-aligned. Renders
// nothing unless a pet is installed + enabled (`clawcodex pets select <slug>`),
// so it's a no-op for everyone else.
const PetPane = memo(function PetPane() {
  const { enabled, grid, kitty } = usePet()

  if (!enabled || (!grid && !kitty)) {
    return null
  }

  return (
    <NoSelect flexShrink={0} justifyContent="flex-end" paddingX={1} width="100%">
      {kitty ? <PetKitty color={kitty.color} placeholder={kitty.placeholder} /> : null}
      {!kitty && grid ? <PetSprite grid={grid} /> : null}
    </NoSelect>
  )
})

const PromptPrefix = memo(function PromptPrefix({
  bold = false,
  color,
  promptText,
  width
}: {
  bold?: boolean
  color: string
  promptText: string
  width: number
}) {
  const glyphWidth = Math.max(1, width - COMPOSER_PROMPT_GAP_WIDTH)

  return (
    <Box width={width}>
      <Box width={glyphWidth}>
        <Text bold={bold} color={color}>
          {promptText}
        </Text>
      </Box>
      <Box width={COMPOSER_PROMPT_GAP_WIDTH} />
    </Box>
  )
})

const TranscriptPane = memo(function TranscriptPane({
  actions,
  composer,
  progress,
  transcript
}: Pick<AppLayoutProps, 'actions' | 'composer' | 'progress' | 'transcript'>) {
  const ui = useStore($uiState)

  // Index of the first user-role message; every later user message gets a
  // small dash above it so multi-turn transcripts visually segment by
  // turn. -1 when no user message has been sent yet → no separator ever
  // renders.
  const firstUserIdx = useMemo(
    () => transcript.historyItems.findIndex(m => m.role === 'user'),
    [transcript.historyItems]
  )

  return (
    <>
      <ScrollBox
        flexDirection="column"
        flexGrow={1}
        flexShrink={1}
        onClick={(e: { cellIsBlank?: boolean }) => {
          if (e.cellIsBlank) {
            actions.clearSelection()
          }
        }}
        ref={transcript.scrollRef}
        stickyScroll
      >
        <Box flexDirection="column" paddingX={1}>
          {transcript.virtualHistory.topSpacer > 0 ? <Box height={transcript.virtualHistory.topSpacer} /> : null}

          {transcript.virtualRows.slice(transcript.virtualHistory.start, transcript.virtualHistory.end).map(row => (
            <Box flexDirection="column" key={row.key} ref={transcript.virtualHistory.measureRef(row.key)}>
              {row.msg.role === 'user' && firstUserIdx >= 0 && row.index > firstUserIdx && (
                <Box marginTop={1}>
                  <Text color={ui.theme.color.border}>───</Text>
                </Box>
              )}

              {row.msg.kind === 'intro' ? (
                <Box flexDirection="column" paddingTop={1}>
                  <Banner logoPalette={ui.logoPalette} maxWidth={Math.max(1, composer.cols - 2)} t={ui.theme} />

                  {row.msg.info && (
                    <SessionPanel
                      info={row.msg.info}
                      logoPalette={ui.logoPalette}
                      maxWidth={Math.max(1, composer.cols - 2)}
                      sid={ui.sid}
                      t={ui.theme}
                    />
                  )}
                </Box>
              ) : row.msg.kind === 'panel' && row.msg.panelData ? (
                <Panel sections={row.msg.panelData.sections} t={ui.theme} title={row.msg.panelData.title} />
              ) : (
                <MessageLine
                  cols={composer.cols}
                  compact={ui.compact}
                  detailsMode={ui.detailsMode}
                  detailsModeCommandOverride={ui.detailsModeCommandOverride}
                  msg={row.msg}
                  prev={prevRenderedMsg(i => transcript.virtualRows[i]?.msg, row.index, {
                    commandOverride: ui.detailsModeCommandOverride,
                    detailsMode: ui.detailsMode,
                    sections: ui.sections
                  })}
                  sections={ui.sections}
                  t={ui.theme}
                />
              )}
            </Box>
          ))}

          {transcript.virtualHistory.bottomSpacer > 0 ? <Box height={transcript.virtualHistory.bottomSpacer} /> : null}

          <StreamingAssistant
            cols={composer.cols}
            compact={ui.compact}
            detailsMode={ui.detailsMode}
            detailsModeCommandOverride={ui.detailsModeCommandOverride}
            prevMsg={transcript.historyItems[transcript.historyItems.length - 1]}
            progress={progress}
            sections={ui.sections}
          />
        </Box>
      </ScrollBox>

      <NoSelect flexShrink={0} marginLeft={1}>
        <TranscriptScrollbar scrollRef={transcript.scrollRef} t={ui.theme} />
      </NoSelect>

      <StickyPromptTracker
        messages={transcript.historyItems}
        offsets={transcript.virtualHistory.offsets}
        onChange={actions.setStickyPrompt}
        scrollRef={transcript.scrollRef}
      />
    </>
  )
})

const ComposerPane = memo(function ComposerPane({
  actions,
  composer,
  status
}: Pick<AppLayoutProps, 'actions' | 'composer' | 'status'>) {
  const ui = useStore($uiState)
  const isBlocked = useStore($isBlocked)
  const sh = composer.input.startsWith('!')

  // Bare glyph — the provider already reads out on the stats line below.
  const promptText = composerPromptText(ui.theme.brand.prompt, sh, TERMUX_TUI_MODE)

  const promptWidth = composerPromptWidth(promptText)
  const inputColumns = stableComposerColumns(composer.cols, promptWidth, TERMUX_TUI_MODE)
  const inputHeight = inputVisualHeight(composer.input, inputColumns)
  const inputMouseRef = useRef<null | TextInputMouseApi>(null)

  const captureInputDrag = (e: GutterMouseEvent) => {
    if (e.button !== 0) {
      return
    }

    e.stopImmediatePropagation?.()
    inputMouseRef.current?.startAtBeginning()
  }

  // Drag origin matches the input box's top-left, so localRow / localCol
  // map directly into TextInput coords (after backing out the prompt cell).
  const dragFromPromptRow = (e: GutterMouseEvent) => {
    if (e.button !== 0) {
      return
    }

    e.stopImmediatePropagation?.()
    inputMouseRef.current?.dragAt(e.localRow ?? 0, (e.localCol ?? 0) - promptWidth)
  }

  // Spacer rows live on a different vertical origin; only the column is
  // parent-aligned with the input. Force row=0 so vertical drags can't
  // jump the cursor to the wrong wrapped line.
  const dragFromSpacer = (e: GutterMouseEvent) => {
    if (e.button !== 0) {
      return
    }

    e.stopImmediatePropagation?.()
    inputMouseRef.current?.dragAt(0, (e.localCol ?? 0) - promptWidth)
  }

  const endInputDrag = () => inputMouseRef.current?.end()

  return (
    <NoSelect
      flexDirection="column"
      flexShrink={0}
      fromLeftEdge
      onClick={(e: { cellIsBlank?: boolean }) => {
        if (e.cellIsBlank) {
          actions.clearSelection()
        }
      }}
      paddingX={1}
    >
      <QueuedMessages
        cols={composer.cols}
        queued={composer.queuedDisplay}
        queueEditIdx={composer.queueEditIdx}
        t={ui.theme}
      />

      {ui.bgTasks.size > 0 && (
        <Text color={ui.theme.color.muted}>
          {ui.bgTasks.size} background {ui.bgTasks.size === 1 ? 'task' : 'tasks'} running
        </Text>
      )}

      {status.showStickyPrompt ? (
        <Text color={ui.theme.color.muted} wrap="truncate-end">
          <Text color={ui.theme.color.label}>↳ </Text>

          {status.stickyPrompt}
        </Text>
      ) : (
        <Box height={1} onMouseDown={captureInputDrag} onMouseDrag={dragFromSpacer} onMouseUp={endInputDrag} />
      )}

      <StatusRulePane at="top" composer={composer} status={status} />

      {ui.statusBar === 'off' && (
        <>
          <IdleChromeNotes t={ui.theme} />
          <BusyLine t={ui.theme} turnStartedAt={status.turnStartedAt} />
        </>
      )}

      <LiveTodoPanel />

      <GoalIndicator t={ui.theme} />

      <Box
        borderBottom
        borderColor={sh ? ui.theme.color.bashBorder : ui.theme.color.promptBorder}
        borderLeft={false}
        borderRight={false}
        borderStyle="round"
        borderTop
        flexDirection="column"
        marginTop={ui.statusBar === 'top' ? 0 : 1}
        position="relative"
      >
        <FloatingOverlays
          cols={composer.cols}
          compIdx={composer.compIdx}
          completions={composer.completions}
          onActiveSessionClose={actions.closeLiveSession}
          onActiveSessionSelect={actions.activateLiveSession}
          onLogoSelect={actions.onLogoSelect}
          onModelSelect={actions.onModelSelect}
          onNewLiveSession={actions.newLiveSession}
          onNewPromptSession={actions.newPromptSession}
          onResumeSelect={actions.resumeById}
          pagerPageSize={composer.pagerPageSize}
        />

        {composer.input === '?' && <HelpHint t={ui.theme} />}

        {!isBlocked && (
          <>
            <Box
              onMouseDown={captureInputDrag}
              onMouseDrag={dragFromPromptRow}
              onMouseUp={endInputDrag}
              position="relative"
              width={Math.max(1, composer.cols - 2)}
            >
              <Box width={promptWidth}>
                {sh ? (
                  <PromptPrefix color={ui.theme.color.shellDollar} promptText={promptText} width={promptWidth} />
                ) : (
                  <PromptPrefix
                    bold
                    color={ui.busy ? ui.theme.color.muted : ui.theme.color.prompt}
                    promptText={promptText}
                    width={promptWidth}
                  />
                )}
              </Box>

              <Box flexGrow={0} flexShrink={0} height={inputHeight} width={inputColumns}>
                {/* Reserve the transcript scrollbar gutter too so typing never rewraps when the scrollbar column repaints. */}
                <TextInput
                  argumentHint={composer.argumentHint}
                  columns={inputColumns}
                  mouseApiRef={inputMouseRef}
                  multiline
                  onChange={composer.updateInput}
                  onPaste={composer.handleTextPaste}
                  onSubmit={composer.submit}
                  placeholder={composer.empty && !ui.busy ? PLACEHOLDER : ''}
                  value={composer.input}
                  voiceRecordKey={composer.voiceRecordKey}
                />
              </Box>

              <Box position="absolute" right={0}>
                <GoodVibesHeart t={ui.theme} tick={status.goodVibesTick} />
              </Box>
            </Box>
          </>
        )}
      </Box>

      {!composer.empty && !ui.sid && <Text color={ui.theme.color.muted}>⚕ {ui.status}</Text>}

      <ComposerFooter
        busy={ui.busy}
        inputEmpty={!composer.input}
        mode={ui.permissionMode}
        sh={sh}
        t={ui.theme}
        voiceLabel={status.voiceLabel}
      />

      <StatusRulePane at="bottom" composer={composer} status={status} />

      <SessionStatsLine cols={composer.cols} />
    </NoSelect>
  )
})

// Idle-only signals that previously lived on the StatusRule: a live credits/
// status notice, and the parked-background reassurance while a top-level
// delegate keeps running after the turn ended. Rendered only when the bar is
// off (its 'top'/'bottom' modes still own them).
const IdleChromeNotes = memo(function IdleChromeNotes({ t }: { t: Theme }) {
  const ui = useStore($uiState)

  if (ui.busy) {
    return null
  }

  // usage.active_subagents persists past turn end (turnState.subagents is
  // cleared by idle()) — it's the same source the StatusRule used.
  const running = typeof ui.usage?.active_subagents === 'number' ? ui.usage.active_subagents : 0
  const notice = ui.notice?.text

  if (!notice && running === 0) {
    return null
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      {notice ? (
        <Text color={t.color.muted} wrap="truncate-end">
          {notice}
        </Text>
      ) : null}
      {running > 0 ? (
        <Text color={t.color.muted} dim>
          {running === 1 ? '↩ resumes when subagent finishes' : `↩ resumes when ${running} subagents finish`}
        </Text>
      ) : null}
    </Box>
  )
})

const AgentsOverlayPane = memo(function AgentsOverlayPane() {
  const { gw } = useGateway()
  const ui = useStore($uiState)
  const overlay = useStore($overlayState)

  return (
    <AgentsOverlay
      gw={gw}
      initialHistoryIndex={overlay.agentsInitialHistoryIndex}
      onClose={() => patchOverlayState({ agents: false, agentsInitialHistoryIndex: 0 })}
      t={ui.theme}
    />
  )
})

const StatusRulePane = memo(function StatusRulePane({
  at,
  composer,
  status
}: Pick<AppLayoutProps, 'composer' | 'status'> & { at: 'bottom' | 'top' }) {
  const ui = useStore($uiState)

  if (ui.statusBar !== at) {
    return null
  }

  return (
    <Box marginTop={at === 'top' ? 1 : 0}>
      <StatusRule
        bgCount={ui.bgTasks.size}
        busy={ui.busy}
        cols={composer.cols}
        cwdLabel={status.cwdLabel}
        indicatorStyle={ui.indicatorStyle}
        lastTurnEndedAt={status.lastTurnEndedAt}
        liveSessionCount={ui.liveSessionCount}
        model={ui.info?.model ?? ''}
        modelFast={ui.info?.fast || ui.info?.service_tier === 'priority'}
        modelReasoningEffort={ui.info?.reasoning_effort}
        notice={ui.notice}
        onSessionCountClick={() => patchOverlayState({ sessions: true })}
        sessionStartedAt={status.sessionStartedAt}
        status={ui.status}
        statusColor={status.statusColor}
        t={ui.theme}
        turnStartedAt={status.turnStartedAt}
        usage={ui.usage}
        voiceLabel={status.voiceLabel}
      />
    </Box>
  )
})

export const AppLayout = memo(function AppLayout({
  actions,
  composer,
  mouseTracking,
  progress,
  status,
  transcript
}: AppLayoutProps) {
  const overlay = useStore($overlayState)
  const ui = useStore($uiState)

  // Inline mode skips AlternateScreen so the host terminal's native
  // scrollback captures rows scrolled off the top; composer + progress
  // stay anchored via normal flex-column flow.
  const Shell = INLINE_MODE ? Fragment : AlternateScreen
  const shellProps = INLINE_MODE ? {} : { mouseTracking }

  return (
    <Shell {...shellProps}>
      <Box flexDirection="column" flexGrow={1}>
        <Box flexDirection="row" flexGrow={1}>
          {overlay.agents ? (
            <PerfPane id="agents">
              <AgentsOverlayPane />
            </PerfPane>
          ) : (
            <PerfPane id="transcript">
              <TranscriptPane actions={actions} composer={composer} progress={progress} transcript={transcript} />
            </PerfPane>
          )}
        </Box>

        {!overlay.agents && (
          <>
            <PetPane />

            <PerfPane id="prompt">
              <PromptZone
                cols={composer.cols}
                onApprovalChoice={actions.answerApproval}
                onClarifyAnswer={actions.answerClarify}
                onPlanApprovalChoice={actions.answerPlanApproval}
                onSecretSubmit={actions.answerSecret}
                onSudoSubmit={actions.answerSudo}
              />
            </PerfPane>

            <PerfPane id="composer">
              <ComposerPane actions={actions} composer={composer} status={status} />
            </PerfPane>

            {SHOW_FPS && (
              <Box flexShrink={0} justifyContent="flex-end" paddingRight={1}>
                <FpsOverlay t={ui.theme} />
              </Box>
            )}
          </>
        )}
      </Box>
    </Shell>
  )
})

type GutterMouseEvent = {
  button: number
  localCol?: number
  localRow?: number
  stopImmediatePropagation?: () => void
}
