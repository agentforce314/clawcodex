import { useStore } from '@nanostores/react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { ProjectNode, SessionRow } from '../gateway/protocol.ts'
import { resumeSession } from '../state/actions.ts'
import { toggleSidebar } from '../state/layout.ts'
import { BrandMark } from '../ui/BrandMark.tsx'
import {
  $connection,
  $projects,
  $sessionId,
  $settingsTab,
  $storedSessionId,
  $workspace,
} from '../state/store.ts'
import { $themePreference, cycleTheme } from '../state/theme.ts'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  GitBranchIcon,
  MonitorIcon,
  MoonIcon,
  PanelLeftIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  SunIcon,
  XIcon,
} from '../ui/icons.tsx'
import { sessionWatermark, SESSION_PAGE } from './watermark.ts'
import { CopyButton } from '../ui/primitives/CopyButton.tsx'
import { filterProjects, isBlankSession, visibleSessions } from './filter.ts'
import { openNewSessionDialog } from './NewSessionDialog.tsx'
import { absoluteTime, relativeTime } from './recency.ts'
import css from './Sidebar.module.css'

export interface SidebarProps {
  collapsed: boolean
}

function sessionLabel(row: SessionRow): string {
  const title = row.title ?? ''

  if (title.trim() !== '') return title

  const preview = row.preview ?? ''

  // A blank row is the one on screen (every other blank one is hidden), and
  // what is on screen is a new session — say so, not "untitled", which reads
  // as a conversation that lost its name.
  return preview.trim() === '' ? 'New session' : preview.slice(0, 80)
}

function laneCount(project: ProjectNode): number {
  return project.repos.reduce((total, repo) => total + repo.groups.length, 0)
}

/** The rows a lane shows: its sessions minus the hidden twin and idle blanks. */
function laneRows(
  sessions: readonly SessionRow[],
  hiddenId: string | null,
  keep: ReadonlySet<string>,
): SessionRow[] {
  return visibleSessions(
    sessions.filter(row => row.id !== hiddenId),
    keep,
  )
}

/** How many rows the project will actually show, for its header count. */
function visibleCount(project: ProjectNode, hiddenId: string | null, keep: ReadonlySet<string>): number {
  return project.repos.reduce(
    (total, repo) =>
      total + repo.groups.reduce((sum, lane) => sum + laneRows(lane.sessions, hiddenId, keep).length, 0),
    0,
  )
}

interface SessionListProps {
  activeId: string | null
  /** Runtime session id to suppress: a replay's fresh session duplicating a row. */
  hiddenId: string | null
  /** Blank sessions still worth a row: the one on screen. */
  keep: ReadonlySet<string>
  liveId: string | null
  project: ProjectNode
  searching: boolean
}

function SessionActions({ row }: { row: SessionRow }) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ top: 0, left: 0 })
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (!open) return
    const rect = trigger.current?.getBoundingClientRect()
    const panel = menu.current
    if (rect && panel) setPosition({
      left: Math.max(8, Math.min(rect.right - panel.offsetWidth, window.innerWidth - panel.offsetWidth - 8)),
      top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - panel.offsetHeight - 8)),
    })
    panel?.querySelector('button')?.focus()
  }, [open])
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); trigger.current?.focus() }
    }
    const close = () => setOpen(false)
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [open])
  return <>
    <button className={css.sessionActions} ref={trigger} type="button" aria-label={`Session actions: ${sessionLabel(row)}`}
      aria-haspopup="dialog" aria-expanded={open} title="Session actions" onClick={() => setOpen(value => !value)}>⋯</button>
    {open && createPortal(<div className={css.sessionMenu} ref={menu} role="dialog" aria-label="Session details" style={position}>
      <span>Session ID</span><code>{row.id}</code><CopyButton text={row.id} label="Copy session ID" />
    </div>, document.body)}
  </>
}

function SessionList({ activeId, hiddenId, keep, liveId, project, searching }: SessionListProps) {
  // Lane headers only earn their line when a repo actually has more than one
  // checkout — a single-worktree project would otherwise show a header that
  // repeats its own name.
  const showLanes = laneCount(project) > 1
  const [marks, setMarks] = useState<Record<string, number>>({})
  const required = Object.fromEntries(project.repos.flatMap(repo => repo.groups.map(lane =>
    [lane.id, sessionWatermark(laneRows(lane.sessions, hiddenId, keep), marks[lane.id] ?? SESSION_PAGE, activeId ?? liveId)])))
  useEffect(() => {
    if (searching) return
    if (Object.entries(required).some(([id, value]) => value > (marks[id] ?? SESSION_PAGE))) setMarks(required)
  }, [required, marks, searching])

  return (
    <>
      {project.repos.map(repo =>
        repo.groups.map(lane => {
          const rows = laneRows(lane.sessions, hiddenId, keep)
          const visible = searching ? rows.length : required[lane.id] ?? SESSION_PAGE
          return <div key={lane.id}>
            {showLanes && (
              <div className={css.laneLabel}>
                <GitBranchIcon size={11} />
                {lane.label}
              </div>
            )}
            {rows.slice(0, visible).map(row => (
              <div className={css.sessionEntry} key={row.id}><button
                className={[css.sessionRow, row.id === activeId ? css.sessionActive : '']
                  .filter(Boolean)
                  .join(' ')}
                key={row.id}
                onClick={() => {
                  void resumeSession(row.id, lane.path ?? row.cwd ?? undefined)
                }}
                title={sessionLabel(row)}
                type="button"
              >
                {row.id === liveId && <span className={css.liveDot} />}
                <span className={css.sessionTitle}>{sessionLabel(row)}</span>
                {/* A blank row has no last activity worth a stamp. */}
                {!isBlankSession(row) && (
                  <span className={css.sessionMeta} title={absoluteTime(row.last_active)}>
                    {relativeTime(row.last_active)}
                  </span>
                )}
              </button>
              <SessionActions row={row} /></div>
            ))}
            {!searching && rows.length > visible && <button className={css.showMore} type="button"
              onClick={() => setMarks(current => ({ ...current, [lane.id]: visible + SESSION_PAGE }))}>
              Show more ({rows.length - visible})
            </button>}
            {!searching && visible > sessionWatermark(rows, SESSION_PAGE, activeId ?? liveId) && <button className={css.showMore} type="button"
              onClick={() => setMarks(current => ({ ...current, [lane.id]: SESSION_PAGE }))}>Show less</button>}
          </div>
        }),
      )}
    </>
  )
}

/**
 * The session tree.
 *
 * Sessions are grouped by workspace directory. Git repositories retain a lane
 * per worktree; non-Git directories use their basename as the project label.
 * The backend computes the shared Web/Desktop grouping. Collapsed, the column
 * becomes a rail of the three controls that still make sense without labels.
 */
export function Sidebar({ collapsed }: SidebarProps) {
  const projects = useStore($projects)
  const workspace = useStore($workspace)
  const liveId = useStore($sessionId)
  const activeId = useStore($storedSessionId)
  const connection = useStore($connection)
  const themePreference = useStore($themePreference)

  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({})
  const [query, setQuery] = useState('')

  const currentId = activeId ?? liveId
  const currentProjectId = useMemo(
    () => projects.find(project =>
      currentId === null
        ? project.path === workspace || project.repos.some(repo =>
            repo.groups.some(lane => lane.path === workspace),
          )
        : project.repos.some(repo =>
            repo.groups.some(lane => lane.sessions.some(row => row.id === currentId)),
          ),
    )?.id,
    [currentId, projects, workspace],
  )

  // Like DeepSeek's workspace browser, initialize only the current session's
  // group and preserve explicit toggles. The launch workspace stays a derived
  // default so restoring a session elsewhere does not leave both groups open.
  useEffect(() => {
    if (currentId === null || currentProjectId === undefined) return

    setCollapsedProjects(current => Object.hasOwn(current, currentProjectId)
      ? current
      : { ...current, [currentProjectId]: false },
    )
  }, [currentId, currentProjectId])

  const searching = query.trim() !== ''

  const ordered = useMemo(
    () =>
      filterProjects(projects, query).sort((a, b) => (b.lastActive ?? 0) - (a.lastActive ?? 0)),
    [projects, query],
  )

  // Resuming spawns a FRESH runtime session that replays a stored one, so the
  // tree lists both: the row the user clicked and an empty "Untitled session"
  // for the runtime. They are one conversation — show the row that has the
  // history, and suppress its runtime twin.
  const duplicateLiveId = liveId !== null && liveId !== activeId ? liveId : null

  // Blank sessions are listed only while they are the one on screen; the
  // backend keeps every runtime session it ever spawned, and a row per
  // abandoned "New session" press is a column of nothing.
  const keep = useMemo(() => {
    const ids = new Set<string>()

    if (liveId !== null) ids.add(liveId)
    if (activeId !== null) ids.add(activeId)

    return ids
  }, [activeId, liveId])

  const ThemeIcon =
    themePreference === 'light' ? SunIcon : themePreference === 'dark' ? MoonIcon : MonitorIcon

  const connectionClass =
    connection === 'open'
      ? css.connectionOk
      : connection === 'error'
        ? css.connectionError
        : connection === 'reconnecting' || connection === 'connecting'
          ? css.connectionWarn
          : ''

  return (
    <div className={[css.root, collapsed ? css.collapsed : ''].filter(Boolean).join(' ')}>
      <div className={css.logoRow}>
        {!collapsed && (
          <button
            className={css.brand}
            onClick={openNewSessionDialog}
            title="New session"
            type="button"
          >
            <BrandMark size={22} />
            <span className={css.brandLabel}>ClawCodex</span>
          </button>
        )}
        <button
          className={css.iconButton}
          onClick={toggleSidebar}
          title={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          type="button"
        >
          <PanelLeftIcon size={collapsed ? 18 : 16} />
        </button>
      </div>

      <button
        className={css.newSession}
        onClick={openNewSessionDialog}
        title="New session"
        type="button"
      >
        <PlusIcon size={16} />
        <span className={css.newSessionLabel}>New session</span>
      </button>

      {!collapsed && (
        <div className={css.search}>
          <SearchIcon size={13} />
          <input
            aria-label="Filter sessions"
            className={css.searchInput}
            onChange={event => {
              setQuery(event.target.value)
            }}
            onKeyDown={event => {
              if (event.key === 'Escape') setQuery('')
            }}
            placeholder="Filter sessions"
            value={query}
          />
          {searching && (
            <button
              aria-label="Clear the filter"
              className={css.searchClear}
              onClick={() => {
                setQuery('')
              }}
              type="button"
            >
              <XIcon size={12} />
            </button>
          )}
        </div>
      )}

      <div className={css.region}>
        {!collapsed &&
          (ordered.length === 0 ? (
            <div className={css.empty}>
              {searching ? `No sessions match “${query.trim()}”.` : 'No sessions yet. Start one above.'}
            </div>
          ) : (
            ordered.map(project => {
              // While searching, every surviving project is open: a project
              // that kept a match is exactly the one the user is looking in,
              // and leaving it collapsed hides the result they searched for.
              const isCollapsed = !searching &&
                (collapsedProjects[project.id] ?? project.id !== currentProjectId)
              const count = visibleCount(project, duplicateLiveId, keep)

              // A project whose every session is an idle blank has nothing
              // to open; the header would announce a count over no rows.
              if (count === 0) return null

              return (
                <div className={css.project} key={project.id}>
                  <button
                    aria-expanded={!isCollapsed}
                    className={css.projectHeader}
                    onClick={() => {
                      setCollapsedProjects(current => ({
                        ...current,
                        [project.id]: !isCollapsed,
                      }))
                    }}
                    title={project.path ?? project.label}
                    type="button"
                  >
                    {isCollapsed ? <ChevronRightIcon size={12} /> : <ChevronDownIcon size={12} />}
                    <span className={css.projectLabel}>{project.label}</span>
                    <span className={css.projectCount}>{count}</span>
                  </button>
                  {!isCollapsed && (
                    <SessionList
                      activeId={activeId}
                      hiddenId={duplicateLiveId}
                      keep={keep}
                      liveId={liveId}
                      project={project}
                      searching={searching}
                    />
                  )}
                </div>
              )
            })
          ))}
      </div>

      <div className={css.foot}>
        <button
          className={css.iconButton}
          onClick={cycleTheme}
          title={`Appearance: ${themePreference}`}
          type="button"
        >
          <ThemeIcon size={16} />
        </button>
        <button
          aria-label="Settings"
          className={css.iconButton}
          onClick={() => {
            $settingsTab.set('general')
          }}
          title="Settings"
          type="button"
        >
          <SettingsIcon size={16} />
        </button>
        {!collapsed && (
          <>
            <span className={css.footSpacer} />
            <span className={[css.connection, connectionClass].filter(Boolean).join(' ')}>
              <span className={css.connectionDot} />
              {connection === 'open' ? 'Connected' : connection}
            </span>
          </>
        )}
      </div>
    </div>
  )
}
